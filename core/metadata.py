"""Local note metadata, auto-tagging, and CMDS vault note generation."""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import app_config
from .paths import integrated_dir
from .storage import Storage
from .tags import normalize_tags
from .templates import load_template
from .classification import classify_snapshot

VAULT_CONTEXT_FILES = [
    "AGENTS.md",
    "🏛 CMDS Guide.md",
    "90. Settings/91. Templates/Template_05. Meeting Minutes.md",
    "90. Settings/94. Agent Settings/Meeting Transcription System Prompt.md",
]

CLAUDE_SKILL_CONTEXT_FILES = [
    Path.home() / ".claude/skills/meeting-minutes/SKILL.md",
    Path.home() / ".claude/skills/cmds-doc-formatter/SKILL.md",
    Path.home() / ".claude/skills/obsidian-yaml-frontmatter/SKILL.md",
]


def generate_note_metadata(
    storage: Storage,
    file_id: str,
    *,
    model: str = "claude",
    model_id: str = "",
    vault_path: Path | None = None,
    use_ai: bool = True,
) -> dict[str, Any]:
    """Generate and persist metadata/tags keyed by Plaud's immutable file id."""
    from .summarize import model_available, run_model

    vault_path = vault_path or app_config.obsidian_vault()
    snapshot = build_recording_snapshot(storage, file_id)
    metadata: dict[str, Any] = {}
    source = "auto"

    if use_ai and model_available(model):
        prompt = load_template("metadata").render(
            file_id=file_id,
            title=snapshot["title"],
            keywords=", ".join(snapshot["keywords"]),
            speakers=snapshot["speakers"],
            plaud_summaries=snapshot["summaries"],
            transcript=snapshot["transcript"][:30000],
            cmds_transcript=snapshot["cmds_transcript"][:30000],
            integrated_summary=snapshot["integrated_summary"][:16000],
            vault_context=load_vault_context(vault_path),
        )
        response = run_model(model, prompt, model_id=model_id or None)
        metadata = _extract_json_object(response)
        metadata["model"] = model
        metadata["model_id"] = model_id or ""
        source = "ai"

    fallback = fallback_metadata(snapshot)
    classification = classify_snapshot(snapshot)
    merged = {**fallback, **{k: v for k, v in metadata.items() if v not in (None, "")}}
    merged["file_id"] = file_id
    merged["stable_id"] = file_id
    merged["category"] = classification.cmds_category
    merged["folder_name"] = classification.folder_name
    merged["classification_confidence"] = classification.confidence
    merged["classification_reason"] = classification.reason
    merged["usage_status"] = merged.get("usage_status") or "metadata-ready"
    merged["tags"] = normalize_tags(
        [
            "plaud",
            *classification.tags,
            *fallback.get("tags", []),
            *as_list(merged.get("tags")),
        ]
    )

    now = int(time.time())
    storage.upsert_note_metadata(
        file_id=file_id,
        title=str(merged.get("title") or snapshot["title"]),
        description=str(merged.get("description") or fallback["description"]),
        note_type=str(merged.get("note_type") or fallback["note_type"]),
        status=str(merged.get("status") or "inProgress"),
        usage_status=str(merged.get("usage_status") or "metadata-ready"),
        category=str(merged.get("category") or classification.cmds_category),
        folder_name=str(merged.get("folder_name") or classification.folder_name),
        vault_path=vault_path,
        metadata=merged,
        generated_at=now,
        now=now,
    )
    storage.replace_generated_note_tags(
        file_id,
        as_list(merged.get("tags")),
        source=source,
        now=now,
    )
    return merged


