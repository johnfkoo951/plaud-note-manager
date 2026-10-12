"""Send a recording's generated content into an Obsidian vault.

Three delivery modes, ordered by how much AI is involved:

  direct         No model call. The already-generated artifact (integrated
                 summary first) is wrapped in CMDS frontmatter and written
                 straight into the vault. Instant, deterministic, offline.
  claude         Headless one-shot: `claude -p` (or codex/gemini/grok via the
                 same run_model backends) reformats the content into a CMDS
                 note; *this code* decides the destination path and writes the
                 file, so no agent file-write permissions are needed.
  claude-window  Interactive: a Terminal window running Claude Code files the
                 note itself using the user's vault skills (obsidian-markdown,
                 cmds-format). Handled in the CLI layer via the same prompt
                 builder here.

Content resolution is integrated-first: the user primarily works with the
fused Plaud+CMDS output, so `auto` means integrated -> slot summary -> Plaud's
own summary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import app_config
from .metadata import (
    as_list,
    build_recording_snapshot,
    first_nonempty,
    format_cmds_transcript,
    format_plaud_transcript,
    human_speakers,
    looks_like_meeting,
    recorded_date,
    safe_filename,
    strip_frontmatter,
    strip_markdown_fence,
)
from .integrate import NO_TRANSCRIPT
from .paths import integrated_dir, summaries_dir, summary_path
from .paths import _safe as safe_component
from .storage import Storage
from .tags import normalize_tags

MEETINGS_DEST = "60. Collections/63. Meetings"
DEFAULT_DEST = "00. Inbox"
#: The wiki's folder name: the sibling looked for next to the main vault, and
#: how a path is recognised as the wiki when none is configured.
WIKI_VAULT_DIRNAME = "CMDS_LLM_Wiki"
WIKI_KEYWORD = "wiki"
WIKI_SEND_REFUSAL = (
    "the wiki takes raw sources only — use "
    "`uv run plaud vault-compose <id> --to wiki --template <note-...> "
    '--collection-purpose "..."`'
)


@dataclass(frozen=True, slots=True)
class ResolvedContent:
    kind: str  # integrated | summary | plaud | transcript
    label: str  # artifact label, e.g. "claude-fable-5__integrated"
    summary: str
    transcript: str = ""
    source_path: str = ""


#: What a send does when this app already wrote a note for the recording in
#: the vault: ask (report it, write nothing) | skip | overwrite | new.
ON_DUPLICATE = ("ask", "skip", "overwrite", "new")

#: The note-reference kind of a wiki raw source, which identifies itself by
#: its `source:` link rather than `plaud_id:`.
WIKI_RAW_SOURCE_KIND = "wiki-raw-source"
#: The Plaud link a raw source carries as `source`; the rest is the recording id.
PLAUD_FILE_URL = "https://web.plaud.ai/file/"


@dataclass(slots=True)
class VaultSendResult:
    # ok | duplicate | no_content | no_vault | write_failed | model_failed | wiki_unsupported
    status: str
    detail: str = ""
    path: str = ""
    vault: str = ""
    dest: str = ""
    content: str = ""
    via: str = ""
    obsidian_url: str = ""
    tags: list[str] = field(default_factory=list)
    action: str = ""  # created | overwritten | skipped (on ok)
    # Notes already in the vault for this recording, newest first:
    # [{"path", "updated_at", "modified_after_send", "obsidian_url"}]
    existing: list[dict] = field(default_factory=list)


def validate_on_duplicate(value: str) -> str:
    if value not in ON_DUPLICATE:
        raise ValueError(f"on_duplicate must be one of: {', '.join(ON_DUPLICATE)} (got {value!r})")
    return value


def wiki_vault() -> Path | None:
    """Wiki (LLM satellite) vault: env > config > sibling of the main vault."""
    import os

    raw = os.environ.get("PLAUD_WIKI_VAULT") or app_config.load().get("wiki_vault", "")
    if raw:
        return Path(os.path.expanduser(raw)).resolve()
    main = app_config.obsidian_vault()
    if main:
        sibling = main.parent / WIKI_VAULT_DIRNAME
        if sibling.is_dir():
            return sibling
    return None


def set_wiki_vault(path: str) -> None:
    cfg = app_config.load()
    cfg["wiki_vault"] = path
    app_config.save(cfg)


def _is_wiki_keyword(target: str) -> bool:
    return (target or "").strip().lower() == WIKI_KEYWORD


def resolve_vault(target: str) -> Path | None:
    """Map a --to value onto a vault root. main|wiki|<absolute path>."""
    target = (target or "main").strip()
    if target in ("", "main"):
        return app_config.obsidian_vault()
    if _is_wiki_keyword(target):
        return wiki_vault()
    import os

    path = Path(os.path.expanduser(target))
    return path if path.is_dir() else None


def is_wiki_target(to: str, vault: Path | None = None) -> bool:
    """True for the word `wiki`, or a resolved `vault` that is the wiki or a
    folder inside it. With no wiki configured, a path is the wiki when one of
    its components is WIKI_VAULT_DIRNAME."""
    if _is_wiki_keyword(to):
        return True
    if vault is None:
        return False
    target = vault.resolve()
    wiki = wiki_vault()
    if wiki is not None:
        return target.is_relative_to(wiki.resolve())
    return WIKI_VAULT_DIRNAME in target.parts


def send_destination(vault: Path, dest: str = "") -> Path:
    """The folder a send writes into: `dest` (default 00. Inbox) under `vault`.
    An absolute `dest` replaces the root, as Path joining does."""
    return vault / (dest.strip() or DEFAULT_DEST)


def sends_into_wiki(to: str, vault: Path | None, dest: str = "") -> bool:
    """Whether a send would land in the wiki — by its target, or by the folder
    it actually writes into: an absolute `dest`, a `..` walk or a symlink can
    all reach the wiki from the main vault. Resolving the folder before
    checking it covers all three."""
    if is_wiki_target(to):
        return True
    if vault is None:
        return False
    return is_wiki_target(to, vault) or is_wiki_target(to, send_destination(vault, dest))


def _latest(paths: list[Path]) -> Path | None:
    existing = [p for p in paths if p.exists()]
    return max(existing, key=lambda p: p.stat().st_mtime) if existing else None


def _read(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return strip_frontmatter(path.read_text(encoding="utf-8", errors="ignore")).strip()
    except OSError:
        return ""


def pick_integrated(file_id: str, *, model: str = "", template: str = "") -> ResolvedContent | None:
    """Newest integrated artifact, or the exact model+template one when given."""
    base = integrated_dir(file_id)
    model_part = safe_component(model) if model else "*"
    template_part = safe_component(template) if template else "*"
    candidates = list(base.glob(f"{model_part}__{template_part}.summary.md"))
    chosen = _latest(candidates)
    if chosen is None:
        return None
    summary = _read(chosen)
    if not summary:
        return None
    transcript_path = chosen.with_name(chosen.name.replace(".summary.md", ".transcript.md"))
    transcript = _read(transcript_path if transcript_path.exists() else None)
    if transcript == NO_TRANSCRIPT:
        # integrate()'s stand-in for a reply with no transcript section: no
        # transcript at all, so a raw source falls back to the CMDS / Plaud one.
        transcript = ""
    return ResolvedContent(
        kind="integrated",
        label=chosen.name.removesuffix(".summary.md"),
        summary=summary,
        transcript=transcript,
        source_path=str(chosen),
    )


def pick_summary(file_id: str, *, model: str = "", template: str = "") -> ResolvedContent | None:
    """Newest slot summary, or the exact model+template one when given."""
    if model and template:
        candidates = [summary_path(file_id, model=model, template=template)]
    else:
        base = summaries_dir(file_id)
        model_part = safe_component(model) if model else "*"
        template_part = safe_component(template) if template else "*"
        candidates = list(base.glob(f"{model_part}__{template_part}.md"))
    chosen = _latest(candidates)
    if chosen is None:
        return None
    body = _read(chosen)
    if not body:
        return None
    return ResolvedContent(
        kind="summary",
        label=chosen.stem,
        summary=body,
        source_path=str(chosen),
    )


def _pick_plaud(snapshot: dict) -> ResolvedContent | None:
    body = (snapshot.get("summaries") or "").strip()
    if not body:
        return None
    return ResolvedContent(kind="plaud", label="plaud-native", summary=body)


def _pick_transcript(storage: Storage, file_id: str, snapshot: dict) -> ResolvedContent | None:
    integrated = pick_integrated(file_id)
    if integrated and integrated.transcript:
        return ResolvedContent(
            kind="transcript",
            label=integrated.label,
            summary="",
            transcript=integrated.transcript,
            source_path=integrated.source_path,
        )
    text = first_nonempty(
        format_cmds_transcript(storage.get_cmds_transcript(file_id)),
        format_plaud_transcript(storage.get_content_row(file_id)),
        snapshot.get("transcript") or "",
    )
    if not text:
        return None
    return ResolvedContent(kind="transcript", label="raw-transcript", summary="", transcript=text)


def resolve_content(
    storage: Storage,
    file_id: str,
    snapshot: dict,
    *,
    content: str = "integrated",
    model: str = "",
    template: str = "",
) -> ResolvedContent | None:
    """Pick the note body. `integrated` (the default) falls back gracefully —
    the user's primary artifact is the fused output, but a recording that was
    never integrated should still be sendable."""
    content = (content or "integrated").strip().lower()
    if content in ("integrated", "auto"):
        return (
            pick_integrated(file_id, model=model, template=template)
            or pick_summary(file_id, model=model, template=template)
            or _pick_plaud(snapshot)
        )
    if content == "summary":
        return pick_summary(file_id, model=model, template=template) or _pick_plaud(snapshot)
    if content == "plaud":
        return _pick_plaud(snapshot)
    if content == "transcript":
        return _pick_transcript(storage, file_id, snapshot)
    return None


def _yaml_quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def note_tags(snapshot: dict, note_type: str) -> list[str]:
    seed = ["plaud", note_type]
    seed.extend((snapshot.get("keywords") or [])[:8])
    return normalize_tags(seed)[:12]


def note_context(storage: Storage, file_id: str, snapshot: dict) -> dict:
    """CMDS/index/keywords/related/speakers for a note, from stored metadata
    when present, else from the deterministic classifier.

    `speakers` are whole real names (`human_speakers`): never split on
    whitespace, no diarizer placeholders, NFC, deduplicated."""
    from .classification import classify_snapshot

    existing = storage.get_note_metadata(file_id)
    meta: dict = {}
    if existing and existing["metadata_json"]:
        try:
            import json as _json

            meta = _json.loads(existing["metadata_json"]) or {}
        except Exception:
            meta = {}
    cls = classify_snapshot(snapshot)
    cmds = str(meta.get("cmds") or cls.cmds or "")
    index = str(meta.get("index") or cls.index or "")
    keywords = [str(k) for k in (snapshot.get("keywords") or [])][:12]
    related = [str(r) for r in (meta.get("related") or [])]
    related_paths: list[dict[str, str]] = []
    try:
        from .vault_index import related_notes

        related_paths = related_notes([*keywords, *as_list(meta.get("key_topics")), *related])
        if not related:
            related = [n["title"] for n in related_paths]
    except Exception:
        related_paths = []
    speakers = human_speakers(snapshot.get("speakers") or "")
    if not speakers:
        try:
            from .dual_pipeline import cmds_speakers

            speakers = human_speakers(cmds_speakers(storage, file_id))
        except Exception:
            speakers = []
    aliases = [str(a) for a in as_list(meta.get("aliases"))][:3]
    return {
        "cmds": cmds,
        "index": index,
        "keywords": keywords,
        "related": related,
        "related_paths": related_paths,
        "speakers": speakers,
        "aliases": aliases,
        "tags": [str(t) for t in as_list(meta.get("tags"))],
    }


def _note_type(storage: Storage, file_id: str, snapshot: dict) -> str:
    existing = storage.get_note_metadata(file_id)
    if existing and existing["note_type"]:
        return str(existing["note_type"])
    probe = " ".join([snapshot.get("title") or "", (snapshot.get("summaries") or "")[:2000]])
    return "meeting" if looks_like_meeting(probe) else "note"


def build_note_markdown(
    *,
    file_id: str,
    title: str,
    date: str,
    note_type: str,
    tags: list[str],
    resolved: ResolvedContent,
    with_transcript: bool,
    author: str = "",
    now: datetime | None = None,
    reuse_channels: list[str] | None = None,
    cmds: str = "",
    index: str = "",
    speakers: list[str] | None = None,
    keywords: list[str] | None = None,
    related: list[str] | None = None,
    model: str = "",
    aliases: list[str] | None = None,
    date_created: str = "",
) -> str:
    """Deterministic CMDS-style note (direct mode). Frontmatter follows the
    vault's frontmatter-standard (7 required fields + CMDS/index + Plaud lane
    extras) via `core.frontmatter.CmdsFrontmatter`. `date_created` keeps an
    overwritten note's original creation time."""
    from .frontmatter import CmdsFrontmatter, iso_minute, model_label

    now = now or datetime.now()
    kind_label = f"{resolved.kind} ({resolved.label})" if resolved.label else resolved.kind
    fm = CmdsFrontmatter(
        type=note_type,
        aliases=list(aliases or []),
        description=f"Plaud recording note: {title}. Generated from the {resolved.kind} output.",
        author=author,
        model=model_label(model) if model else "",
        date_created=date_created or iso_minute(now),
        date_modified=iso_minute(now),
        date=date,
        tags=tags,
        cmds=cmds,
        index=index,
        status="inProgress",
        source="plaud",
        plaud_id=file_id,
        speakers=list(speakers or []),
        keywords=list(keywords or []),
        related=list(related or []),
        reuse_channels=list(reuse_channels or []),
        extra={"content_kind": kind_label},
    )
    lines = fm.lines()
    lines.append("")
    lines.append(f"> [!info] Plaud 녹음 · {date}")
    lines.append(f"> [web.plaud.ai](https://web.plaud.ai/file/{file_id}) · `{file_id}`")
    if speakers:
        lines.append(f"> 참석자: {' '.join(f'[[{s}]]' for s in speakers)}")
    lines.append("")
    if resolved.summary:
        lines.append(resolved.summary)
    if resolved.transcript and (with_transcript or not resolved.summary):
        if resolved.summary:
            lines.append("")
            lines.append("## Transcript")
            lines.append("")
        lines.append(resolved.transcript)
    if related:
        lines.append("")
        lines.append("## 관련 노트")
        lines.append("")
        lines.extend(f"- [[{r}]]" for r in related)
    lines.append("")
    return "\n".join(lines)


