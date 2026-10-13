"""generate_note_metadata: an unusable reply fails loudly and writes nothing."""

from __future__ import annotations

import json

import pytest

from core import app_config, auto_folder, metadata, summarize, vault_index
from core.classification import classify_snapshot
from core.models import FileContent, PlaudFile, SummaryBlock
from core.storage import Storage


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    store = Storage(tmp_path / "plaud.db")
    store.upsert_file(PlaudFile(id="f", filename="회의 녹음"), now=1)
    store.save_content(
        FileContent(file_id="f", summaries=[SummaryBlock(kind="auto_sum_note", body_md="회의")]),
        now=1,
    )
    monkeypatch.setattr(app_config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.delenv("PLAUD_OBSIDIAN_VAULT", raising=False)
    monkeypatch.setattr(metadata, "integrated_dir", lambda _: tmp_path / "integrated")
    monkeypatch.setattr(metadata, "load_vault_context", lambda _: "")
    monkeypatch.setattr(vault_index, "related_wikilinks", lambda _: [])
    monkeypatch.setattr(summarize, "model_available", lambda _m: True)
    return store


def test_reply_without_json_raises_and_writes_nothing(storage, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(summarize, "run_model", lambda *a, **k: "I cannot help with that.")
    with pytest.raises(metadata.MetadataUnavailable, match="no usable metadata JSON"):
        metadata.generate_note_metadata(storage, "f", vault_path=tmp_path)
    assert storage.get_note_metadata("f") is None
    assert isinstance(metadata.MetadataUnavailable("x"), summarize.ModelCallError)
    assert not summarize.is_provider_failure(metadata.MetadataUnavailable("x"))


def test_an_empty_json_object_is_not_metadata(storage, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(summarize, "run_model", lambda *a, **k: "```json\n{}\n```")
    with pytest.raises(metadata.MetadataUnavailable):
        metadata.generate_note_metadata(storage, "f", vault_path=tmp_path)
    assert storage.get_note_metadata("f") is None


def test_classification_llm_outcome_is_stored(storage, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(summarize, "run_model", lambda *a, **k: '{"title": "주간 회의"}')

    def assisted(snapshot, **_k):
        return auto_folder.AssistedClassification(
            classification=classify_snapshot(snapshot),
            llm_status="failed",
            llm_error="x",
        )

    monkeypatch.setattr(auto_folder, "classify_with_assist", assisted)
    result = metadata.generate_note_metadata(storage, "f", vault_path=tmp_path)
    stored = json.loads(storage.get_note_metadata("f")["metadata_json"])
    assert (result["classification_llm_status"], result["classification_llm_error"]) == (
        "failed",
        "x",
    )
    assert stored["classification_llm_status"] == "failed"
    assert stored["classification_llm_error"] == "x"