def write_meeting_note(
    storage: Storage,
    file_id: str,
    *,
    model: str = "claude",
    model_id: str = "",
    vault_path: Path | None = None,
    draft_path: Path | None = None,
    out_path: Path | None = None,
    use_ai: bool = True,
) -> Path:
    """Create or update a CMDS-style meeting note in the main Obsidian vault."""
    from .summarize import model_available, run_model

    vault_path = vault_path or app_config.obsidian_vault()
    snapshot = build_recording_snapshot(storage, file_id)
    drafts = (
        [draft_path] if draft_path else find_related_drafts(vault_path, file_id, snapshot["title"])
    )
    draft_context = "\n\n".join(
        f"## Draft: {path}\n\n{read_text(path, 12000)}" for path in drafts if path and path.exists()
    )

    note_text = ""
    if use_ai and model_available(model):
        prompt = load_template("vault-meeting-note").render(
            file_id=file_id,
            title=snapshot["title"],
            keywords=", ".join(snapshot["keywords"]),
            speakers=snapshot["speakers"],
            plaud_summaries=snapshot["summaries"],
            transcript=snapshot["transcript"][:42000],
            cmds_transcript=snapshot["cmds_transcript"][:42000],
            integrated_summary=snapshot["integrated_summary"][:18000],
            vault_context=load_vault_context(vault_path),
            draft_context=draft_context or "(관련 초안 없음)",
            meeting_date=recorded_date(snapshot),
        )
        note_text = strip_markdown_fence(
            run_model(model, prompt, model_id=model_id or None, timeout=900)
        )

    if not note_text.strip():
        note_text = fallback_meeting_note(snapshot, draft_context=draft_context)

    target = out_path or default_meeting_note_path(storage, file_id, snapshot, vault_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(note_text.strip() + "\n", encoding="utf-8")

    now = int(time.time())
    existing = storage.get_note_metadata(file_id)
    payload: dict[str, Any] = {}
    if existing and existing["metadata_json"]:
        try:
            payload = json.loads(existing["metadata_json"] or "{}")
        except Exception:
            payload = {}
    payload.update({"meeting_note_path": str(target), "drafts": [str(p) for p in drafts]})
    storage.upsert_note_metadata(
        file_id=file_id,
        title=snapshot["title"],
        note_type="meeting",
        status="inProgress",
        usage_status="vault-linked",
        category="60. Collections/63. Meetings",
        folder_name="10. Meetings",
        vault_path=vault_path,
        draft_path=drafts[0] if drafts else None,
        final_note_path=target,
        metadata=payload,
        generated_at=now,
        now=now,
    )
    storage.add_note_tags(
        file_id,
        ["plaud", "meeting", "MeetingMinutes"],
        source="auto",
        now=now,
    )
    storage.upsert_note_reference(
        file_id=file_id,
        path=target,
        kind="meeting-note",
        title=snapshot["title"],
        now=now,
    )
    for draft in drafts:
        if draft:
            storage.upsert_note_reference(
                file_id=file_id,
                path=draft,
                kind="draft",
                title=draft.stem,
                now=now,
            )
    return target


def build_recording_snapshot(storage: Storage, file_id: str) -> dict[str, Any]:
    file_row = storage.get_file_row(file_id)
    content = storage.get_content_row(file_id)
    cmds = storage.get_cmds_transcript(file_id)
    title = (
        (content["title"] if content else None)
        or (file_row["filename"] if file_row else None)
        or file_id
    )
    keywords = json.loads(content["keywords"] or "[]") if content else []
    return {
        "file_id": file_id,
        "title": title,
        "keywords": keywords,
        "transcript": format_plaud_transcript(content),
        "summaries": format_plaud_summaries(content),
        "cmds_transcript": format_cmds_transcript(cmds),
        "integrated_summary": read_integrated_summary(file_id),
        "speakers": speaker_list(content, cmds),
        "file_row": dict(file_row) if file_row else {},
    }


def fallback_metadata(snapshot: dict[str, Any]) -> dict[str, Any]:
    title = snapshot["title"]
    text = " ".join(
        [
            title,
            " ".join(snapshot["keywords"]),
            snapshot["summaries"][:4000],
            snapshot["integrated_summary"][:4000],
        ]
    )
    note_type = "meeting" if looks_like_meeting(text) else "note"
    tags = infer_tags(title, snapshot["keywords"], text, note_type=note_type)
    summary = first_nonempty(snapshot["integrated_summary"], snapshot["summaries"])
    return {
        "title": title,
        "description": (
            f"Plaud recording metadata for {title}. Reference when reviewing "
            "the transcript, summaries, tags, and Obsidian filing status."
        ),
        "note_type": note_type,
        "status": "inProgress",
        "tags": tags,
        "summary": summary[:1200],
    }


def infer_tags(title: str, keywords: list[str], text: str, *, note_type: str) -> list[str]:
    seed = ["plaud", note_type]
    if note_type == "meeting":
        seed.extend(["meeting", "MeetingMinutes"])
    seed.extend(keywords[:12])
    for token in re.split(r"[\s·,;/|]+", title):
        if 2 <= len(token) <= 30:
            seed.append(token)
    if any(word in text for word in ("옵시디언", "Obsidian")):
        seed.append("Obsidian")
    if any(word in text for word in ("Claude", "클로드")):
        seed.append("Claude")
    if any(word in text for word in ("CMDS", "CMDSPACE", "커맨드스페이스")):
        seed.append("CMDS")
    return normalize_tags(seed)[:20]


def load_vault_context(vault_path: Path | None, *, max_chars: int = 24000) -> str:
    parts: list[str] = []
    for rel in VAULT_CONTEXT_FILES:
        if not vault_path:
            break
        path = vault_path / rel
        if path.exists():
            parts.append(f"# {rel}\n\n{read_text(path, 5000)}")
    for path in CLAUDE_SKILL_CONTEXT_FILES:
        if path.exists():
            parts.append(f"# Claude skill: {path.name}\n\n{read_text(path, 3500)}")
    return "\n\n---\n\n".join(parts)[:max_chars]


def find_related_drafts(
    vault_path: Path | None, file_id: str, title: str, *, limit: int = 3
) -> list[Path]:
    if not vault_path:
        return []
    patterns = [file_id]
    cleaned = re.sub(r"\s+", " ", title).strip()
    # Draft auto-linking is heuristic: only content-grep the vault for the title
    # when it is distinctive enough (long or multi-word) so a short fragment
    # doesn't match unrelated notes.
    if len(cleaned) >= 16 or len(cleaned.split()) >= 2:
        patterns.append(cleaned[:80])

    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        try:
            proc = subprocess.run(
                ["rg", "-l", "-F", pattern, str(vault_path), "-g", "*.md"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except Exception:
            continue
        for line in proc.stdout.splitlines():
            path = Path(line)
            if path not in seen:
                seen.add(path)
                found.append(path)
            if len(found) >= limit:
                return found
    return found


def default_meeting_note_path(
    storage: Storage,
    file_id: str,
    snapshot: dict[str, Any],
    vault_path: Path | None,
) -> Path:
    existing = storage.get_note_metadata(file_id)
    if existing and existing["final_note_path"]:
        return Path(existing["final_note_path"])
    if not vault_path:
        raise ValueError(
            "Obsidian vault not configured — set PLAUD_OBSIDIAN_VAULT, run "
            "`uv run plaud config-vault <path>`, or pass an explicit output path."
        )
    date = recorded_date(snapshot).replace("-", "")
    title = safe_filename(snapshot["title"]) or file_id[:8]
    return vault_path / "60. Collections/63. Meetings" / f"{date}_{title}.meeting.md"


def fallback_meeting_note(snapshot: dict[str, Any], *, draft_context: str = "") -> str:
    date = recorded_date(snapshot)
    title = snapshot["title"]
    tags = normalize_tags(["MeetingMinutes", "meeting", "plaud", *snapshot["keywords"][:8]])
    tag_block = "\n".join(f"  - {tag}" for tag in tags)
    draft_section = (
        f"\n## Draft Context\n\n{draft_context.strip()}\n" if draft_context.strip() else ""
    )

    self_name = app_config.author()
    # When no author is configured, leave the author/attendee/self-speaker
    # fields blank rather than inserting a name.
    if self_name:
        author_block = f'author:\n  - "[[{self_name}]]"\n'
        attendees_block = f'attendees:\n  - "[[{self_name}]]"\n'
        attendees_callout = f">- Attendees: [[{self_name}]]\n"
        speaker_row = f"| {self_name} | 본인 | 기본 포함 |"
    else:
        author_block = "author:\n"
        attendees_block = "attendees:\n"
        attendees_callout = ""
        speaker_row = "|  |  |  |"

    return f"""---
type: meeting
aliases: []
description: "Meeting minutes generated from Plaud recording {snapshot["file_id"]}. Reference when reviewing discussion, decisions, next steps, and raw transcript evidence."
{author_block}date created: {date}
date modified: {date}
date: {date}
{attendees_block}organization:
CMDS:
index: "[[🏷 Meeting Notes]]"
status: inProgress
tags:
{tag_block}
source: plaud
plaud_id: {snapshot["file_id"]}
---

>[!info]
>- Meeting Title: {title} Meeting
>- Meeting Date: [[{date}]]
{attendees_callout}>- Meeting Topic: {title}

## Summary

{first_nonempty(snapshot["integrated_summary"], snapshot["summaries"], "(요약 없음)")}

## Discussion

#### 주요 논의
- Plaud 녹음과 캐시된 요약을 기준으로 회의 내용을 정리함.

## Decisions

- 

## Next Steps

- [ ] 

## Speaker Map

| Label | 추정 정보 | 비고 |
|-------|-----------|------|
{speaker_row}

{draft_section}
## Transcript

{first_nonempty(snapshot["cmds_transcript"], snapshot["transcript"], "(전사 없음)")}
"""


def format_plaud_transcript(row: Any) -> str:
    if not row:
        return ""
    try:
        segs = json.loads(row["transcript"] or "[]")
    except Exception:
        return ""
    lines = []
    for seg in segs:
        lines.append(
            f"[{fmt_ts(int(seg.get('start_time') or 0))}] "
            f"{seg.get('speaker') or ''}: {seg.get('content') or ''}".strip()
        )
    return "\n".join(lines)


def format_cmds_transcript(row: Any) -> str:
    if not row:
        return ""
    try:
        segs = json.loads(row["segments"] or "[]")
    except Exception:
        return row["text"] or ""
    lines = []
    for seg in segs:
        lines.append(
            f"[{fmt_ts(int(seg.get('start_ms') or 0))}] "
            f"{seg.get('speaker') or ''}: {seg.get('content') or ''}".strip()
        )
    return "\n".join(lines)


def format_plaud_summaries(row: Any) -> str:
    if not row:
        return ""
    blocks = []
    if row["summary_md"]:
        blocks.append(f"## Plaud Summary\n\n{row['summary_md']}")
    try:
        extras = json.loads(row["summary_extra"] or "[]")
    except Exception:
        extras = []
    for i, body in enumerate(extras):
        blocks.append(f"## Plaud Template {i + 1}\n\n{body}")
    return "\n\n".join(blocks)


def read_integrated_summary(file_id: str) -> str:
    base = integrated_dir(file_id)
    if not base.exists():
        return ""
    parts = []
    for path in sorted(base.glob("*.summary.md"))[:5]:
        parts.append(f"## {path.name}\n\n{strip_frontmatter(read_text(path, 12000))}")
    return "\n\n".join(parts)


def speaker_list(content_row: Any, cmds_row: Any) -> str:
    speakers: set[str] = set()
    for raw, key in (
        (cmds_row["segments"] if cmds_row else "", "speaker"),
        (content_row["transcript"] if content_row else "", "speaker"),
    ):
        try:
            for seg in json.loads(raw or "[]"):
                if seg.get(key):
                    speakers.add(str(seg[key]))
        except Exception:
            pass
    return ", ".join(sorted(speakers))


def recorded_date(snapshot: dict[str, Any]) -> str:
    row = snapshot.get("file_row") or {}
    # start_time is epoch-ms, edit_time is epoch-s. Sanity-guard the year so a
    # malformed row (0 / far-future) cascades to the next source, not 1970/now.
    for ts, divisor in ((row.get("start_time"), 1000), (row.get("edit_time"), 1)):
        if not ts:
            continue
        try:
            dt = datetime.fromtimestamp(int(ts) / divisor)
        except (ValueError, OSError, OverflowError):
            continue
        if 2000 <= dt.year <= 2100:
            return dt.strftime("%Y-%m-%d")
    return datetime.now().strftime("%Y-%m-%d")


def _extract_json_object(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    if "{" in text:
        candidates.append(text[text.find("{") : text.rfind("}") + 1])
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            continue
    return {}


def strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", stripped)
        stripped = re.sub(r"\n```$", "", stripped)
    return stripped.strip()


def strip_frontmatter(raw: str) -> str:
    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 4)
        if end >= 0:
            return raw[end + 5 :].strip()
    return raw


def fmt_ts(ms: int) -> str:
    secs = max(0, ms // 1000)
    return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"


def read_text(path: Path, max_chars: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception:
        return ""


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [value]
    return [str(value)]


def first_nonempty(*values: str) -> str:
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""


def looks_like_meeting(text: str) -> bool:
    return any(
        marker in text for marker in ("회의", "미팅", "논의", "액션", "참석", "Meeting", "meeting")
    )


def safe_filename(raw: str) -> str:
    safe = re.sub(r"[\\/:*?\"<>|#\[\]\n\r]+", " ", raw)
    safe = re.sub(r"\s+", " ", safe).strip()
    return safe[:90]
