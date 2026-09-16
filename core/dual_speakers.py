"""Speaker real-name proposal for the dual-transcribe pipeline.

Cross-references three sources (PLAN-v0.7 §3):
  1. ElevenLabs (CMDS) segments — utterance content per speaker_n
  2. Plaud transcript — its own speaker labels (may already carry real names)
  3. The vault's Transcription Context note — real-name roster + common
     STT-misrecognition corrections ("오류: 홍길둥" → 홍길동)

plus the saved-speakers roster accumulated from previously confirmed maps.
Returns `[{speaker, name, confidence, evidence}]` for the confirmation UI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import app_config
from .storage import Storage

CONTEXT_RELPATH = "50. Assets/53. Transcription Context/transcription-context-compact.md"
MAX_CONTEXT_CHARS = 6000
SAMPLE_PER_SPEAKER = 6
MAX_SAMPLE_CHARS = 9000


def transcription_context_path() -> Path | None:
    vault = app_config.obsidian_vault()
    if not vault:
        return None
    path = vault / CONTEXT_RELPATH
    return path if path.exists() else None


def load_transcription_context(*, hint: str = "", max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """Roster blocks from the vault context note, most relevant first.

    The note is organized as `## section` headings with ~190-token code
    blocks. Section 1 (핵심 인물) is always included; other sections are
    ranked by keyword overlap with `hint` (title/folder/tags) so e.g. an LG
    recording pulls the LG contacts block.
    """
    path = transcription_context_path()
    if path is None:
        return ""
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""

    sections = re.split(r"\n(?=## )", raw)
    if not sections:
        return ""
    head, rest = [], []
    hint_words = {w for w in re.split(r"[\s·/,()]+", hint.lower()) if len(w) >= 2}
    for sec in sections:
        if not sec.strip().startswith("## "):
            continue
        first = sec.strip().splitlines()[0]
        if "1." in first or "핵심" in first:
            head.append(sec)
            continue
        score = sum(1 for w in hint_words if w in sec.lower())
        rest.append((score, sec))
    rest.sort(key=lambda t: t[0], reverse=True)
    ordered = head + [sec for score, sec in rest if score > 0]
    # With no hint matches, still include the next block or two as general roster.
    if len(ordered) == len(head):
        ordered += [sec for _, sec in rest[:2]]
    out = "\n".join(ordered)
    return out[:max_chars]


def _cmds_sample(storage: Storage, file_id: str) -> str:
    row = storage.get_cmds_transcript(file_id)
    if not row:
        return ""
    segs = json.loads(row["segments"] or "[]")
    per_speaker: dict[str, list[str]] = {}
    for s in segs:
        sp = s.get("speaker") or "?"
        bucket = per_speaker.setdefault(sp, [])
        if len(bucket) >= SAMPLE_PER_SPEAKER:
            continue
        secs = int(s.get("start_ms") or 0) // 1000
        bucket.append(f"[{secs // 60}:{secs % 60:02d}] {sp}: {(s.get('content') or '').strip()}")
    lines: list[str] = []
    for sp in sorted(per_speaker):
        lines.extend(per_speaker[sp])
        lines.append("")
    return "\n".join(lines)[:MAX_SAMPLE_CHARS]


def _plaud_sample(storage: Storage, file_id: str) -> str:
    row = storage.get_content_row(file_id)
    if not row:
        return ""
    segs = json.loads(row["transcript"] or "[]")
    lines = []
    for s in segs[:80]:
        secs = int(s.get("start_time") or 0) // 1000
        speaker = s.get("speaker") or s.get("original_speaker") or "?"
        lines.append(
            f"[{secs // 60}:{secs % 60:02d}] {speaker}: {(s.get('content') or '').strip()}"
        )
    return "\n".join(lines)[:MAX_SAMPLE_CHARS]


def _hint(storage: Storage, file_id: str) -> str:
    parts = []
    file_row = storage.get_file_row(file_id)
    if file_row:
        parts.append(file_row["filename"] or "")
    meta = storage.get_note_metadata(file_id)
    if meta:
        parts.append(meta["folder_name"] or "")
        parts.append(meta["category"] or "")
    parts.extend(r["tag"] for r in storage.list_note_tags(file_id))
    return " ".join(filter(None, parts))


def parse_proposal(raw: str) -> list[dict[str, Any]]:
    """Extract the JSON array from model output (fences/prose tolerated)."""
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for item in data:
        if not isinstance(item, dict) or not item.get("speaker"):
            continue
        out.append(
            {
                "speaker": str(item["speaker"]),
                "name": str(item.get("name") or "").strip(),
                "confidence": float(item.get("confidence") or 0),
                "evidence": str(item.get("evidence") or "").strip(),
            }
        )
    return out


def propose_speaker_names(
    storage: Storage, file_id: str, *, model: str = ""
) -> list[dict[str, Any]]:
    """LLM proposal, aligned to the actual CMDS speaker set."""
    from .dual_pipeline import cmds_speakers
    from .summarize import model_available, run_model
    from .templates import load_template

    speakers = cmds_speakers(storage, file_id)
    if not speakers:
        return []
    model = model or app_config.metadata_model()
    if not model_available(model):
        return []

    file_row = storage.get_file_row(file_id)
    known = ", ".join(r["name"] for r in storage.list_speakers()) or "(없음)"
    prompt = load_template("speaker-names").render(
        title=(file_row["filename"] if file_row else "") or "",
        transcription_context=load_transcription_context(hint=_hint(storage, file_id)) or "(없음)",
        known_speakers=known,
        cmds_sample=_cmds_sample(storage, file_id),
        plaud_sample=_plaud_sample(storage, file_id) or "(없음)",
    )
    raw = run_model(model, prompt, model_id=app_config.model_id_for(model) or None)
    proposal = parse_proposal(raw)

    # Align to the real speaker set: keep order, fill gaps, drop unknowns.
    by_speaker = {p["speaker"]: p for p in proposal}
    return [
        by_speaker.get(sp, {"speaker": sp, "name": "", "confidence": 0.0, "evidence": ""})
        for sp in speakers
    ]
