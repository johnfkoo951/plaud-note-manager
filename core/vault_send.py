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

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import app_config
from .metadata import (
    build_recording_snapshot,
    first_nonempty,
    format_cmds_transcript,
    format_plaud_transcript,
    looks_like_meeting,
    recorded_date,
    safe_filename,
    strip_frontmatter,
    strip_markdown_fence,
)
from .paths import integrated_dir, summaries_dir, summary_path
from .paths import _safe as safe_component
from .storage import Storage
from .tags import normalize_tags

MEETINGS_DEST = "60. Collections/63. Meetings"
DEFAULT_DEST = "00. Inbox"
WIKI_VAULT_DIRNAME = "CMDS_LLM_Wiki"


@dataclass(frozen=True, slots=True)
class ResolvedContent:
    kind: str  # integrated | summary | plaud | transcript
    label: str  # artifact label, e.g. "claude-opus-4-8__integrated"
    summary: str
    transcript: str = ""
    source_path: str = ""


@dataclass(slots=True)
class VaultSendResult:
    status: str  # ok | no_content | no_vault | write_failed | model_failed
    detail: str = ""
    path: str = ""
    vault: str = ""
    dest: str = ""
    content: str = ""
    via: str = ""
    obsidian_url: str = ""
    tags: list[str] = field(default_factory=list)


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


def resolve_vault(target: str) -> Path | None:
    """Map a --to value onto a vault root. main|wiki|<absolute path>."""
    target = (target or "main").strip()
    if target in ("", "main"):
        return app_config.obsidian_vault()
    if target == "wiki":
        return wiki_vault()
    import os

    path = Path(os.path.expanduser(target))
    return path if path.is_dir() else None


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
    return ResolvedContent(
        kind="integrated",
        label=chosen.name.removesuffix(".summary.md"),
        summary=summary,
        transcript=_read(transcript_path if transcript_path.exists() else None),
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
) -> str:
    """Deterministic CMDS-style note (direct mode) — frontmatter conventions
    follow the vault-meeting-note template: English quoted description, plain
    tags, ISO dates, source/plaud_id fields."""
    now = now or datetime.now()
    created = now.strftime("%Y-%m-%d %H:%M")
    kind_label = f"{resolved.kind} ({resolved.label})" if resolved.label else resolved.kind

    lines = ["---"]
    lines.append(
        "description: "
        + _yaml_quote(f"Plaud recording note: {title}. Generated from the {resolved.kind} output.")
    )
    if author:
        lines.append("author:")
        lines.append(f'  - "[[{author}]]"')
    lines.append(f"date created: {created}")
    lines.append(f"date: {date}")
    lines.append(f"type: {note_type}")
    lines.append("status: inProgress")
    lines.append("source: plaud")
    lines.append(f"plaud_id: {file_id}")
    lines.append(f"content_kind: {kind_label}")
    lines.append("tags:")
    lines.extend(f"  - {t}" for t in tags)
    lines.append("---")
    lines.append("")
    lines.append(f"> [!info] Plaud 녹음 · {date}")
    lines.append(f"> [web.plaud.ai](https://web.plaud.ai/file/{file_id}) · `{file_id}`")
    lines.append("")
    if resolved.summary:
        lines.append(resolved.summary)
    if resolved.transcript and (with_transcript or not resolved.summary):
        if resolved.summary:
            lines.append("")
            lines.append("## Transcript")
            lines.append("")
        lines.append(resolved.transcript)
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
) -> str:
    """Prompt for the interactive `--via claude-window` mode: Claude Code files
    the note itself with the user's vault skills."""
    transcript_block = (
        f"\n## 전사본\n{resolved.transcript[:40000]}\n"
        if with_transcript and resolved.transcript
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
2. 파일명은 `YYYYMMDD_제목.md` (한글 제목 OK), 위 대상 폴더에 저장.
3. frontmatter에 source: plaud, plaud_id, tags, date created 포함.
4. 아래 콘텐츠는 이미 통합·요약된 결과물이니 구조를 존중하며 다듬어.
5. 저장 후 파일 절대 경로를 마지막 줄에 출력하고 종료.

## 콘텐츠 ({resolved.kind}: {resolved.label})
{resolved.summary[:45000]}
{transcript_block}"""


def unique_note_path(folder: Path, date_compact: str, title: str) -> Path:
    stem = f"{date_compact}_{safe_filename(title) or 'plaud-note'}"
    path = folder / f"{stem}.md"
    counter = 2
    while path.exists():
        path = folder / f"{stem} ({counter}).md"
        counter += 1
    return path


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
) -> VaultSendResult:
    """Resolve content, build the note (direct or headless-AI), write it into
    the vault, and record the reference. `claude-window` is NOT handled here —
    the CLI launches the Terminal itself using build_filing_prompt."""
    storage = storage or Storage()
    vault = vault_override or resolve_vault(to)
    if vault is None or not vault.is_dir():
        return VaultSendResult(
            "no_vault",
            f"vault '{to}' not configured/found — set it via `plaud config-vault` "
            "(main) or `plaud config-wiki-vault` (wiki)",
            via=via,
        )

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
    note_type = _note_type(storage, file_id, snapshot)
    tags = note_tags(snapshot, note_type)
    author = app_config.author()

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
        )

    dest = dest.strip() or DEFAULT_DEST
    folder = vault / dest
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = unique_note_path(folder, date.replace("-", ""), title)
        target.write_text(note_text.rstrip() + "\n", encoding="utf-8")
    except OSError as exc:
        return VaultSendResult("write_failed", str(exc), vault=str(vault), dest=dest, via=via)

    import time as _time

    storage.upsert_note_reference(
        file_id=file_id, path=target, kind="vault-send", title=title, now=int(_time.time())
    )

    return VaultSendResult(
        "ok",
        f"{resolved.kind} note written",
        path=str(target),
        vault=str(vault),
        dest=dest,
        content=resolved.kind,
        via=via,
        obsidian_url=obsidian_open_url(vault, target),
        tags=tags,
    )
