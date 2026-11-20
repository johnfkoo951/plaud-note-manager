from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from core import reminders
from core.reminders import Reminder, parse_items, reminder_reason, select
from core.storage import Storage
from core.summarize import ModelFailed

KST_NOW = datetime(2026, 11, 20, 7, 30).astimezone()


def _item(id_: int = 1, *, kind: str = "event", due: date | None = None,
          importance: int = 2, started: datetime | None = None,
          time_: str | None = None) -> Reminder:
    return Reminder(id=id_, file_id=f"f{id_}", kind=kind, title=f"item {id_}",
                    due_date=due, due_time=time_, importance=importance, evidence="",
                    recording_title="rec", recording_start=started)


# ---------------------------------------------------------------- parse


def test_parse_keeps_valid_items_and_resolves_shapes() -> None:
    raw = """Here you go:
```json
[
  {"kind": "event", "title": "  평생교육원   통화 ", "date": "2026-11-24", "time": "14:00", "importance": 3},
  {"kind": "deadline", "title": "견적 회신", "date": "next week", "importance": 2},
  {"kind": "todo", "title": "자료 보내기", "date": null, "time": "09:00"},
  {"kind": "gossip", "title": "모르는 종류"},
  {"kind": "note", "title": ""},
  {"kind": "event", "title": "너무 먼 일정", "date": "2029-01-01"}
]
```"""
    items = parse_items(raw, recorded=date(2026, 11, 20))
    assert [i["title"] for i in items] == ["평생교육원 통화", "견적 회신", "자료 보내기", "너무 먼 일정"]
    assert items[0]["due_date"] == date(2026, 11, 24) and items[0]["due_time"] == "14:00"
    # A dated kind whose date is unusable is kept as an undated to-do.
    assert items[1]["kind"] == "todo" and items[1]["due_date"] is None
    assert items[2]["due_time"] is None           # time without a date is dropped
    assert items[3]["kind"] == "todo"             # > 400 days out is implausible


def test_parse_rejects_garbage() -> None:
    assert parse_items("no json here", recorded=date(2026, 11, 20)) == []
    assert parse_items('{"kind": "event"}', recorded=date(2026, 11, 20)) == []


def test_parse_caps_items_per_recording() -> None:
    raw = json.dumps([{"kind": "todo", "title": f"t{i}"} for i in range(20)])
    assert len(parse_items(raw, recorded=date(2026, 11, 20))) == reminders.MAX_ITEMS_PER_RECORDING


# ---------------------------------------------------------------- policy

TODAY = date(2026, 11, 20)


@pytest.mark.parametrize(
    ("kind", "offset", "importance", "when", "expected"),
    [
        ("event", 0, 2, "morning", "오늘"),
        ("deadline", 0, 2, "morning", "오늘 마감"),
        ("deadline", 2, 2, "morning", "마감 D-2"),
        ("deadline", 5, 2, "morning", None),
        ("event", 4, 3, "morning", "이번 주 · D-4"),
        ("event", 4, 2, "morning", None),
        ("event", 1, 1, "evening", "내일"),
        ("event", 1, 2, "morning", None),
        ("event", -1, 3, "morning", None),
    ],
)
def test_reminder_reason_lead_times(kind, offset, importance, when, expected) -> None:
    item = _item(kind=kind, due=TODAY + timedelta(days=offset), importance=importance)
    got = reminder_reason(item, today=TODAY, when=when, fresh=False)
    assert (got[1] if got else None) == expected


def test_fresh_items_are_announced_once_unless_minor_or_past() -> None:
    assert reminder_reason(_item(kind="todo"), today=TODAY, when="evening",
                           fresh=True) == ("new", "새로 들어옴")
    assert reminder_reason(_item(kind="todo", importance=1), today=TODAY,
                           when="evening", fresh=True) is None
    past = _item(due=TODAY - timedelta(days=2))
    assert reminder_reason(past, today=TODAY, when="morning", fresh=True) is None


def test_select_skips_sent_slots_and_orders_by_date() -> None:
    fresh_start = KST_NOW - timedelta(hours=3)
    old_start = KST_NOW - timedelta(days=5)
    items = [
        _item(1, kind="todo", started=fresh_start, importance=3),
        _item(2, due=TODAY, time_="15:00", started=old_start),
        _item(3, due=TODAY, time_="09:00", started=old_start),
        _item(4, kind="todo", started=old_start, importance=3),  # backfill: never "new"
    ]
    picks = select(items, today=TODAY, when="morning", now=KST_NOW, sent=set())
    assert [p.item.id for p in picks] == [3, 2, 1]
    again = select(items, today=TODAY, when="morning", now=KST_NOW,
                   sent={(p.item.id, p.slot) for p in picks})
    assert again == []


