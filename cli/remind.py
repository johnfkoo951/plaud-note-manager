"""`plaud remind …` and `plaud day-recordings` — the OmniControl reminder lane.

OmniControl's scheduled jobs call these: `scan` every half hour (new
recordings only), `due` + `ack` at 07:30 and 21:00, `day-recordings` before
dawn to link yesterday's calendar events to their recordings.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from typing import Any

import typer

from core import reminders
from core.storage import Storage

remind_app = typer.Typer(
    no_args_is_help=True,
    help="Reminders mined from recording summaries (OmniControl schedules these).",
)


def _emit(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")


def _parse_day(value: str) -> date:
    today = datetime.now().astimezone().date()
    if value == "today":
        return today
    if value == "yesterday":
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(f"YYYY-MM-DD expected: {value}") from None


@remind_app.command("scan")
def scan_cmd(
    days: int = typer.Option(14, help="Only recordings from the last N days."),
    limit: int = typer.Option(20, help="Read at most N recordings this run (newest first)."),
    model: str = typer.Option("", help="Model provider (claude, codex, …). Empty = metadata model."),
    model_id: str = typer.Option("", "--model-id", help="Exact model id, e.g. claude-sonnet-5-5."),
    force: bool = typer.Option(False, "--force", help="Read recordings again even if already read."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Read summaries of new recordings once and store what to remember."""
    report = reminders.scan(Storage(), days=days, limit=limit, model=model,
                            model_id=model_id, force=force)
    if json_out:
        _emit(reminders.report_json(report))
        return
    if report.skipped_no_model:
        typer.echo(f"model unavailable — {report.remaining} recording(s) wait")
        raise typer.Exit(2)
    typer.echo(f"read {report.scanned}, items {report.items}, failed {report.failed}, "
               f"waiting {report.remaining}")


@remind_app.command("due")
def due_cmd(
    when: str = typer.Option(..., help="morning (today, this week, new) or evening (tomorrow, new)."),
    on: str = typer.Option("", "--date", help="Pretend today is YYYY-MM-DD (testing)."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """What to send now. Prints only — `remind ack` records what went out."""
    if when not in ("morning", "evening"):
        raise typer.BadParameter("--when must be morning or evening")
    today = _parse_day(on) if on else None
    picks = reminders.due(Storage(), when=when, today=today)
    if json_out:
        _emit({"when": when, "date": (today or datetime.now().astimezone().date()).isoformat(),
               "items": [p.to_json() for p in picks]})
        return
    if not picks:
        typer.echo("nothing due")
        return
    for p in picks:
        it = p.item
        when_txt = f"{it.due_date} {it.due_time or ''}".strip() if it.due_date else "날짜 없음"
        typer.echo(f"[{p.reason}] #{it.id} {it.title} ({when_txt}) — {it.recording_title}")


@remind_app.command("ack")
def ack_cmd(
    pairs: list[str] = typer.Argument(..., help="ID:SLOT pairs from `due --json` (e.g. 12:new)."),
) -> None:
    """Record that items went out in a slot, so they are not sent again."""
    parsed: list[tuple[int, str]] = []
    for raw in pairs:
        rid, sep, slot = raw.partition(":")
        if not sep or not rid.isdigit() or not slot:
            raise typer.BadParameter(f"ID:SLOT expected: {raw}")
        parsed.append((int(rid), slot))
    added = reminders.ack(Storage(), parsed)
    typer.echo(f"recorded {added}")


@remind_app.command("list")
def list_cmd(
    all_items: bool = typer.Option(False, "--all", help="Include past and dismissed items."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Stored reminder items (upcoming and undated by default)."""
    today = datetime.now().astimezone().date()
    items = reminders.load(Storage(), include_dismissed=all_items,
                           from_date=None if all_items else today)
    if json_out:
        _emit([it.to_json() for it in items])
        return
    for it in items:
        when_txt = f"{it.due_date} {it.due_time or ''}".strip() if it.due_date else "—"
        typer.echo(f"#{it.id:<4} {reminders.KIND_LABELS[it.kind]:<5} {'★' * it.importance:<3} "
                   f"{when_txt:<16} {it.title}  ({it.recording_title})")


@remind_app.command("dismiss")
def dismiss_cmd(reminder_id: int) -> None:
    """Stop reminding an item (done, cancelled, or wrong)."""
    if not reminders.dismiss(Storage(), reminder_id):
        typer.echo(f"no open item #{reminder_id}")
        raise typer.Exit(1)
    typer.echo(f"dismissed #{reminder_id}")


def day_recordings_cmd(
    on: str = typer.Option(..., "--date", help="YYYY-MM-DD, today or yesterday (local time)."),
    json_out: bool = typer.Option(True, "--json/--text"),
) -> None:
    """Recordings that started on a day, with their span and plaudnote:// link."""
    day = _parse_day(on)
    rows = reminders.day_recordings(Storage(), day)
    if json_out:
        _emit({"date": day.isoformat(), "recordings": rows})
        return
    for r in rows:
        typer.echo(f"{r['start'][11:16]}–{r['end'][11:16]}  {r['title']}  {r['link']}")
