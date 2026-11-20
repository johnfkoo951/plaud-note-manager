"""Reminders mined from recording summaries — appointments, deadlines, promises.

A model reads each new recording's summary once and lists what its owner
should not forget (`scan`). Twice a day OmniControl asks which of those are
due now (`due`), sends them, and acknowledges what went out (`ack`), so an
item never repeats in the same slot.

Local-only tables, created on first use (never synced to Plaud, outside the
shared SCHEMA_VERSION the Swift app also reads):
  reminders       one row per extracted item
  reminder_scans  recordings already read — each is read once
  reminder_sends  (item, slot) pairs already sent
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from . import app_config
from .storage import Storage

KINDS = ("event", "deadline", "todo", "note")
KIND_LABELS = {"event": "일정", "deadline": "마감", "todo": "할 일", "note": "기억할 것"}
MAX_SUMMARY_CHARS = 9000
MAX_ITEMS_PER_RECORDING = 8
# A recording this recent counts as "new": its items are announced once.
# Older recordings (a first backfill) are only reminded when they fall due.
FRESH_HOURS = 48
LINK_SCHEME = "plaudnote://open?id="

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    due_date    TEXT,
    due_time    TEXT,
    importance  INTEGER NOT NULL DEFAULT 2,
    evidence    TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    dismissed_at INTEGER,
    UNIQUE (file_id, kind, title, due_date)
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_date);
CREATE TABLE IF NOT EXISTS reminder_scans (
    file_id    TEXT PRIMARY KEY,
    scanned_at INTEGER NOT NULL,
    model      TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL,
    items      INTEGER NOT NULL DEFAULT 0,
    detail     TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS reminder_sends (
    reminder_id INTEGER NOT NULL,
    slot        TEXT NOT NULL,
    sent_at     INTEGER NOT NULL,
    PRIMARY KEY (reminder_id, slot)
);
"""


def ensure_tables(storage: Storage) -> None:
    with storage._connect() as conn:
        conn.executescript(_SCHEMA)


def link_for(file_id: str) -> str:
    return LINK_SCHEME + file_id


# ---------------------------------------------------------------- data


@dataclass
class Reminder:
    id: int
    file_id: str
    kind: str
    title: str
    due_date: date | None
    due_time: str | None
    importance: int
    evidence: str
    recording_title: str = ""
    recording_start: datetime | None = None

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "kind_label": KIND_LABELS.get(self.kind, self.kind),
            "title": self.title,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "due_time": self.due_time,
            "importance": self.importance,
            "evidence": self.evidence,
            "recording": {
                "id": self.file_id,
                "title": self.recording_title,
                "started_at": self.recording_start.isoformat() if self.recording_start else None,
                "link": link_for(self.file_id),
            },
        }


@dataclass(frozen=True)
class Pick:
    item: Reminder
    slot: str
    reason: str

    def to_json(self) -> dict:
        return {**self.item.to_json(), "slot": self.slot, "reason": self.reason}


@dataclass
class ScanReport:
    scanned: int = 0
    items: int = 0
    failed: int = 0
    skipped_no_model: bool = False
    remaining: int = 0
    details: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------- policy


def reminder_reason(item: Reminder, *, today: date, when: str,
                    fresh: bool) -> tuple[str, str] | None:
    """Whether `item` belongs in the `when` reminder on `today`.

    Returns (slot, reason) — the slot is the dedupe key recorded in
    `reminder_sends`, so the same item is never sent twice for one slot — or
    None to leave it out. `when` is "morning" (07:30) or "evening" (21:00);
    `fresh` means its recording came in within the last FRESH_HOURS.

    Default lead times: the evening before and the morning of anything dated;
    deadlines also on each of the three mornings before; important dated
    items once when they enter the coming week; anything else from a fresh
    recording once, as new. Past items are never sent.
    """
    if item.due_date is not None:
        days = (item.due_date - today).days
        if days < 0:
            return None
        if when == "morning":
            if days == 0:
                return f"{today}:morning", ("오늘 마감" if item.kind == "deadline" else "오늘")
            if item.kind == "deadline" and days <= 3:
                return f"{today}:morning", f"마감 D-{days}"
            if item.importance >= 3 and days <= 7:
                return "week", f"이번 주 · D-{days}"
        if when == "evening" and days == 1:
            return f"{today}:evening", "내일"
    if fresh and item.importance >= 2:
        return "new", "새로 들어옴"
    return None


