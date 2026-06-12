"""Canonical filesystem layout for the project.

All Python and Swift code reads/writes under these well-known paths so the
CLI and app share a single mental model of where things live.

  data/
    plaud.db                     # SQLite metadata + cache
    transcripts/{file_id}/       # Generated transcripts (per recording)
      plaud.transcript.md
      plaud.summary.md
      plaud.outline.md
      cmds.transcript.md         # ElevenLabs Scribe output
      cmds.transcript.json       # raw segments (with timestamps + speakers)
    summaries/{file_id}/         # Multi-model CMDS summaries
      {model}__{template}.md     # e.g. claude__default.md
    slots.json                   # User-configured summary slot list
  templates/                     # Summary prompt templates (markdown)
    default.md
    meeting.md
    lecture.md
    ...                          # User templates dropped here
"""

from __future__ import annotations

from pathlib import Path

from .config import PROJECT_ROOT

DATA_DIR = PROJECT_ROOT / "data"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
SUMMARIES_DIR = DATA_DIR / "summaries"
INTEGRATED_DIR = DATA_DIR / "integrated"
SLOTS_FILE = DATA_DIR / "slots.json"
TEMPLATES_DIR = PROJECT_ROOT / "templates"


def _override(kind: str) -> Path | None:
    """Honor user-configured override from data/config.json if present."""
    try:
        from .app_config import path_override

        return path_override(kind)
    except Exception:
        return None


def transcripts_dir(file_id: str) -> Path:
    base = _override("transcripts") or TRANSCRIPTS_DIR
    p = base / file_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def summaries_dir(file_id: str) -> Path:
    base = _override("summaries") or SUMMARIES_DIR
    p = base / file_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def integrated_base() -> Path:
    """Integrated output root WITHOUT creating it (for read-only scans)."""
    return _override("integrated") or INTEGRATED_DIR


def integrated_dir(file_id: str) -> Path:
    base = _override("integrated") or INTEGRATED_DIR
    p = base / file_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def integrated_paths(file_id: str, *, model: str, template: str) -> dict[str, Path]:
    base = integrated_dir(file_id)
    stem = f"{_safe(model)}__{_safe(template)}"
    return {
        "all": base / f"{stem}.md",
        "transcript": base / f"{stem}.transcript.md",
        "summary": base / f"{stem}.summary.md",
    }


def template_path(name: str) -> Path:
    return TEMPLATES_DIR / f"{name}.md"


def summary_path(file_id: str, *, model: str, template: str) -> Path:
    return summaries_dir(file_id) / f"{_safe(model)}__{_safe(template)}.md"


def ensure_directories() -> None:
    for p in (DATA_DIR, TRANSCRIPTS_DIR, SUMMARIES_DIR, INTEGRATED_DIR, TEMPLATES_DIR):
        p.mkdir(parents=True, exist_ok=True)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "x"