def build_format_prompt(
    *,
    file_id: str,
    title: str,
    date: str,
    note_type: str,
    tags: list[str],
    resolved: ResolvedContent,
    with_transcript: bool,
    author: str = "",
) -> str:
    """Prompt for the headless `--via claude` mode: the model returns ONLY the
    finished note markdown; placement stays deterministic on our side."""
    author_rule = (
        f'- author 필드: `author:` 리스트에 `"[[{author}]]"` 포함.'
        if author
        else "- author 필드는 생략."
    )
    transcript_block = (
        f"\n## 전사본 (노트 하단 `## Transcript` 섹션에 포함)\n{resolved.transcript[:40000]}\n"
        if with_transcript and resolved.transcript
        else ""
    )
    return f"""아래 Plaud 녹음 콘텐츠를 CMDS 옵시디언 볼트용 노트 1개로 정리해줘.

규칙:
- 최종 마크다운 노트만 출력. 코드펜스로 감싸지 마.
- YAML frontmatter 필수: description(영문, 큰따옴표 1-2문장), date created: {date}, type: {note_type}, status: inProgress, source: plaud, plaud_id: {file_id}, tags({", ".join(tags)} 기반으로 정제, # 없이).
{author_rule}
- 본문: 핵심 요약 → 주요 논점/인사이트(불릿) → 결정·액션 아이템(있으면) 순. 한국어 ~임/~함 또는 명사형 종결.
- 날짜/이름/숫자/결정 사항은 유실 없이 보존.
- 원문 요약이 이미 충분히 구조화돼 있으면 과도하게 재작성하지 말고 다듬는 수준으로.

## 제목
{title}

## 콘텐츠 ({resolved.kind}: {resolved.label})
{resolved.summary[:45000]}
{transcript_block}"""