def select(items: list[Reminder], *, today: date, when: str, now: datetime,
           sent: set[tuple[int, str]]) -> list[Pick]:
    """Apply `reminder_reason` and drop what already went out in its slot.

    Ordered for reading: dated items by date/time, then undated by importance.
    """
    picks: list[Pick] = []
    for item in items:
        fresh = (item.recording_start is not None
                 and now - item.recording_start <= timedelta(hours=FRESH_HOURS))
        decided = reminder_reason(item, today=today, when=when, fresh=fresh)
        if decided is None:
            continue
        slot, reason = decided
        if (item.id, slot) in sent:
            continue
        picks.append(Pick(item=item, slot=slot, reason=reason))
    picks.sort(key=lambda p: (
        p.item.due_date is None,
        p.item.due_date or date.max,
        p.item.due_time or "99:99",
        -p.item.importance,
    ))
    return picks


# ---------------------------------------------------------------- extraction

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def parse_items(raw: str, *, recorded: date) -> list[dict]:
    """Validate the model's JSON array. Unknown kinds, unparsable or
    implausible dates (before the recording, more than ~13 months after) and
    empty titles are dropped; time is kept only with a date."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            return []
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    out: list[dict] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "")).strip().lower()
        title = " ".join(str(entry.get("title", "")).split())[:120]
        if kind not in KINDS or not title:
            continue
        due: date | None = None
        raw_date = entry.get("date")
        if isinstance(raw_date, str) and _DATE_RE.match(raw_date.strip()):
            try:
                due = date.fromisoformat(raw_date.strip())
            except ValueError:
                due = None
            if due and not (recorded - timedelta(days=1) <= due <= recorded + timedelta(days=400)):
                due = None
        if kind in ("event", "deadline") and due is None:
            # A dated kind without a usable date is still worth keeping as a to-do.
            kind = "todo"
        raw_time = entry.get("time")
        due_time = raw_time.strip() if (due and isinstance(raw_time, str)
                                        and _TIME_RE.match(raw_time.strip())) else None
        try:
            importance = max(1, min(3, int(entry.get("importance", 2))))
        except (TypeError, ValueError):
            importance = 2
        evidence = " ".join(str(entry.get("evidence", "")).split())[:240]
        out.append({"kind": kind, "title": title, "due_date": due, "due_time": due_time,
                    "importance": importance, "evidence": evidence})
        if len(out) >= MAX_ITEMS_PER_RECORDING:
            break
    return out


_WEEKDAYS = "월화수목금토일"


def _owner_names(storage: Storage) -> str:
    names = [r["name"] for r in storage.list_speakers() if r["is_self"]]
    return ", ".join(names) or "녹음한 사람"


def _recordings_to_scan(storage: Storage, *, since_ms: int, force: bool) -> list[dict]:
    with storage._connect() as conn:
        rows = conn.execute(
            """SELECT f.id, f.filename, f.start_time, f.duration,
                      c.summary_md, COALESCE(m.description, '') AS description
                 FROM files f
                 JOIN file_content c ON c.file_id = f.id
                 LEFT JOIN note_metadata m ON m.file_id = f.id
                WHERE f.is_trash = 0
                  AND f.start_time >= ?
                  AND TRIM(COALESCE(c.summary_md, '')) <> ''
                  AND (? OR f.id NOT IN (SELECT file_id FROM reminder_scans
                                          WHERE status IN ('ok', 'empty')))
                ORDER BY f.start_time DESC""",
            (since_ms, 1 if force else 0),
        ).fetchall()
    return [dict(r) for r in rows]


def scan(storage: Storage, *, days: int = 14, limit: int = 20, model: str = "",
         model_id: str = "", force: bool = False, now: datetime | None = None,
         run_model: Callable[..., str] | None = None,
         model_available: Callable[[str], bool] | None = None) -> ScanReport:
    """Read summaries of recent, not-yet-read recordings and store their items.

    Newest first, at most `limit` per call; `remaining` says how many wait for
    the next run. A failed model call is recorded and retried next time.
    """
    from .summarize import ModelCallError
    from .summarize import model_available as _available
    from .summarize import run_model as _run
    from .templates import load_template

    run_model = run_model or _run
    model_available = model_available or _available
    ensure_tables(storage)
    now = now or datetime.now().astimezone()
    report = ScanReport()
    since_ms = int((now - timedelta(days=days)).timestamp() * 1000)
    todo = _recordings_to_scan(storage, since_ms=since_ms, force=force)
    report.remaining = max(0, len(todo) - limit)
    if not todo:
        return report

    model = model or app_config.metadata_model()
    if not model_available(model):
        report.skipped_no_model = True
        report.remaining = len(todo)
        return report
    model_id = model_id or app_config.model_id_for(model) or ""
    template = load_template("reminder-extract")
    owner = _owner_names(storage)

    for rec in todo[:limit]:
        started = datetime.fromtimestamp(rec["start_time"] / 1000).astimezone()
        recorded = started.date()
        prompt = template.render(
            owner=owner,
            title=rec["filename"] or "",
            recorded_at=f"{started:%Y-%m-%d} ({_WEEKDAYS[started.weekday()]}) {started:%H:%M}",
            today=f"{now:%Y-%m-%d} ({_WEEKDAYS[now.weekday()]})",
            description=rec["description"] or "(없음)",
            summary=rec["summary_md"][:MAX_SUMMARY_CHARS],
        )
        try:
            raw = run_model(model, prompt, model_id=model_id or None, timeout=240)
        except ModelCallError as exc:
            report.failed += 1
            _record_scan(storage, rec["id"], model_id or model, "failed", 0, str(exc)[:200])
            report.details.append({"id": rec["id"], "status": "failed"})
            continue
        items = parse_items(raw, recorded=recorded)
        saved = _save_items(storage, rec["id"], items, now=now)
        _record_scan(storage, rec["id"], model_id or model, "ok" if items else "empty", saved, "")
        report.scanned += 1
        report.items += saved
        report.details.append({"id": rec["id"], "title": rec["filename"], "items": saved})
    return report


def _save_items(storage: Storage, file_id: str, items: list[dict], *, now: datetime) -> int:
    saved = 0
    with storage._connect() as conn:
        for it in items:
            cur = conn.execute(
                """INSERT OR IGNORE INTO reminders
                       (file_id, kind, title, due_date, due_time, importance, evidence, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (file_id, it["kind"], it["title"],
                 it["due_date"].isoformat() if it["due_date"] else None,
                 it["due_time"], it["importance"], it["evidence"], int(now.timestamp())),
            )
            saved += cur.rowcount
    return saved


