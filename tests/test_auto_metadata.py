from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from core import auto_metadata as am
from core.models import FileContent, PlaudFile, SummaryBlock
from core.storage import Storage

NOW = int(time.time())


@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "plaud.db")


def add_recording(
    storage: Storage,
    file_id: str,
    *,
    summary: str = "summary body",
    fetched_at: int = NOW,
) -> None:
    storage.upsert_file(PlaudFile(id=file_id, filename=f"{file_id}.m4a"), now=NOW)
    storage.save_content(
        FileContent(
            file_id=file_id,
            title=file_id,
            summaries=[SummaryBlock(kind="auto_sum_note", body_md=summary)],
        ),
        now=fetched_at,
    )


def fake_generate(storage: Storage, file_id: str, *, model: str = "", **kwargs):
    merged = {"file_id": file_id, "title": file_id, "model": model}
    storage.upsert_note_metadata(
        file_id=file_id, metadata=merged, generated_at=int(time.time()), now=int(time.time())
    )
    return merged


@pytest.fixture()
def ai_ok(monkeypatch: pytest.MonkeyPatch):
    from core import metadata, summarize

    monkeypatch.setattr(summarize, "model_available", lambda model: True)
    monkeypatch.setattr(metadata, "generate_note_metadata", fake_generate)


def test_generates_for_eligible_and_stores_hash(storage: Storage, ai_ok) -> None:
    add_recording(storage, "f1")
    report = am.run_auto_metadata(storage, model="codex")
    assert report.generated == ["f1"]
    row = storage.get_note_metadata("f1")
    info = json.loads(row["metadata_json"])["auto_meta"]
    assert info["source_hash"]

    # Second run: generated_at >= fetched_at, so not even a candidate.
    report2 = am.run_auto_metadata(storage, model="codex")
    assert report2.generated == []
    assert report2.unchanged == []

    # Content refetched later with identical text: candidate again, but the
    # source hash matches -> skipped and generated_at bumped (not regenerated).
    add_recording(storage, "f1", fetched_at=NOW + 3600)
    report3 = am.run_auto_metadata(storage, model="codex")
    assert report3.generated == []
    assert report3.unchanged == ["f1"]
    report4 = am.run_auto_metadata(storage, model="codex")
    assert report4.unchanged == []


def test_recency_window_excludes_old_backlog(storage: Storage, ai_ok) -> None:
    add_recording(storage, "old", fetched_at=NOW - 30 * 86400)
    add_recording(storage, "new", fetched_at=NOW)
    report = am.run_auto_metadata(storage, model="codex", since_days=7)
    assert report.generated == ["new"]
    backfill = am.run_auto_metadata(storage, model="codex", backfill=True)
    assert backfill.generated == ["old"]


def test_limit_reports_remaining(storage: Storage, ai_ok) -> None:
    for i in range(3):
        add_recording(storage, f"f{i}")
    report = am.run_auto_metadata(storage, model="codex", limit=2)
    assert len(report.generated) == 2
    assert report.remaining == 1


def test_failure_records_backoff(storage: Storage, monkeypatch: pytest.MonkeyPatch) -> None:
    from core import metadata, summarize

    monkeypatch.setattr(summarize, "model_available", lambda model: True)

    def boom(*args, **kwargs):
        raise RuntimeError("provider timeout")

    monkeypatch.setattr(metadata, "generate_note_metadata", boom)
    add_recording(storage, "f1")
    report = am.run_auto_metadata(storage, model="codex")
    assert "f1" in report.failed

    row = storage.get_note_metadata("f1")
    info = json.loads(row["metadata_json"])["auto_meta"]
    assert info["attempts"] == 1

    # Immediately after a failure the file is in backoff, not retried.
    report2 = am.run_auto_metadata(storage, model="codex")
    assert report2.backed_off == ["f1"]
    assert report2.failed == {}


def test_model_unavailable_aborts_without_fallback_spam(
    storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core import summarize

    monkeypatch.setattr(summarize, "model_available", lambda model: False)
    add_recording(storage, "f1")
    report = am.run_auto_metadata(storage, model="codex")
    assert report.aborted
    assert storage.get_note_metadata("f1") is None


def test_dry_run_changes_nothing(storage: Storage, ai_ok) -> None:
    add_recording(storage, "f1")
    report = am.run_auto_metadata(storage, model="codex", dry_run=True)
    assert report.generated == ["f1"]
    assert storage.get_note_metadata("f1") is None
