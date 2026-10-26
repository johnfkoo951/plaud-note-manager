"""Glossary note: people, nicknames, proper nouns and jargon, kept in the vault.

One markdown note the user curates in Obsidian (and other tools — TTS, content
skills — read too). This module

  - resolves where it lives (config `glossary_path` → vault default),
  - hands its text to the final-transcript and speaker-name prompts, and
  - grows it: after a final transcript is made, a model lists names and terms
    the note does not have yet, and they are appended to the note's
    "검토 대기" section for the user to confirm or delete.

The curated sections are never rewritten — new candidates only ever land in
the review section, under a marker comment so a renamed heading still works.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import app_config
from .paths import DATA_DIR
from .storage import Storage

DEFAULT_GLOSSARY_RELPATH = "50. Assets/53. Transcription Context/cmds-glossary.md"
DEFAULT_CONTEXT_RELPATH = "50. Assets/53. Transcription Context/transcription-context-compact.md"

AUTO_HEADING = "## 검토 대기 (자동 수집)"
AUTO_MARKER = "<!-- plaud-glossary:auto -->"
MAX_PROMPT_CHARS = 5000
MAX_EXTRACT_GLOSSARY_CHARS = 12000
MAX_EXTRACT_TRANSCRIPT_CHARS = 60000
MAX_CANDIDATES = 15

STATE_FILE = DATA_DIR / "glossary_harvest.json"
LOCK_FILE = DATA_DIR / "glossary.lock"

KIND_LABELS = {"person": "인물", "place": "장소·기관", "term": "용어"}

_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_DATE_MODIFIED_RE = re.compile(r"^(date modified:\s*).*$", re.MULTILINE)


# -- where the notes live ---------------------------------------------------


def _configured(key: str) -> str:
    """Raw path setting. Separate so tests can isolate it from data/config.json."""
    return str(app_config.load().get(key) or "").strip()


def resolve_note(raw: str, default_relpath: str) -> Path | None:
    """Absolute or `~/…` paths as given; anything else is vault-relative."""
    vault = app_config.obsidian_vault()
    raw = (raw or "").strip()
    if raw:
        path = Path(os.path.expanduser(raw))
        if path.is_absolute():
            return path
        return vault / path if vault else None
    return vault / default_relpath if vault else None


def glossary_path() -> Path | None:
    return resolve_note(_configured("glossary_path"), DEFAULT_GLOSSARY_RELPATH)


def context_path() -> Path | None:
    return resolve_note(_configured("transcription_context_path"), DEFAULT_CONTEXT_RELPATH)


def auto_update_enabled() -> bool:
    return bool(app_config.load().get("glossary_auto_update", True))


# -- reading ------------------------------------------------------------------


def strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1)


def read_glossary(path: Path | None = None) -> str:
    path = path or glossary_path()
    if path is None or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def load_for_prompt(*, max_chars: int = MAX_PROMPT_CHARS) -> str:
    """Glossary body for a prompt: curated part first, unreviewed part labelled.

    Candidates waiting for review are still useful hints, but the model must
    know they are unconfirmed — and when the note is long they are what gets
    cut, not the entries the user wrote.
    """
    body = _COMMENT_RE.sub("", strip_frontmatter(read_glossary())).strip()
    if not body:
        return ""
    curated, sep, pending = body.partition(AUTO_HEADING)
    out = curated.strip()
    pending = pending.strip()
    if sep and pending:
        out += "\n\n## 검토 대기 (자동 수집된 미확인 후보)\n" + pending
    return out[:max_chars]


# -- extracting ---------------------------------------------------------------


def final_transcript(storage: Storage, file_id: str) -> str:
    """Newest final transcript; else the CMDS transcript; else Plaud's."""
    from .integrate import NO_TRANSCRIPT, collect_inputs
    from .relabel import final_files

    finals = [p for p in final_files(file_id) if p.name.endswith(".transcript.md")]
    if finals:
        newest = max(finals, key=lambda p: p.stat().st_mtime)
        text = strip_frontmatter(newest.read_text(encoding="utf-8", errors="ignore")).strip()
        if text and text != NO_TRANSCRIPT:
            return text
    inputs = collect_inputs(storage, file_id)
    return inputs["cmds_transcript"] or inputs["plaud_transcript"]


def parse_candidates(raw: str) -> list[dict[str, Any]]:
    """The JSON array from model output (fences/prose tolerated)."""
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term") or "").strip()
        if not term:
            continue
        kind = str(item.get("kind") or "term").strip().lower()
        out.append(
            {
                "term": term,
                "kind": kind if kind in KIND_LABELS else "term",
                "aliases": _str_list(item.get("aliases")),
                "errors": _str_list(item.get("errors")),
                "note": str(item.get("note") or "").strip(),
                "evidence": str(item.get("evidence") or "").strip(),
            }
        )
    return out[:MAX_CANDIDATES]


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def new_candidates(candidates: list[dict[str, Any]], glossary_text: str) -> list[dict[str, Any]]:
    """Drop what the note already mentions anywhere, and repeats within the batch."""
    known = glossary_text.lower()
    seen: set[str] = set()
    out = []
    for item in candidates:
        key = item["term"].lower()
        if len(item["term"]) < 2 or key in seen or key in known:
            continue
        seen.add(key)
        out.append(item)
    return out