def _record_scan(storage: Storage, file_id: str, model: str, status: str,
                 items: int, detail: str) -> None:
    with storage._connect() as conn:
        conn.execute(
            """INSERT INTO reminder_scans (file_id, scanned_at, model, status, items, detail)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(file_id) DO UPDATE SET scanned_at = excluded.scanned_at,
                   model = excluded.model, status = excluded.status,
                   items = excluded.items, detail = excluded.detail""",
            (file_id, int(time.time()), model, status, items, detail),
        )


# ---------------------------------------------------------------- reading


def load(storage: Storage, *, include_dismissed: bool = False,
         from_date: date | None = None) -> list[Reminder]:
    """Stored items joined with their recording (trashed recordings drop out)."""
    ensure_tables(storage)
    sql = """SELECT r.*, f.filename, f.start_time
               FROM reminders r JOIN files f ON f.id = r.file_id
              WHERE f.is_trash = 0"""
    args: list = []
    if not include_dismissed:
        sql += " AND r.dismissed_at IS NULL"
    if from_date is not None:
        sql += " AND (r.due_date IS NULL OR r.due_date >= ?)"
        args.append(from_date.isoformat())
    sql += " ORDER BY r.due_date IS NULL, r.due_date, r.due_time, r.importance DESC"
    with storage._connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [
        Reminder(
            id=r["id"], file_id=r["file_id"], kind=r["kind"], title=r["title"],
            due_date=date.fromisoformat(r["due_date"]) if r["due_date"] else None,
            due_time=r["due_time"], importance=r["importance"], evidence=r["evidence"],
            recording_title=r["filename"] or "",
            recording_start=(datetime.fromtimestamp(r["start_time"] / 1000).astimezone()
                             if r["start_time"] else None),
        )
        for r in rows
    ]


