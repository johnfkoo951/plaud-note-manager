"""Content-reuse marks: which output channel a recording feeds, and how far.

Orthogonal to `usage_status` (lifecycle): a recording can be vault-linked AND
flagged as newsletter material. Channels mirror the user's actual output
channels (PLAN-v0.7 §5, vocabulary confirmed 2026-08-15).
"""

from __future__ import annotations

from typing import Any

from .storage import Storage

CHANNELS = [
    "newsletter",  # 더배러
    "lecture",  # 강의·특강 자산
    "shorts",  # 쇼츠/릴스
    "sns",  # SNS 카피 (Threads/X/LinkedIn/카톡)
    "consulting",  # 컨설팅 자료
    "research",  # 연구·에세이
    "other",
]

# Progression order; a file's overall reuse "state" is its furthest mark.
STATUSES = ["flagged", "drafted", "published"]


def validate_channel(channel: str) -> str:
    channel = channel.strip().lower()
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of: {', '.join(CHANNELS)}")
    return channel


def validate_status(status: str) -> str:
    status = status.strip().lower()
    if status not in STATUSES:
        raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
    return status


def reuse_rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "channel": r["channel"],
            "status": r["status"],
            "note": r["note"] or "",
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def overall_state(rows: list[Any]) -> str:
    """Furthest progression across channels ('' when unmarked)."""
    best = -1
    for r in rows:
        try:
            best = max(best, STATUSES.index(r["status"]))
        except ValueError:
            continue
    return STATUSES[best] if best >= 0 else ""


def reuse_summary(storage: Storage, file_id: str) -> dict[str, Any]:
    rows = storage.list_reuse(file_id)
    return {
        "file_id": file_id,
        "state": overall_state(rows),
        "targets": reuse_rows_to_dicts(rows),
    }


def reuse_channels_for_frontmatter(storage: Storage, file_id: str) -> list[str]:
    """`channel:status` strings for vault note frontmatter (Bases-queryable)."""
    return [f"{r['channel']}:{r['status']}" for r in storage.list_reuse(file_id)]