# ---------------------------------------------------------------- storage


def _storage(tmp_path: Path) -> Storage:
    storage = Storage(tmp_path / "plaud.db")
    now = int(KST_NOW.timestamp())
    with storage._connect() as conn:
        for fid, hours_ago, summary in [
            ("rec-new", 3, "다음 주 월요일 2시 통화"),
            ("rec-old", 24 * 30, "오래된 녹음"),
            ("rec-empty", 2, ""),
        ]:
            start = int((KST_NOW - timedelta(hours=hours_ago)).timestamp() * 1000)
            conn.execute(
                "INSERT INTO files (id, filename, duration, start_time, synced_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (fid, f"{fid} title", 30 * 60000, start, now, now),
            )
            conn.execute(
                "INSERT INTO file_content (file_id, summary_md, fetched_at) VALUES (?, ?, ?)",
                (fid, summary, now),
            )
        conn.execute("INSERT INTO speakers (name, is_self, created_at) VALUES ('나', 1, ?)", (now,))
    return storage


def test_scan_reads_each_recent_summary_once(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    prompts: list[str] = []

    def fake_model(_model, prompt, **_kw):
        prompts.append(prompt)
        return json.dumps([
            {"kind": "event", "title": "평생교육원 통화", "date": "2026-11-23", "time": "14:00",
             "importance": 3, "evidence": "월요일 2시"},
            {"kind": "todo", "title": "자료 보내기", "importance": 2},
        ])

    report = reminders.scan(storage, days=14, now=KST_NOW, run_model=fake_model,
                            model_available=lambda _m: True)
    assert (report.scanned, report.items, report.failed) == (1, 2, 0)
    assert "나의 비서" in prompts[0] and "2026-11-20 (금)" in prompts[0]
    # Already read: the next run does nothing and never calls the model.
    again = reminders.scan(storage, days=14, now=KST_NOW, run_model=fake_model,
                           model_available=lambda _m: True)
    assert again.scanned == 0 and len(prompts) == 1


def test_scan_failure_is_retried_next_run(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    def broken(*_a, **_kw):
        raise ModelFailed("quota")

    report = reminders.scan(storage, now=KST_NOW, run_model=broken,
                            model_available=lambda _m: True)
    assert report.failed == 1 and report.items == 0
    retry = reminders.scan(storage, now=KST_NOW, run_model=lambda *_a, **_kw: "[]",
                           model_available=lambda _m: True)
    assert retry.scanned == 1


def test_due_then_ack_never_repeats(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    reminders.scan(storage, now=KST_NOW, model_available=lambda _m: True,
                   run_model=lambda *_a, **_kw: json.dumps([
                       {"kind": "event", "title": "내일 미팅", "date": "2026-11-21", "importance": 2},
                   ]))
    evening = reminders.due(storage, when="evening", now=KST_NOW.replace(hour=21))
    assert [(p.reason, p.item.title) for p in evening] == [("내일", "내일 미팅")]
    assert evening[0].to_json()["recording"]["link"] == "plaudnote://open?id=rec-new"
    reminders.ack(storage, [(p.item.id, p.slot) for p in evening])
    assert reminders.due(storage, when="evening", now=KST_NOW.replace(hour=21)) == []
    # Next morning it is due today: a new slot, sent once more.
    next_morning = KST_NOW + timedelta(days=1)
    assert [p.reason for p in reminders.due(storage, when="morning", now=next_morning)] == ["오늘"]


def test_dismissed_items_stay_quiet(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    reminders.scan(storage, now=KST_NOW, model_available=lambda _m: True,
                   run_model=lambda *_a, **_kw: json.dumps([
                       {"kind": "todo", "title": "자료 보내기", "importance": 3}]))
    [item] = reminders.load(storage)
    assert reminders.dismiss(storage, item.id)
    assert reminders.due(storage, when="morning", now=KST_NOW) == []


def test_day_recordings_lists_spans_and_links(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    rows = reminders.day_recordings(storage, KST_NOW.date())
    # Oldest first; a month-old recording is not on this day.
    assert [r["id"] for r in rows] == ["rec-new", "rec-empty"]
    assert rows[0]["minutes"] == 30 and rows[0]["link"] == "plaudnote://open?id=rec-new"
