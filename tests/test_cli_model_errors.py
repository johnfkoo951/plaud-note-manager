"""A failed model call ends a command with one stderr line, not a traceback."""

from __future__ import annotations

import json
import time

from typer.testing import CliRunner

import cli.main as cli
import core.metadata as metadata
from core import app_config, summarize
from core.models import FileContent, PlaudFile, SummaryBlock
from core.storage import Storage
from core.summarize import ModelFailed


def test_model_failure_is_one_line_without_traceback(tmp_path, monkeypatch) -> None:
    storage = Storage(tmp_path / "plaud.db")
    storage.upsert_file(PlaudFile(id="f", filename="회의"), now=1)
    storage.save_content(
        FileContent(file_id="f", summaries=[SummaryBlock(kind="auto_sum_note", body_md="회의")]),
        now=1,
    )
    monkeypatch.setattr(cli, "Storage", lambda: storage)
    monkeypatch.setattr(app_config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.delenv("PLAUD_OBSIDIAN_VAULT", raising=False)

    def quota(*_a, **_k):
        raise ModelFailed("codex CLI failed (exit 1): ERROR: [quota] usage limit\nretry later")

    monkeypatch.setattr(metadata, "generate_note_metadata", quota)
    result = CliRunner().invoke(cli.app, ["metadata-generate", "f", "--no-auto-folder"])
    assert result.exit_code == 1
    # Markup-looking vendor text is printed as is, on one line.
    assert result.stderr.strip() == (
        "model call failed: codex CLI failed (exit 1): ERROR: [quota] usage limit retry later"
    )
    assert "Traceback" not in result.output
    assert storage.get_note_metadata("f") is None


def test_metadata_auto_json_reports_the_stop(tmp_path, monkeypatch) -> None:
    storage = Storage(tmp_path / "plaud.db")
    for file_id in ("f1", "f2"):
        storage.upsert_file(PlaudFile(id=file_id, filename=f"{file_id}.m4a"), now=1)
        storage.save_content(
            FileContent(
                file_id=file_id, summaries=[SummaryBlock(kind="auto_sum_note", body_md="x")]
            ),
            now=int(time.time()),
        )
    monkeypatch.setattr(cli, "Storage", lambda: storage)
    monkeypatch.setattr(app_config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(summarize, "model_available", lambda _m: True)
    calls: list[str] = []

    def quota(_storage, file_id, **_k):
        calls.append(file_id)
        raise ModelFailed("codex CLI failed (exit 1): ERROR: usage limit")

    monkeypatch.setattr(metadata, "generate_note_metadata", quota)
    result = CliRunner().invoke(cli.app, ["metadata-auto", "--json", "--model", "codex"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert len(calls) == 1
    assert "usage limit" in report["stopped"]
    assert report["failed"] == {calls[0]: report["stopped"]}
    assert report["remaining"] == 1
