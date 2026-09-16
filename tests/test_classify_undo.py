import json

from typer.testing import CliRunner

import cli.main as cli
import core.metadata as metadata
import core.paths as paths
from core.classify_history import capture_state
from core.models import Folder, PlaudFile
from core.storage import Storage


def _setup(tmp_path, monkeypatch, *, existing_metadata=True):
    storage = Storage(tmp_path / "test.db")
    storage.upsert_file(PlaudFile(id="f", filename="강의 녹음"), now=1)
    storage.replace_folders(
        [Folder(id="old", name="Old"), Folder(id="new", name="11. AI 강의")], now=1
    )
    storage.set_file_folders("f", ["old"])
    if existing_metadata:
        storage.upsert_note_metadata(
            file_id="f",
            title="Before",
            note_type="meeting",
            status="done",
            usage_status="vault-linked",
            folder_id="old",
            folder_name="Old",
            metadata={"attendees": ["Person"], "related": ["[[Existing]]"]},
            now=1,
        )
    storage.add_note_tags("f", ["Manual"], source="manual", now=1)
    storage.add_note_tags("f", ["Generated"], source="ai", now=1)
    monkeypatch.setattr(cli, "Storage", lambda: storage)
    monkeypatch.setattr(cli, "load_config", lambda: None)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        metadata,
        "build_recording_snapshot",
        lambda *_: {"title": "강의 녹음", "keywords": []},
    )
    calls = []

    class Client:
        fail = False

        def __init__(self, _cfg):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def set_file_folders(self, file_id, folder_ids):
            if self.fail:
                raise RuntimeError("network unavailable")
            calls.append((file_id, folder_ids))

    monkeypatch.setattr(cli, "PlaudClient", Client)
    return storage, Client, calls


def _apply(file_id="f", *, append=False):
    return CliRunner().invoke(
        cli.app,
        [
            "classify",
            "--apply",
            "--include-filed",
            "--only",
            file_id,
            "--folder",
            "11. AI 강의",
            "--no-llm",
            "--json",
            *(["--append-undo"] if append else []),
        ],
    )


def test_classify_preserves_metadata_and_undo_restores_original_folder(tmp_path, monkeypatch):
    storage, _, calls = _setup(tmp_path, monkeypatch)
    before = capture_state(storage, "f")
    result = _apply()
    assert result.exit_code == 0, result.output
    row = storage.get_note_metadata("f")
    assert row["usage_status"] == "vault-linked" and row["status"] == "done"
    assert json.loads(row["metadata_json"])["attendees"] == ["Person"]
    result = CliRunner().invoke(cli.app, ["classify-undo", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["reverted"] == 1
    assert capture_state(storage, "f") == before
    assert calls == [("f", ["new"]), ("f", ["old"])]
    assert not (tmp_path / "last_classify.json").exists()


def test_undo_restores_absent_metadata(tmp_path, monkeypatch):
    storage, _, _ = _setup(tmp_path, monkeypatch, existing_metadata=False)
    assert _apply().exit_code == 0
    assert CliRunner().invoke(cli.app, ["classify-undo"]).exit_code == 0
    assert storage.get_note_metadata("f") is None


def test_failed_undo_preserves_manifest_for_retry(tmp_path, monkeypatch):
    storage, client, _ = _setup(tmp_path, monkeypatch)
    before = capture_state(storage, "f")
    assert _apply().exit_code == 0
    client.fail = True
    result = CliRunner().invoke(cli.app, ["classify-undo", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "partial"
    assert (tmp_path / "last_classify.json").exists()
    client.fail = False
    assert CliRunner().invoke(cli.app, ["classify-undo"]).exit_code == 0
    assert capture_state(storage, "f") == before


def test_undo_does_not_overwrite_new_manual_edits(tmp_path, monkeypatch):
    storage, _, calls = _setup(tmp_path, monkeypatch)
    assert _apply().exit_code == 0
    storage.add_note_tags("f", ["Added-after-classify"], source="manual", now=9)
    result = CliRunner().invoke(cli.app, ["classify-undo", "--json"])
    assert result.exit_code == 1
    assert calls == [("f", ["new"])]
    assert "changed since classification" in result.stdout
    assert (tmp_path / "last_classify.json").exists()


def test_legacy_undo_clears_folder_with_required_timestamp(tmp_path, monkeypatch):
    storage, _, _ = _setup(tmp_path, monkeypatch)
    (tmp_path / "last_classify.json").write_text(json.dumps({"moved": [{"file_id": "f"}]}))
    result = CliRunner().invoke(cli.app, ["classify-undo", "--json"])
    assert result.exit_code == 0, result.output
    row = storage.get_note_metadata("f")
    assert row["folder_id"] is None and row["folder_name"] is None
    assert row["usage_status"] == "vault-linked"


def test_app_multiple_apply_groups_share_one_undo(tmp_path, monkeypatch):
    storage, _, calls = _setup(tmp_path, monkeypatch)
    original = capture_state(storage, "f")
    assert _apply().exit_code == 0
    storage.upsert_file(PlaudFile(id="g", filename="Second"), now=1)
    assert _apply("g", append=True).exit_code == 0
    manifest = json.loads((tmp_path / "last_classify.json").read_text())
    assert [entry["file_id"] for entry in manifest["moved"]] == ["f", "g"]
    assert CliRunner().invoke(cli.app, ["classify-undo"]).exit_code == 0
    assert capture_state(storage, "f") == original
    assert storage.get_note_metadata("g") is None
    assert calls[-2:] == [("f", ["old"]), ("g", [])]


def test_append_same_recording_keeps_first_snapshot(tmp_path, monkeypatch):
    storage, _, _ = _setup(tmp_path, monkeypatch)
    original = capture_state(storage, "f")
    assert _apply().exit_code == 0
    assert _apply(append=True).exit_code == 0
    manifest = json.loads((tmp_path / "last_classify.json").read_text())
    assert len(manifest["moved"]) == 1
    assert manifest["moved"][0]["before"] == original
    assert CliRunner().invoke(cli.app, ["classify-undo"]).exit_code == 0
    assert capture_state(storage, "f") == original