def build_filing_prompt(
    *,
    vault: Path,
    dest: str,
    title: str,
    file_id: str,
    resolved: ResolvedContent,
    with_transcript: bool,
    filename: str = "",
    keep: list[str] | None = None,
) -> str:
    """Prompt for the interactive `--via claude-window` mode: Claude Code files
    the note itself with the user's vault skills. `filename` pins the exact
    (unused) name, so the note neither replaces another file nor escapes a
    later duplicate check; `keep` lists this recording's existing notes, which
    Claude must leave alone."""
    transcript_block = (
        f"\n## 전사본\n{resolved.transcript[:40000]}\n"
        if with_transcript and resolved.transcript
        else ""
    )
    name_rule = (
        f"정확히 `{filename}` (새 파일)" if filename else "`YYYYMMDD_제목.md` (한글 제목 OK)"
    )
    keep_rule = (
        "\n- 이 녹음의 기존 노트는 그대로 둔다 — 수정하거나 덮어쓰지 말 것: "
        + ", ".join(f"`{name}`" for name in keep)
        if keep
        else ""
    )
    return f"""아래 Plaud 녹음 콘텐츠를 옵시디언 볼트에 노트로 정리해서 저장해줘.

## 메타데이터
- file_id: {file_id}
- 제목: {title}
- 볼트 경로: {vault}
- 대상 폴더: {dest}

## 작업
1. obsidian-markdown / cmds-format 스킬 컨벤션으로 노트 1개를 만들어.
2. 파일명은 {name_rule}, 위 대상 폴더에 저장.{keep_rule}
3. frontmatter에 source: plaud, plaud_id, tags, date created 포함.
4. 아래 콘텐츠는 이미 통합·요약된 결과물이니 구조를 존중하며 다듬어.
5. 저장 후 파일 절대 경로를 마지막 줄에 출력하고 종료.

## 콘텐츠 ({resolved.kind}: {resolved.label})
{resolved.summary[:45000]}
{transcript_block}"""