def sent_slots(storage: Storage) -> set[tuple[int, str]]:
    ensure_tables(storage)
    with storage._connect() as conn:
        return {(r[0], r[1]) for r in conn.execute("SELECT reminder_id, slot FROM reminder_sends")}


def due(storage: Storage, *, when: str, today: date | None = None,
        now: datetime | None = None) -> list[Pick]:
    if when not in ("morning", "evening"):
        raise ValueError(f"when must be morning or evening: {when}")
    now = now or datetime.now().astimezone()
    today = today or now.date()
    items = load(storage, from_date=today)
    return select(items, today=today, when=when, now=now, sent=sent_slots(storage))


def ack(storage: Storage, pairs: list[tuple[int, str]]) -> int:
    """Record that these (item, slot) pairs went out. Anything sent also
    counts as announced, so it never comes back later as "new"."""
    ensure_tables(storage)
    rows = set()
    for rid, slot in pairs:
        rows.add((rid, slot))
        rows.add((rid, "new"))
    stamp = int(time.time())
    with storage._connect() as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO reminder_sends (reminder_id, slot, sent_at) VALUES (?, ?, ?)",
            [(rid, slot, stamp) for rid, slot in sorted(rows)],
        )
        return conn.total_changes - before


def dismiss(storage: Storage, reminder_id: int) -> bool:
    ensure_tables(storage)
    with storage._connect() as conn:
        cur = conn.execute(
            "UPDATE reminders SET dismissed_at = ? WHERE id = ? AND dismissed_at IS NULL",
            (int(time.time()), reminder_id),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------- one day


def day_recordings(storage: Storage, day: date) -> list[dict]:
    """Recordings that started on `day` (local time), oldest first, with the
    span they cover and their app link — the input for calendar linking."""
    start = datetime.combine(day, datetime.min.time()).astimezone()
    end = start + timedelta(days=1)
    with storage._connect() as conn:
        rows = conn.execute(
            """SELECT f.id, f.filename, f.start_time, f.duration,
                      COALESCE(m.description, '') AS description
                 FROM files f LEFT JOIN note_metadata m ON m.file_id = f.id
                WHERE f.is_trash = 0 AND f.start_time >= ? AND f.start_time < ?
                ORDER BY f.start_time""",
            (int(start.timestamp() * 1000), int(end.timestamp() * 1000)),
        ).fetchall()
    out = []
    for r in rows:
        began = datetime.fromtimestamp(r["start_time"] / 1000).astimezone()
        ended = began + timedelta(milliseconds=r["duration"] or 0)
        out.append({
            "id": r["id"],
            "title": r["filename"] or "",
            "start": began.isoformat(timespec="minutes"),
            "end": ended.isoformat(timespec="minutes"),
            "minutes": round((r["duration"] or 0) / 60000),
            "description": r["description"],
            "link": link_for(r["id"]),
        })
    return out


def report_json(report: ScanReport) -> dict:
    return asdict(report)