def format_entry(item: dict[str, Any], *, source: str, date: str) -> str:
    """One line per entry, the same shape the curated sections use."""
    parts = [f"- **{item['term']}** — {KIND_LABELS.get(item['kind'], '용어')}"]
    if item.get("aliases"):
        parts.append("호칭: " + ", ".join(item["aliases"]))
    if item.get("note"):
        parts.append(item["note"])
    if item.get("errors"):
        parts.append("오류: " + ", ".join(item["errors"]))
    parts.append(f"출처: {source} ({date})" if source else f"수집: {date}")
    return " · ".join(parts)


def append_entries(text: str, lines: list[str], *, now_iso: str = "") -> str:
    """Insert entry lines at the end of the review section (created if absent)."""
    if not lines:
        return text
    block = "\n".join(lines)
    if AUTO_MARKER in text:
        start = text.index(AUTO_MARKER) + len(AUTO_MARKER)
        next_heading = re.search(r"^## ", text[start:], re.MULTILINE)
        end = start + next_heading.start() if next_heading else len(text)
        section = text[start:end].rstrip("\n")
        tail = text[end:]
        text = text[:start] + section + "\n" + block + "\n" + ("\n" + tail if tail else "")
    else:
        text = text.rstrip("\n") + f"\n\n{AUTO_HEADING}\n{AUTO_MARKER}\n{block}\n"
    if now_iso:
        front = _FRONTMATTER_RE.match(text)
        if front:
            head = _DATE_MODIFIED_RE.sub(lambda m: m.group(1) + now_iso, front.group(0), count=1)
            text = head + text[front.end() :]
    return text


# -- the loop -------------------------------------------------------------------


@dataclass
class HarvestReport:
    file_id: str
    status: str = (
        ""  # added | nothing-new | skipped | disabled | no-glossary | no-transcript | unavailable
    )
    path: str = ""
    added: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


@contextmanager
def _locked() -> Iterator[None]:
    """One harvest at a time: Generate All can finish several finals at once,
    and two writers appending to one note would drop each other's lines."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def harvest(
    storage: Storage,
    file_id: str,
    *,
    model: str = "",
    force: bool = False,
    dry_run: bool = False,
) -> HarvestReport:
    """Extract new glossary candidates from a recording and append them.

    Runs once per recording; `force` runs it again. Failures never raise —
    this is a side job of making a final transcript and must not fail it.
    """
    from .summarize import ModelFailed, ModelNotInstalled, model_available, run_model
    from .templates import load_template

    report = HarvestReport(file_id=file_id)
    path = glossary_path()
    if path is None or not path.is_file():
        report.status = "no-glossary"
        report.detail = f"glossary note not found: {path}" if path else "vault not configured"
        return report
    report.path = str(path)

    with _locked():
        state = _load_state()
        if not force and file_id in state:
            report.status = "skipped"
            report.detail = "already harvested (use --force to run again)"
            return report

        transcript = final_transcript(storage, file_id)
        if not transcript.strip():
            report.status = "no-transcript"
            return report

        model = model or app_config.metadata_model()
        if not model_available(model):
            report.status = "unavailable"
            report.detail = f"model '{model}' unavailable"
            return report

        text = read_glossary(path)
        file_row = storage.get_file_row(file_id)
        title = (file_row["filename"] if file_row else "") or ""
        prompt = load_template("glossary-extract").render(
            title=title,
            glossary=(load_for_prompt(max_chars=MAX_EXTRACT_GLOSSARY_CHARS) or "(비어 있음)"),
            transcript=transcript[:MAX_EXTRACT_TRANSCRIPT_CHARS],
        )
        try:
            raw = run_model(model, prompt, model_id=app_config.model_id_for(model) or None)
        except (ModelFailed, ModelNotInstalled) as exc:
            report.status = "unavailable"
            report.detail = str(exc)
            return report

        report.added = new_candidates(parse_candidates(raw), text)
        report.status = "added" if report.added else "nothing-new"
        if dry_run:
            return report

        if report.added:
            date = time.strftime("%Y-%m-%d")
            lines = [format_entry(item, source=title, date=date) for item in report.added]
            # Re-read under the lock: the user may have saved the note meanwhile.
            current = read_glossary(path)
            path.write_text(
                append_entries(current, lines, now_iso=time.strftime("%Y-%m-%dT%H:%M")),
                encoding="utf-8",
            )
        state[file_id] = {"at": int(time.time()), "added": len(report.added)}
        _save_state(state)
    return report