def _ctx_kwargs(ctx: dict) -> dict:
    return {
        "cmds": ctx.get("cmds", ""),
        "index": ctx.get("index", ""),
        "speakers": ctx.get("speakers") or [],
        "keywords": ctx.get("keywords") or [],
        "related": ctx.get("related") or [],
        "aliases": ctx.get("aliases") or [],
    }


def note_stem(date_compact: str, title: str) -> str:
    """`YYYYMMDD_제목` — the filename a send gives a note before numbering."""
    return f"{date_compact}_{safe_filename(title) or 'plaud-note'}"


def unique_note_path(folder: Path, date_compact: str, title: str) -> Path:
    stem = note_stem(date_compact, title)
    path = folder / f"{stem}.md"
    counter = 2
    while path.exists():
        path = folder / f"{stem} ({counter}).md"
        counter += 1
    return path


# The identity check reads front matter by a strict line grammar — exactly
# the shapes this app writes. Every line of the block must be one of:
#   1. `key: value` or `key:` at column 0: the key is ASCII words (letters,
#      digits, `_`, `-`) joined by single spaces (`date created`), the value
#      a single-line plain scalar, a quoted scalar closed on the same line,
#      or the literal empty list `[]`;
#   2. `  - scalar`, exactly two spaces, under a `key:` with no value;
#   3. `#comment` at column 0;
#   4. blank.
# Before any of that, the block's bytes must decode as UTF-8 and hold no
# control character but LF — no CR (so no CRLF), no tab — and no other line
# break YAML may honour (NEL, U+2028, U+2029). Any other line, any such
# character, or an identity key (`plaud_id`, `source`) that appears twice
# means the note makes no ownership claim. A missed match only costs a
# numbered new note; a wrong one could overwrite another recording's note.

