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


def _auto_meta(storage: Storage, file_id: str) -> dict:
    return json.loads(storage.get_note_metadata(file_id)["metadata_json"])["auto_meta"]


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch):
    """`generate` raising what `calls["raise"]` maps the file id to, if anything."""
    from core import metadata, summarize

    state: dict = {"seen": [], "raise": {}}

    def generate(storage: Storage, file_id: str, **kwargs):
        state["seen"].append(file_id)
        exc = state["raise"].get(file_id) or state["raise"].get("*")
        if exc is not None:
            raise exc
        return fake_generate(storage, file_id, **kwargs)

    monkeypatch.setattr(summarize, "model_available", lambda model: True)
    monkeypatch.setattr(metadata, "generate_note_metadata", generate)
    return state


def test_failure_records_reason_for_the_app(storage: Storage, calls) -> None:
    from core.summarize import ModelFailed

    calls["raise"]["*"] = ModelFailed("codex CLI failed (exit 1): ERROR: usage limit\nmore")
    add_recording(storage, "f1")
    report = am.run_auto_metadata(storage, model="codex", limit=5)
    info = _auto_meta(storage, "f1")
    assert "usage limit" in info["last_error"] and "\n" not in info["last_error"]
    assert isinstance(info["last_error_at"], int)
    assert info["attempts"] == 1
    assert report.failed["f1"] == info["last_error"]


def test_provider_failure_stops_the_batch(storage: Storage, calls) -> None:
    from core.summarize import ModelFailed

    calls["raise"]["*"] = ModelFailed("codex CLI failed (exit 1): ERROR: usage limit")
    for i in range(3):
        add_recording(storage, f"f{i}")
    report = am.run_auto_metadata(storage, model="codex", limit=5)
    assert len(calls["seen"]) == 1
    assert len(report.failed) == 1
    assert "usage limit" in report.stopped
    assert report.remaining == 2
    assert "stopped: " in report.summary()
    untouched = [f for f in ("f0", "f1", "f2") if f not in calls["seen"]]
    assert [storage.get_note_metadata(f) for f in untouched] == [None, None]


def test_unusable_reply_does_not_stop_the_batch(storage: Storage, calls) -> None:
    from core.metadata import MetadataUnavailable

    for i in range(3):
        add_recording(storage, f"f{i}")
    first = storage.files_for_auto_metadata(fetched_after=None)[0]["file_id"]
    calls["raise"][first] = MetadataUnavailable("model reply contained no usable metadata JSON")
    report = am.run_auto_metadata(storage, model="codex", limit=5)
    assert list(report.failed) == [first]
    assert len(report.generated) == 2
    assert report.stopped == ""


def test_success_clears_the_recorded_failure(storage: Storage, calls) -> None:
    from core.summarize import ModelFailed

    add_recording(storage, "f1")
    calls["raise"]["*"] = ModelFailed("codex CLI timed out after 600s")
    am.run_auto_metadata(storage, model="codex", limit=5)
    assert _auto_meta(storage, "f1")["last_error"]
    # Age the failure past the backoff window, then succeed.
    info = {**_auto_meta(storage, "f1"), "last_attempt": NOW - 2 * 86400}
    storage.upsert_note_metadata(file_id="f1", metadata={"auto_meta": info}, generated_at=0, now=0)
    calls["raise"].clear()
    report = am.run_auto_metadata(storage, model="codex", limit=5)
    assert report.generated == ["f1"]
    info = _auto_meta(storage, "f1")
    assert "last_error" not in info and "last_error_at" not in info
    assert info["source_hash"]


def test_a_reply_without_json_is_not_cached_with_a_source_hash(
    storage: Storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real generate_note_metadata against a model that answers in prose:
    # the reply used to be stored as source 'ai' with the source hash, so the
    # file was never retried.
    from core import app_config, metadata, summarize, vault_index

    monkeypatch.setattr(app_config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.delenv("PLAUD_OBSIDIAN_VAULT", raising=False)
    monkeypatch.setattr(metadata, "integrated_dir", lambda _: tmp_path / "integrated")
    monkeypatch.setattr(metadata, "load_vault_context", lambda _: "")
    monkeypatch.setattr(vault_index, "related_wikilinks", lambda _: [])
    monkeypatch.setattr(summarize, "model_available", lambda model: True)
    monkeypatch.setattr(summarize, "run_model", lambda *a, **k: "Sorry, I can't do that.")
    add_recording(storage, "f1")
    report = am.run_auto_metadata(storage, model="codex", limit=5)
    assert "no usable metadata JSON" in report.failed["f1"]
    assert report.stopped == ""
    stored = json.loads(storage.get_note_metadata("f1")["metadata_json"])
    assert "source_hash" not in stored["auto_meta"]
    assert "no usable metadata JSON" in stored["auto_meta"]["last_error"]
    assert "title" not in stored  # nothing from the reply was stored as metadata


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
