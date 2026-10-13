"""classify --llm: a failing provider is called once, and every row says why."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import cli.main as cli
import core.metadata as metadata
import core.paths as paths
from core import app_config, summarize
from core.classification import classify_snapshot
from core.models import Folder, PlaudFile
from core.storage import Storage

# A date-only title: the rule verdict is the weak default bucket, so the LLM is asked.
WEAK = {"title": "2026-05-29 14:30:00", "keywords": [], "summaries": "", "integrated_summary": ""}


@pytest.fixture()
def classify_env(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "plaud.db")
    for file_id in ("a", "b"):
        storage.upsert_file(PlaudFile(id=file_id, filename=f"{file_id}.m4a"), now=1)
    monkeypatch.setattr(cli, "Storage", lambda: storage)
    monkeypatch.setattr(cli, "load_config", lambda: None)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(metadata, "build_recording_snapshot", lambda *_: dict(WEAK))
    monkeypatch.setattr(summarize, "model_available", lambda _m: True)
    calls: list[str] = []

    def quota(*_a, **_k):
        calls.append("call")
        raise summarize.ModelFailed("codex CLI failed (exit 1): ERROR: usage limit")

    monkeypatch.setattr(summarize, "run_model", quota)
    return storage, calls


def test_classify_json_reports_llm_failure_once_then_skips(classify_env) -> None:
    storage, calls = classify_env
    result = CliRunner().invoke(cli.app, ["classify", "--json", "--llm"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [r["llm_status"] for r in rows] == ["failed", "skipped"]
    assert "usage limit" in rows[0]["llm_error"]
    assert "usage limit" in rows[1]["llm_error"] and "not attempted" in rows[1]["llm_error"]
    assert calls == ["call"]
    assert [r["moved_to"] for r in rows] == ["", ""]
    assert storage.get_note_metadata("a") is None and storage.get_note_metadata("b") is None


def test_classify_text_mode_names_the_failure_once(classify_env) -> None:
    result = CliRunner().invoke(cli.app, ["classify", "--llm"])
    assert result.exit_code == 0, result.output
    assert "llm failed" in result.stdout and "llm skipped" in result.stdout
    notice = [ln for ln in result.stdout.splitlines() if ln.startswith("LLM assist failed")]
    assert len(notice) == 1
    assert "2 recordings" in notice[0] and "usage limit" in notice[0]


def test_classify_apply_stores_the_llm_outcome(classify_env, monkeypatch) -> None:
    storage, calls = classify_env
    default_folder = classify_snapshot(dict(WEAK)).folder_name
    storage.replace_folders([Folder(id="inbox", name=default_folder)], now=1)

    class Client:
        def __init__(self, _cfg):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def set_file_folders(self, file_id, folder_ids):
            pass

    monkeypatch.setattr(cli, "PlaudClient", Client)
    result = CliRunner().invoke(
        cli.app, ["classify", "--apply", "--llm", "--only", "a", "--min-confidence", "0"]
    )
    assert result.exit_code == 0, result.output
    stored = json.loads(storage.get_note_metadata("a")["metadata_json"])
    assert stored["classification_llm_status"] == "failed"
    assert "usage limit" in stored["classification_llm_error"]
    assert calls == ["call"]