_KEY_LINE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*(?: [A-Za-z0-9_-]+)*):(?: (.*))?")
_ITEM_LINE = re.compile(r"  - (.*)")
_NOT_PLAIN_TEXT = re.compile("[\x00-\x09\x0b-\x1f\x7f\x85  ]")
IDENTITY_KEYS = ("plaud_id", "source")
# The value of a key that holds a list (block items or `[]`); equals no id.
_LIST = "\x00"
# The escapes a double-quoted scalar may use; any other means no claim.
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "t": "\t", "r": "\r", " ": " "}
# A plain value may not start with these: each opens another construct.
_INDICATORS = "[]{}&*!|>%@`,#'\""


class _Unsupported(ValueError):
    """Front matter outside the subset the identity check reads."""


def _read_quoted(text: str) -> tuple[str, str]:
    """The quoted scalar opening `text`, decoded, and the rest of the line.
    It must close on this line."""
    quote = text[0]
    out: list[str] = []
    j = 1
    while j < len(text):
        ch = text[j]
        if quote == '"' and ch == "\\":
            escaped = _ESCAPES.get(text[j + 1 : j + 2])
            if escaped is None:
                raise _Unsupported
            out.append(escaped)
            j += 2
        elif ch == quote and quote == "'" and text[j + 1 : j + 2] == "'":
            out.append("'")
            j += 2
        elif ch == quote:
            return "".join(out), text[j + 1 :]
        else:
            out.append(ch)
            j += 1
    raise _Unsupported  # it runs onto the next line, or never closes


def _read_scalar(text: str) -> str:
    """One single-line scalar, plain or quoted, with an optional ` #comment`,
    as YAML reads it. `text` holds no tab (see `_read_mapping`)."""
    text = text.lstrip(" ")
    if text[:1] in ('"', "'"):
        value, after = _read_quoted(text)
        rest = after.lstrip(" ")
        if rest and not (rest.startswith("#") and rest != after):
            raise _Unsupported  # only a blank-separated comment may follow
        return value
    cut = text.find(" #")
    value = (text[:cut] if cut != -1 else text).rstrip(" ")
    if not value or value[0] in _INDICATORS or value[:2].rstrip() in ("-", "?", ":"):
        raise _Unsupported
    if ": " in value or value.endswith(":"):
        raise _Unsupported  # a mapping, not a scalar
    return value


