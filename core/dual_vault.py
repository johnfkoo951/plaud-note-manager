"""Vault landing for dual-transcribed recordings.

Follows the vault's own transcript conventions (PLAN-v0.7 §4, sourced from
the vault's `Transcripts Inbox Guide` / `Transcripts Archive Guide`):

- Landing lane: `00. Inbox/08. Transcripts/08-1. Plaud/` — the Plaud intake
  lane. Downstream routing (meeting-minutes → 63. Meetings, archive move) is
  the vault-side CMDS Process's job; the app only lands the note.
- Filename: `YYYYMMDD_제목.transcript.md` (type-suffix convention).
- Body: title header → info callout → 개요 (integrated summary) → 최종
  전사본 (cross-analyzed transcript with real speaker names).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

from . import app_config
from .metadata import build_recording_snapshot, recorded_date, safe_filename, strip_frontmatter
from .paths import integrated_paths
from .storage import Storage
from .vault_send import VaultSendResult, obsidian_open_url

TRANSCRIPT_LANE = "00. Inbox/08. Transcripts/08-1. Plaud"


def _read_artifact(path: Path) -> str:
    try:
        return strip_frontmatter(path.read_text(encoding="utf-8", errors="ignore")).strip()
    except OSError:
        return ""


def _speaker_names(storage: Storage, file_id: str) -> list[str]:
    row = storage.get_dual(file_id)
    if row and row["speaker_map"]:
        try:
            mapping = json.loads(row["speaker_map"])
            names = [v for v in mapping.values() if v]
            if names:
                return sorted(set(names))
        except json.JSONDecodeError:
            pass
    from .dual_pipeline import cmds_speakers

    return cmds_speakers(storage, file_id)


def build_transcript_note(
    storage: Storage,
    file_id: str,
    *,
    model: str,
    template: str = "integrated",
    now: datetime | None = None,
) -> tuple[str, str, str]:
    """Returns (markdown, date YYYY-MM-DD, title). Raises when artifacts are missing."""
    paths = integrated_paths(file_id, model=model, template=template)
    summary = _read_artifact(paths["summary"])
    transcript = _read_artifact(paths["transcript"])
    if not transcript:
        raise RuntimeError("no integrated transcript — run the dual pipeline first")

    snapshot = build_recording_snapshot(storage, file_id)
    title = snapshot["title"] or file_id
    date = recorded_date(snapshot)
    speakers = _speaker_names(storage, file_id)
    now = now or datetime.now()
    author = app_config.author()

    from .reuse import reuse_channels_for_frontmatter
    from .vault_send import note_tags

    tags = note_tags(snapshot, "transcript")
    reuse_channels = reuse_channels_for_frontmatter(storage, file_id)

    from .frontmatter import CmdsFrontmatter, iso_minute, model_label
    from .vault_send import note_context

    ctx = note_context(storage, file_id, snapshot)
    if ctx["tags"]:
        from .tags import normalize_tags

        tags = normalize_tags([*tags, *ctx["tags"]])[:20]
    if "transcript" not in tags:
        tags = [*tags, "transcript"]
    fm = CmdsFrontmatter(
        type="transcript",
        aliases=ctx["aliases"],
        description=(
            f"Dual-transcribed final transcript: {title}. "
            "Cross-analyzed from Plaud and ElevenLabs Scribe with confirmed speaker names."
        ),
        author=author,
        model=model_label(model),
        date_created=iso_minute(now),
        date=date,
        tags=tags,
        cmds=ctx["cmds"],
        index=ctx["index"],
        status="inProgress",
        source="plaud",
        plaud_id=file_id,
        dual=True,
        speakers=speakers,
        keywords=ctx["keywords"],
        related=ctx["related"],
        reuse_channels=reuse_channels,
    )
    lines = fm.lines()
    lines.append("")
    # Plaud titles usually already lead with "MM-DD " — don't double it.
    if len(date) == 10 and not re.match(r"^\d{2}-\d{2}\s", title):
        lines.append(f"# {date[5:7]}-{date[8:10]} {title}")
    else:
        lines.append(f"# {title}")
    lines.append("")
    lines.append(f"> [!info] Plaud 녹음 · {date} · 듀얼 전사 (Plaud × ElevenLabs)")
    lines.append(f"> [web.plaud.ai](https://web.plaud.ai/file/{file_id}) · `{file_id}`")
    if speakers:
        lines.append(f"> 참석자: {' '.join(f'[[{s}]]' for s in speakers)}")
    lines.append("")
    if summary:
        # The integrated summary carries its own `## …` structure; only wrap
        # it in an 개요 heading when it's a plain paragraph.
        if not summary.lstrip().startswith("#"):
            lines.append("## 개요")
            lines.append("")
        lines.append(summary)
        lines.append("")
    lines.append("## 최종 전사본")
    lines.append("")
    lines.append(transcript)
    if ctx["related"]:
        lines.append("")
        lines.append("## 관련 노트")
        lines.append("")
        lines.extend(f"- [[{r}]]" for r in ctx["related"])
    lines.append("")
    return "\n".join(lines), date, title


def send_dual_to_vault(
    storage: Storage,
    file_id: str,
    *,
    model: str,
    template: str = "integrated",
) -> VaultSendResult:
    vault = app_config.obsidian_vault()
    if vault is None:
        return VaultSendResult("no_vault", "obsidian vault not configured")
    try:
        markdown, date, title = build_transcript_note(
            storage, file_id, model=model, template=template
        )
    except RuntimeError as exc:
        return VaultSendResult("no_content", str(exc), vault=str(vault))

    folder = vault / TRANSCRIPT_LANE
    stem = f"{date.replace('-', '')}_{safe_filename(title) or file_id}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{stem}.transcript.md"
        counter = 2
        while target.exists():
            target = folder / f"{stem} ({counter}).transcript.md"
            counter += 1
        target.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    except OSError as exc:
        return VaultSendResult("write_failed", str(exc), vault=str(vault), dest=TRANSCRIPT_LANE)

    storage.upsert_note_reference(
        file_id=file_id, path=target, kind="dual-transcript", title=title, now=int(time.time())
    )
    return VaultSendResult(
        "ok",
        "dual transcript landed in the vault inbox lane",
        path=str(target),
        vault=str(vault),
        dest=TRANSCRIPT_LANE,
        content="transcript",
        via="direct",
        obsidian_url=obsidian_open_url(vault, target),
    )
