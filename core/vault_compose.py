"""Run a template over a recording and land the result in a chosen vault.

`vault_send` sends whatever output already exists. This composes instead: pick
the template for the kind of recording it is, generate, then write a note that
satisfies the destination vault's own contract.

The two vaults get different artifacts, because they are for different things:

- **Main** — a note about the recording. Delegated to `vault_send`, which
  already builds `CmdsFrontmatter` and lands it in the inbox lane.
- **Wiki** — an immutable raw source. The verbatim transcript is preserved
  under `## Original Content` with the generated summary above it, and the
  note lands in `10. Raw Sources/`, the only path the wiki's profile resolver
  maps to `wiki-raw-source`.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, fields
from pathlib import Path

from . import app_config
from .frontmatter import iso_minute
from .metadata import build_recording_snapshot, recorded_date
from .paths import summary_path
from .storage import Storage
from .templates import (
    KIND_SUMMARY,
    TemplateKindError,
    deprecation_notice,
    load_template,
    require_kind,
)
from .vault_send import (
    VaultSendResult,
    _pick_transcript,
    early_duplicate_result,
    existing_notes,
    is_wiki_target,
    note_context,
    note_tags,
    obsidian_open_url,
    resolve_vault,
    send_duplicates,
    sends_into_wiki,
    validate_on_duplicate,
    vault_send,
)
from .wiki_frontmatter import (
    WikiRawSourceFrontmatter,
    WikiSchemaError,
    agent_label,
    build_raw_source_note,
    model_fields,
)

#: The only wiki path whose profile resolver returns `wiki-raw-source`.
WIKI_RAW_ROOT = "10. Raw Sources"

#: The vault files raw sources under a subfolder per category.
WIKI_RAW_SUBFOLDER = {
    "Articles": "11. Articles",
    "Papers": "12. Papers",
    "Books": "13. Books",
    "Transcripts": "14. Transcripts",
    "Clippings": "15. Clippings",
    "AI Research": "16. AI Research",
}

#: Back-compat alias for the root; the note lands in the category subfolder.
WIKI_RAW_DEST = WIKI_RAW_ROOT


def wiki_raw_dest(category: str) -> str:
    sub = WIKI_RAW_SUBFOLDER.get(category)
    return f"{WIKI_RAW_ROOT}/{sub}" if sub else WIKI_RAW_ROOT


#: The category a composed raw source gets — WikiRawSourceFrontmatter's own
#: default, so the duplicate check looks where the writer will write.
RAW_SOURCE_CATEGORY: str = next(
    f.default for f in fields(WikiRawSourceFrontmatter) if f.name == "category"
)

WIKI_OVERWRITE_REFUSAL = (
    "wiki raw sources are immutable — use --on-duplicate skip, or new to add a new "
    "version and keep the old one"
)


def wiki_slug(title: str, *, max_len: int = 80) -> str:
    """`some-title-words` — the vault's existing raw-source filename style.

    Plaud titles usually open with the recording's own `MM-DD `, which the
    filename already carries as a full date. Drop it rather than emit
    `2026-09-14-09-14-…`.
    """
    body = re.sub(r"^\d{2}-\d{2}\s+", "", title.strip())
    cleaned = re.sub(r"[^0-9A-Za-z가-힣ぁ-んァ-ヶ一-龥]+", "-", body).strip("-")
    return cleaned[:max_len].rstrip("-") or "plaud-transcript"


@dataclass
class ComposeResult:
    # ok | duplicate | invalid_option (overwrite on the wiki) | bad_template |
    # needs_purpose | no_vault | no_transcript | model_failed | schema_error |
    # wiki_unsupported (a main --dest that lands in the wiki), and from the
    # main-vault send: no_content | write_failed.
    # `vault-compose --json` emits every one with exit 0; the app maps them.
    status: str
    detail: str = ""
    path: str = ""
    vault: str = ""
    dest: str = ""
    template: str = ""
    obsidian_url: str = ""
    action: str = ""  # created | overwritten | skipped (on ok)
    existing: list[dict] = field(default_factory=list)  # as VaultSendResult.existing

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "detail": self.detail,
            "path": self.path,
            "vault": self.vault,
            "dest": self.dest,
            "template": self.template,
            "obsidian_url": self.obsidian_url,
            "action": self.action,
            "existing": self.existing,
        }


def _from_send(sent: VaultSendResult, template: str) -> ComposeResult:
    return ComposeResult(
        sent.status,
        sent.detail,
        path=sent.path,
        vault=sent.vault,
        dest=sent.dest,
        template=template,
        obsidian_url=sent.obsidian_url,
        action=sent.action,
        existing=sent.existing,
    )


def _summarize_with(
    storage: Storage, file_id: str, *, template: str, model: str, model_id: str, transcript: str
) -> str:
    """Generate the template's output, reusing a cached run when present."""
    from .summarize import model_available, model_unavailable_message, summarize

    output_model = model_id or model
    cached = summary_path(file_id, model=output_model, template=template)
    if cached.exists():
        text = cached.read_text(encoding="utf-8")
        # summarize() prepends its own wrapper frontmatter; drop it.
        if text.startswith("---"):
            _, _, rest = text.partition("\n---\n")
            text = rest or text
        if text.strip():
            return text.strip()

    if not model_available(model):
        raise RuntimeError(model_unavailable_message(model))

    snapshot = build_recording_snapshot(storage, file_id)
    out = summarize(
        file_id=file_id,
        model=model,
        template_name=template,
        transcript=transcript,
        title=snapshot.get("title") or "",
        keywords=", ".join((snapshot.get("keywords") or [])[:15]),
        speakers=snapshot.get("speakers") or "",
        model_id=model_id,
    )
    text = out.read_text(encoding="utf-8")
    if text.startswith("---"):
        _, _, rest = text.partition("\n---\n")
        text = rest or text
    return text.strip()