def _front_matter_fields(path: Path) -> dict[str, list[str]] | None:
    """The `key: value` pairs of the note's leading front matter, every value
    of a repeated key kept, `_LIST` for a list. None when there is no complete
    block at the very top of the file, or any line of it breaks the grammar
    described above."""
    try:
        with path.open("rb") as fh:
            head = fh.read(65536)
    except OSError:
        return None
    # Cut the block at the byte level, so nothing is decoded or normalised
    # before it is checked: the opening and closing lines are exactly `---`.
    raw_lines = head.split(b"\n")
    if raw_lines[0] != b"---" or b"---" not in raw_lines[1:]:
        return None  # no block, or none that ends within what was read
    end = raw_lines.index(b"---", 1)
    try:
        block = b"\n".join(raw_lines[1:end]).decode("utf-8")  # strict: no byte dropped
    except UnicodeDecodeError:
        return None
    if _NOT_PLAIN_TEXT.search(block):
        return None
    try:
        return _read_mapping(block.split("\n"))
    except _Unsupported:
        return None


def _read_mapping(lines: list[str]) -> dict[str, list[str]]:
    """`_front_matter_fields` over the lines between the `---` lines."""
    fields: dict[str, list[str]] = {}
    list_key: str | None = None  # a `key:` with no value, whose items may follow
    for line in lines:
        if not line.strip(" "):  # 4. blank
            continue
        if line.startswith("#"):  # 3. a comment at column 0
            continue
        item = _ITEM_LINE.fullmatch(line)
        if item:  # 2. `  - scalar` under a key with no value
            if list_key is None:
                raise _Unsupported
            _read_scalar(item.group(1))
            fields[list_key][-1] = _LIST
            continue
        pair = _KEY_LINE.fullmatch(line)
        if not pair:  # 1. `key: value` — or a line this grammar does not know
            raise _Unsupported
        key, value_text = pair.group(1), (pair.group(2) or "").strip(" ")
        if key in IDENTITY_KEYS and key in fields:
            raise _Unsupported  # an identity given twice is no identity
        list_key = None
        if not value_text:
            value, list_key = "", key  # null, or the head of a block list
        elif value_text == "[]":
            value = _LIST  # the empty list this app writes for no aliases/tags
        else:
            value = _read_scalar(value_text)
        fields.setdefault(key, []).append(value)
    return fields


def _plaud_url_id(values: list[str]) -> str:
    """The recording id of a single canonical `PLAUD_FILE_URL<id>` value."""
    if len(set(values)) != 1 or not values[0].startswith(PLAUD_FILE_URL):
        return ""
    rest = values[0][len(PLAUD_FILE_URL) :]
    return rest if rest and all(c.isalnum() or c in "-_" for c in rest) else ""


def _note_owner(path: Path, kind: str) -> str:
    """The recording a note says it belongs to, or "" unless it says so
    unambiguously in its own identity field: `plaud_id` on a main note, the
    canonical Plaud `source` link on a wiki raw source. Any `plaud_id` that
    names a different recording disqualifies the note, so a note for another
    recording is never claimed through a field that merely mentions this one."""
    fields = _front_matter_fields(path)
    if fields is None:
        return ""
    plaud_ids = set(fields.get("plaud_id", [])) - {""}
    if kind == WIKI_RAW_SOURCE_KIND:
        owner = _plaud_url_id(fields.get("source", []))
    else:
        owner = next(iter(plaud_ids)) if len(plaud_ids) == 1 else ""
    if not owner or owner == _LIST or plaud_ids - {owner}:
        return ""
    return owner


def existing_notes(
    storage: Storage,
    file_id: str,
    *,
    vault: Path,
    kind: str = "vault-send",
    same_name: Path | None = None,
) -> list[dict]:
    """Notes of `kind` this app already wrote for `file_id` that still exist
    inside `vault` (any folder), newest first. `same_name` adds a note at that
    path whose own identity is the recording even without a reference row —
    written on another machine, before references existed, or by Claude.

    Every candidate, a reference row's path included, must still name the
    recording by its own identity (`_note_owner`): a note moved away can leave
    its row pointing at a path another recording's same-named note now holds.
    """
    if not file_id:
        return []
    root = vault.resolve()
    seen: set[Path] = set()
    out: list[dict] = []
    for row in storage.list_note_references(file_id, kind=kind):
        path = Path(row["path"])
        resolved = path.resolve()
        if (
            resolved in seen
            or not path.is_file()
            or not resolved.is_relative_to(root)
            or _note_owner(path, kind) != file_id
        ):
            continue
        seen.add(resolved)
        updated_at = int(row["updated_at"] or 0)
        out.append(
            {
                "path": str(path),
                "updated_at": updated_at,
                # Slack for the gap between writing the file and recording it.
                "modified_after_send": path.stat().st_mtime > updated_at + 2,
                "obsidian_url": obsidian_open_url(vault, path),
            }
        )
    if (
        same_name is not None
        and same_name.is_file()
        and same_name.resolve() not in seen
        and same_name.resolve().is_relative_to(root)
        and _note_owner(same_name, kind) == file_id
    ):
        out.append(
            {
                "path": str(same_name),
                "updated_at": int(same_name.stat().st_mtime),
                "modified_after_send": False,
                "obsidian_url": obsidian_open_url(vault, same_name),
            }
        )
    return sorted(out, key=lambda e: e["updated_at"], reverse=True)


