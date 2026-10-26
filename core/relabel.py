"""Rename speakers across one recording: CMDS transcript + every final transcript.

The final transcript (``data/integrated/<id>/*.md``) copies the CMDS speaker
labels at integration time. Renaming used to mean relabeling CMDS and then
paying for a fresh integration run just to carry the names over. A label is a
label, so this rewrites them in place instead — instant and free.

Every mapping is applied *simultaneously*, so ``A=B B=A`` swaps two speakers
instead of collapsing both into one.

What gets rewritten in a final's text:
  - the speaker of a ``[HH:MM:SS] label: …`` line;
  - a diarizer placeholder (``speaker_0``, ``Speaker 3``, ``화자 2``) anywhere —
    it can only ever mean that speaker;
  - a real name only where it attributes a line (``- 구요한: "…"``), never
    inside prose, where the same name may be someone talking *about* them.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .storage import Storage

_PLACEHOLDER_RE = re.compile(r"^(?:speaker|spk|화자)[ _]?\d+$", re.IGNORECASE)
_TRANSCRIPT_LINE_RE = re.compile(r"^(\s*\[[^\]\n]*\]\s*)([^:\n]+?)(\s*:)")
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def is_placeholder(label: str) -> bool:
    """`speaker_0`, `Speaker 3`, `화자 2` — a diarizer's number, not a name."""
    return bool(_PLACEHOLDER_RE.match((label or "").strip()))


def clean_mapping(mapping: dict[str, str]) -> dict[str, str]:
    """Drop blank targets and no-op pairs; trim both sides."""
    out: dict[str, str] = {}
    for old, new in mapping.items():
        old, new = (old or "").strip(), (new or "").strip()
        if old and new and old != new:
            out[old] = new
    return out


def relabel_text(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    """Rewrite speaker labels in a final transcript/summary. Returns (text, changes)."""
    mapping = clean_mapping(mapping)
    if not mapping or not text:
        return text, 0

    front = ""
    match = _FRONTMATTER_RE.match(text)
    if match:
        front, text = match.group(0), text[match.end() :]

    changes = 0
    placeholders = [k for k in mapping if is_placeholder(k)]
    names = [k for k in mapping if not is_placeholder(k)]
    # ASCII-only boundaries: a Korean particle glued on ("speaker_0과") still
    # ends the token, while "speaker_01" is never read as "speaker_0".
    token_re = (
        re.compile(
            r"(?<![A-Za-z0-9_])("
            + "|".join(re.escape(k) for k in sorted(placeholders, key=len, reverse=True))
            + r")(?![A-Za-z0-9_])"
        )
        if placeholders
        else None
    )
    attribution_re = (
        re.compile(
            r"^(\s*(?:[-*]\s+)?(?:\*\*)?)("
            + "|".join(re.escape(k) for k in sorted(names, key=len, reverse=True))
            + r")((?:\*\*)?\s*:)"
        )
        if names
        else None
    )

    def tokens(segment: str) -> str:
        nonlocal changes
        if token_re is None:
            return segment
        segment, n = token_re.subn(lambda m: mapping[m.group(1)], segment)
        changes += n
        return segment

    out_lines = []
    for line in text.split("\n"):
        hit = _TRANSCRIPT_LINE_RE.match(line)
        if hit and hit.group(2).strip() in mapping:
            label = mapping[hit.group(2).strip()]
            line = hit.group(1) + label + hit.group(3) + tokens(line[hit.end() :])
            changes += 1
        else:
            if attribution_re is not None:
                line, n = attribution_re.subn(
                    lambda m: m.group(1) + mapping[m.group(2)] + m.group(3), line
                )
                changes += n
            line = tokens(line)
        out_lines.append(line)
    return front + "\n".join(out_lines), changes


def relabel_segments(segments: list[dict[str, Any]], mapping: dict[str, str]) -> int:
    """Rename `speaker` on CMDS segments in place. Returns segments touched."""
    mapping = clean_mapping(mapping)
    touched = 0
    for seg in segments:
        speaker = (seg.get("speaker") or "").strip()
        if speaker in mapping:
            seg["speaker"] = mapping[speaker]
            touched += 1
    return touched


def final_files(file_id: str) -> list[Path]:
    """Every final-transcript artifact of a recording (all models/templates)."""
    from .paths import integrated_base

    base = integrated_base() / file_id
    if not base.is_dir():
        return []
    return sorted(p for p in base.glob("*.md") if p.is_file())


@dataclass
class RelabelReport:
    file_id: str
    mapping: dict[str, str]
    cmds_segments: int = 0
    files: list[dict[str, Any]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.cmds_segments or self.files)


def relabel_recording(
    storage: Storage, file_id: str, mapping: dict[str, str], *, now: int | None = None
) -> RelabelReport:
    """Apply one speaker mapping to the CMDS transcript and every final."""
    mapping = clean_mapping(mapping)
    report = RelabelReport(file_id=file_id, mapping=mapping)
    if not mapping:
        return report
    now = now or int(time.time())

    row = storage.get_cmds_transcript(file_id)
    if row:
        segs = json.loads(row["segments"] or "[]")
        report.cmds_segments = relabel_segments(segs, mapping)
        if report.cmds_segments:
            storage.update_cmds_segments(
                file_id, row["model"], json.dumps(segs, ensure_ascii=False), now=now
            )

    for path in final_files(file_id):
        text = path.read_text(encoding="utf-8")
        new_text, changes = relabel_text(text, mapping)
        if changes:
            path.write_text(new_text, encoding="utf-8")
            report.files.append({"path": str(path), "changes": changes})

    _record_names(storage, file_id, mapping, now=now)
    return report


def _record_names(storage: Storage, file_id: str, mapping: dict[str, str], *, now: int) -> None:
    """Keep the dual row's map current and grow the saved-speakers roster.

    The stored map is composed with the new one, so renaming a name a second
    time ("홍길동" → "구요한") follows through to the original diarizer label
    instead of leaving a stale entry behind.
    """
    row = storage.get_dual(file_id)
    stored: dict[str, str] = json.loads(row["speaker_map"] or "{}") if row else {}
    composed = {k: mapping.get(v, v) for k, v in stored.items()}
    targets = set(stored.values())
    for old, new in mapping.items():
        if old not in targets and is_placeholder(old):
            composed[old] = new
    if row is not None and composed != stored:
        storage.upsert_dual(
            file_id,
            speaker_map=json.dumps(composed, ensure_ascii=False),
            error=row["error"],
            now=now,
        )

    existing = {r["name"] for r in storage.list_speakers()}
    for name in mapping.values():
        if name and name not in existing and not is_placeholder(name):
            try:
                storage.add_speaker(name=name, now=now)
                existing.add(name)
            except Exception:
                pass