def _wiki_description(title: str, template: str) -> str:
    """The English one-liner the wiki requires of every note.

    Deliberately factual rather than interpretive: this is a retrieval hint for
    a future agent, and inventing a characterisation of a recording nobody has
    read yet would be worse than a plain statement of what it is.
    """
    kind = template.removeprefix("note-") or "recording"
    return (
        f"Verbatim Plaud transcript of {title}. "
        f"Collected as a {kind} source; the generated summary sits above the "
        f"original content, which is the record."
    )


def compose_to_wiki(
    storage: Storage,
    file_id: str,
    *,
    template: str,
    collection_purpose: str,
    model: str = "",
    model_id: str = "",
    dry_run: bool = False,
    on_duplicate: str = "ask",
) -> ComposeResult:
    if on_duplicate == "overwrite":
        return ComposeResult("invalid_option", WIKI_OVERWRITE_REFUSAL, template=template)
    # One line: yaml_quote does not escape newlines, so a pasted multi-line
    # purpose would be folded silently by YAML.
    collection_purpose = re.sub(r"\s*[\r\n]+\s*", " ", collection_purpose).strip()
    if not collection_purpose:
        return ComposeResult(
            "needs_purpose",
            "the wiki requires collectionPurpose — state why this was collected "
            "and how it will be reused",
            template=template,
        )

    vault = resolve_vault("wiki")
    if vault is None or not vault.is_dir():
        return ComposeResult(
            "no_vault",
            "wiki vault not found — set it via `plaud config-wiki-vault`",
            template=template,
        )

    snapshot = build_recording_snapshot(storage, file_id)
    transcript = _pick_transcript(storage, file_id, snapshot)
    if transcript is None or not transcript.transcript.strip():
        return ComposeResult(
            "no_transcript",
            f"no transcript cached for {file_id} — a raw source needs its original text",
            vault=str(vault),
            template=template,
        )

    # Before generation: an existing raw source for this recording decides
    # first. The same-name candidate is where the writer below would write.
    title = snapshot.get("title") or file_id
    date = recorded_date(snapshot)
    same_name = vault / wiki_raw_dest(RAW_SOURCE_CATEGORY) / f"{date}-{wiki_slug(title)}.md"
    existing = existing_notes(
        storage, file_id, vault=vault, kind="wiki-raw-source", same_name=same_name
    )
    early = early_duplicate_result(existing, on_duplicate, vault=vault, content="", via="")
    if early is not None:
        return _from_send(early, template)

    model = model or app_config.dual_model()
    model_id = model_id or app_config.model_id_for(model) or ""
    try:
        summary = _summarize_with(
            storage,
            file_id,
            template=template,
            model=model,
            model_id=model_id,
            transcript=transcript.transcript,
        )
    except Exception as exc:
        return ComposeResult("model_failed", str(exc), vault=str(vault), template=template)

    ctx = note_context(storage, file_id, snapshot, dest_root=vault)
    main_vault = app_config.obsidian_vault()
    fm_model, fm_effort = model_fields(model)

    frontmatter = WikiRawSourceFrontmatter(
        description=_wiki_description(title, template),
        collection_purpose=collection_purpose.strip(),
        source=f"https://web.plaud.ai/file/{file_id}",
        aliases=ctx["aliases"],
        tags=["raw-source", *note_tags(snapshot, "transcript")],
        authors=ctx["speakers"],
        agent=agent_label(model),
        model=fm_model,
        effort=fm_effort,
        date_created=date,
        date_ingested=iso_minute(),
        source_vault=main_vault.name if main_vault else "",
        main_vault_cmds=ctx["cmds"],
        main_vault_related=ctx["main_related"][:5],  # the wiki asks for 2-5 links
    )

    try:
        note = build_raw_source_note(
            frontmatter=frontmatter,
            title=title,
            original_content=transcript.transcript,
            summary=summary,
            plaud_url=f"https://web.plaud.ai/file/{file_id}",
            recorded_date=date,
        )
    except WikiSchemaError as exc:
        return ComposeResult("schema_error", str(exc), vault=str(vault), template=template)

    dest = wiki_raw_dest(frontmatter.category)
    folder = vault / dest
    target = folder / f"{date}-{wiki_slug(title)}.md"
    counter = 2
    while target.exists():
        target = folder / f"{date}-{wiki_slug(title)} ({counter}).md"
        counter += 1
    if dry_run:
        return ComposeResult(
            "ok",
            f"dry run — would write {len(note)} chars",
            path=str(target),
            vault=str(vault),
            dest=dest,
            template=template,
        )

    folder.mkdir(parents=True, exist_ok=True)
    target.write_text(note.rstrip() + "\n", encoding="utf-8")
    storage.upsert_note_reference(
        file_id=file_id, path=str(target), kind="wiki-raw-source", now=int(time.time())
    )
    detail = f"wrote {target.name}"
    if existing:  # on_duplicate == "new"
        detail += " — a new version; the earlier raw source is kept"
    return ComposeResult(
        "ok",
        detail,
        path=str(target),
        vault=str(vault),
        dest=dest,
        template=template,
        obsidian_url=obsidian_open_url(vault, target),
        action="created",
        existing=existing,
    )