def send_duplicates(
    storage: Storage, file_id: str, *, vault: Path, dest: str, title: str, date: str
) -> list[dict]:
    """Earlier vault-send notes for this recording in `vault`, including a
    same-name note in the destination folder that names the recording.
    Wiki-owned notes never count — a wiki can sit inside the main vault, and
    its raw sources must not become a main send's overwrite target."""
    same_name = send_destination(vault, dest) / f"{note_stem(date.replace('-', ''), title)}.md"
    return [
        entry
        for entry in existing_notes(storage, file_id, vault=vault, same_name=same_name)
        if not is_wiki_target("", Path(entry["path"]))
    ]


def folder_in_vault(vault: Path, path: str) -> str:
    """`path`'s folder relative to `vault` (as `dest` is reported)."""
    parent = Path(path).resolve().parent
    try:
        return str(parent.relative_to(vault.resolve()))
    except ValueError:
        return str(parent)


def duplicate_detail(existing: list[dict]) -> str:
    return (
        f"already in the vault: {Path(existing[0]['path']).name} — "
        "choose --on-duplicate skip|overwrite|new"
    )


def early_duplicate_result(
    existing: list[dict], on_duplicate: str, *, vault: Path, content: str, via: str
) -> VaultSendResult | None:
    """The result for ask (`duplicate`) or skip (`ok`/skipped) when a note
    already exists; None when the send should go ahead."""
    if not existing or on_duplicate not in ("ask", "skip"):
        return None
    asked = on_duplicate == "ask"
    return VaultSendResult(
        "duplicate" if asked else "ok",
        duplicate_detail(existing) if asked else "already in the vault — skipped",
        path=existing[0]["path"],
        vault=str(vault),
        dest=folder_in_vault(vault, existing[0]["path"]),
        content=content,
        via=via,
        obsidian_url=existing[0]["obsidian_url"],
        action="" if asked else "skipped",
        existing=existing,
    )