def compose(
    file_id: str,
    *,
    template: str,
    to: str = "main",
    dest: str = "",
    collection_purpose: str = "",
    model: str = "",
    model_id: str = "",
    dry_run: bool = False,
    storage: Storage | None = None,
    on_duplicate: str = "ask",
) -> ComposeResult:
    """Generate `template` for `file_id` and land it in vault `to`.

    An existing note for the recording is handled by `on_duplicate`
    (ask | skip | overwrite | new) before anything is generated; the wiki
    refuses overwrite, since raw sources are immutable."""
    validate_on_duplicate(on_duplicate)
    # Checked first: a cached summary is reused without calling summarize(),
    # so its own kind check would never see a cached integrate-prompt run.
    try:
        loaded = load_template(template)
        require_kind(loaded, KIND_SUMMARY)
    except FileNotFoundError:
        return ComposeResult("bad_template", f"template not found: {template}", template=template)
    except (OSError, UnicodeDecodeError) as exc:
        return ComposeResult(
            "bad_template", f"template unreadable: {template} ({exc})", template=template
        )
    except TemplateKindError as exc:
        return ComposeResult("bad_template", str(exc), template=template)
    # Deprecated templates ask the model for their own YAML block, which would
    # land as a second front matter block in the note. Only composing refuses
    # them; summarize and existing slots keep running them.
    if notice := deprecation_notice(loaded):
        return ComposeResult("bad_template", notice, template=template)

    # `wiki`, the wiki's path or a folder inside it all take the raw-source
    # route; vault_send would refuse them anyway, after a wasted model call.
    vault = resolve_vault(to)
    if is_wiki_target(to, vault):
        return compose_to_wiki(
            storage or Storage(),
            file_id,
            template=template,
            collection_purpose=collection_purpose,
            model=model,
            model_id=model_id,
            dry_run=dry_run,
            on_duplicate=on_duplicate,
        )
    if vault is None or not vault.is_dir():
        return ComposeResult(
            "no_vault",
            f"vault '{to}' not configured/found — set it via `plaud config-vault`",
            template=template,
        )
    # A --dest can still walk into the wiki (absolute, `..`, symlink); vault_send
    # would refuse it only after the summary had been generated.
    if sends_into_wiki(to, vault, dest):
        return ComposeResult(
            "wiki_unsupported",
            f"--dest '{dest}' lands in the wiki, which takes raw sources only — "
            'use `--to wiki --collection-purpose "..."` instead',
            vault=str(vault),
            template=template,
        )

    # Main vault (or another explicit path): generate, then hand the existing
    # sender the freshly written summary to file.
    storage = storage or Storage()
    snapshot = build_recording_snapshot(storage, file_id)
    transcript = _pick_transcript(storage, file_id, snapshot)
    if transcript is None or not transcript.transcript.strip():
        return ComposeResult(
            "no_transcript",
            f"no transcript cached for {file_id}",
            template=template,
        )
    # Before generation: ask/skip on an existing note need no summary at all.
    existing = send_duplicates(
        storage,
        file_id,
        vault=vault,
        dest=dest,
        title=snapshot["title"],
        date=recorded_date(snapshot),
    )
    early = early_duplicate_result(
        existing, on_duplicate, vault=vault, content="summary", via="direct"
    )
    if early is not None:
        return _from_send(early, template)

    model = model or app_config.metadata_model()
    model_id = model_id or app_config.model_id_for(model) or ""
    try:
        _summarize_with(
            storage,
            file_id,
            template=template,
            model=model,
            model_id=model_id,
            transcript=transcript.transcript,
        )
    except Exception as exc:
        return ComposeResult("model_failed", str(exc), template=template)

    if dry_run:
        return ComposeResult(
            "ok",
            "dry run — summary generated, not filed",
            vault=str(vault),
            dest=dest,
            template=template,
        )

    sent: VaultSendResult = vault_send(
        file_id,
        to=to,
        dest=dest,
        content="summary",
        model=model_id or model,
        template=template,
        storage=storage,
        note_model=model_id or model,
        on_duplicate=on_duplicate,
    )
    return _from_send(sent, template)


def vault_choices() -> list[dict[str, str]]:
    """Configured vault targets, for a picker. Only ones that actually exist."""
    out: list[dict[str, str]] = []
    for key, label in (("main", "Main vault"), ("wiki", "LLM Wiki")):
        path: Path | None = resolve_vault(key)
        if path and path.is_dir():
            out.append({"key": key, "label": label, "name": path.name, "path": str(path)})
    return out