def _existing_date_created(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    head = text.split("\n---", 1)[0] if text.startswith("---") else ""
    for line in head.splitlines():
        if line.startswith("date created:"):
            return line.split(":", 1)[1].strip()
    return ""


def obsidian_open_url(vault: Path, note_path: Path) -> str:
    from urllib.parse import quote

    try:
        rel = note_path.relative_to(vault)
    except ValueError:
        return f"obsidian://open?path={quote(str(note_path))}"
    return f"obsidian://open?vault={quote(vault.name)}&file={quote(str(rel.with_suffix('')))}"


def vault_send(
    file_id: str,
    *,
    to: str = "main",
    dest: str = "",
    content: str = "integrated",
    model: str = "",
    template: str = "",
    via: str = "direct",
    with_transcript: bool = False,
    ai_model: str = "claude",
    ai_model_id: str = "",
    storage: Storage | None = None,
    vault_override: Path | None = None,
    note_model: str = "",
    on_duplicate: str = "ask",
) -> VaultSendResult:
    """Resolve content, build the note (direct or headless-AI), write it into
    the vault, and record the reference. `claude-window` is NOT handled here —
    the CLI launches the Terminal itself using build_filing_prompt.

    The wiki is refused: it takes raw sources, which `vault-compose --to wiki`
    writes. Both checks — the target, then the folder the note would really be
    written into — run before the database is opened. `note_model` names the
    model that wrote the content, for the note's `model:`/`effort:`.

    When this app already sent a note for the recording to the vault,
    `on_duplicate` decides, before any model call: ask (status `duplicate`,
    nothing written), skip, overwrite (in place, wherever the note now is) or
    new (a numbered copy)."""
    validate_on_duplicate(on_duplicate)
    if is_wiki_target(to):
        return VaultSendResult("wiki_unsupported", WIKI_SEND_REFUSAL, via=via)
    vault = vault_override or resolve_vault(to)
    if vault is None or not vault.is_dir():
        return VaultSendResult(
            "no_vault",
            f"vault '{to}' not configured/found — set it via `plaud config-vault`",
            via=via,
        )
    if sends_into_wiki(to, vault, dest):
        return VaultSendResult("wiki_unsupported", WIKI_SEND_REFUSAL, vault=str(vault), via=via)
    storage = storage or Storage()

    snapshot = build_recording_snapshot(storage, file_id)
    resolved = resolve_content(
        storage, file_id, snapshot, content=content, model=model, template=template
    )
    if resolved is None:
        return VaultSendResult(
            "no_content",
            f"no '{content}' output found for {file_id} — generate a summary/"
            "integrated output first (Work Sidebar > Generate)",
            vault=str(vault),
            via=via,
        )

    title = snapshot["title"]
    date = recorded_date(snapshot)
    existing = send_duplicates(storage, file_id, vault=vault, dest=dest, title=title, date=date)
    early = early_duplicate_result(
        existing, on_duplicate, vault=vault, content=resolved.kind, via=via
    )
    if early is not None:
        return early
    overwrite = Path(existing[0]["path"]) if existing and on_duplicate == "overwrite" else None
    # The target replaces the folder checked above, so it is checked again —
    # before any model call or write.
    if overwrite is not None and is_wiki_target(to, overwrite):
        return VaultSendResult("wiki_unsupported", WIKI_SEND_REFUSAL, vault=str(vault), via=via)

    note_type = _note_type(storage, file_id, snapshot)
    tags = note_tags(snapshot, note_type)
    author = app_config.author()
    from .reuse import reuse_channels_for_frontmatter

    reuse_channels = reuse_channels_for_frontmatter(storage, file_id)
    ctx = note_context(storage, file_id, snapshot)
    if ctx["tags"]:
        tags = normalize_tags([*tags, *ctx["tags"]])[:20]

    if via == "claude":
        from .summarize import model_available, model_unavailable_message, run_model

        provider = ai_model or "claude"
        if not model_available(provider):
            return VaultSendResult(
                "model_failed",
                f"{provider} unavailable — {model_unavailable_message(provider)}",
                vault=str(vault),
                via=via,
            )
        prompt = build_format_prompt(
            file_id=file_id,
            title=title,
            date=date,
            note_type=note_type,
            tags=tags,
            resolved=resolved,
            with_transcript=with_transcript,
            author=author,
        )
        try:
            note_text = strip_markdown_fence(
                run_model(provider, prompt, model_id=ai_model_id or None, timeout=900)
            )
        except Exception as exc:  # model runners raise heterogeneous errors
            return VaultSendResult("model_failed", str(exc), vault=str(vault), via=via)
        if not note_text.strip():
            return VaultSendResult(
                "model_failed", f"{provider} returned empty output", vault=str(vault), via=via
            )
        if not note_text.startswith("---"):
            # Model skipped frontmatter — fall back to the deterministic wrapper
            # so the vault never receives a frontmatter-less note.
            note_text = build_note_markdown(
                file_id=file_id,
                title=title,
                date=date,
                note_type=note_type,
                tags=tags,
                resolved=ResolvedContent(
                    kind=resolved.kind, label=resolved.label, summary=note_text
                ),
                with_transcript=False,
                author=author,
                reuse_channels=reuse_channels,
                model=provider,
                **_ctx_kwargs(ctx),
            )
    else:
        note_text = build_note_markdown(
            file_id=file_id,
            title=title,
            date=date,
            note_type=note_type,
            tags=tags,
            resolved=resolved,
            with_transcript=with_transcript,
            author=author,
            reuse_channels=reuse_channels,
            model=note_model,
            date_created=_existing_date_created(overwrite) if overwrite else "",
            **_ctx_kwargs(ctx),
        )

    if overwrite is not None:
        # Rewritten in place, never moved: where the note lives now is the
        # vault's decision.
        folder, target, action = overwrite.parent, overwrite, "overwritten"
        dest = folder_in_vault(vault, str(overwrite))
    else:
        folder = send_destination(vault, dest)  # the folder sends_into_wiki() checked
        dest = dest.strip() or DEFAULT_DEST
        target, action = None, "created"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = target or unique_note_path(folder, date.replace("-", ""), title)
        target.write_text(note_text.rstrip() + "\n", encoding="utf-8")
    except OSError as exc:
        return VaultSendResult("write_failed", str(exc), vault=str(vault), dest=dest, via=via)

    import time as _time

    storage.upsert_note_reference(
        file_id=file_id, path=target, kind="vault-send", title=title, now=int(_time.time())
    )

    return VaultSendResult(
        "ok",
        f"{resolved.kind} note {'overwritten in place' if overwrite else 'written'}",
        path=str(target),
        vault=str(vault),
        dest=dest,
        content=resolved.kind,
        via=via,
        obsidian_url=obsidian_open_url(vault, target),
        tags=tags,
        action=action,
        existing=existing,
    )
