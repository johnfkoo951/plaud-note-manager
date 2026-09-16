import json
import time

from core.models import FileContent, Folder, PlaudFile, SummaryBlock, TranscriptSegment
from core.metadata import write_meeting_note
from core.storage import Storage
from core.tags import normalize_tag, normalize_tags


def test_storage_saves_content_and_folder_links(tmp_path) -> None:
    storage = Storage(tmp_path / "plaud.db")
    now = int(time.time())

    storage.replace_folders(
        [Folder(id="folder-1", name="Meetings", color="#4c8eff")],
        now=now,
    )
    storage.upsert_file(
        PlaudFile(id="file-1", filename="recording.m4a", duration=120_000),
        now=now,
    )
    storage.save_content(
        FileContent(
            file_id="file-1",
            title="Recording",
            transcript=[
                TranscriptSegment(
                    start_time=0,
                    end_time=1000,
                    speaker="speaker_0",
                    content="hello",
                )
            ],
            summaries=[SummaryBlock(kind="auto_sum_note", body_md="summary")],
            keywords=["test"],
            folder_ids=["folder-1"],
        ),
        now=now,
    )

    row = storage.get_content_row("file-1")

    assert row is not None
    assert row["title"] == "Recording"
    assert json.loads(row["keywords"]) == ["test"]
    with storage._connect() as conn:
        folder_rows = list(conn.execute("SELECT * FROM file_folders"))
    assert [(r["file_id"], r["folder_id"]) for r in folder_rows] == [("file-1", "folder-1")]


def test_note_metadata_and_tags_are_keyed_by_file_id(tmp_path) -> None:
    storage = Storage(tmp_path / "plaud.db")
    now = int(time.time())

    storage.upsert_note_metadata(
        file_id="file-1",
        title="Original Title",
        description="Stable metadata for a Plaud note.",
        note_type="meeting",
        status="inProgress",
        metadata={"stable_id": "file-1", "title": "Original Title"},
        now=now,
    )
    added = storage.add_note_tags(
        "file-1",
        ["#Meeting Minutes", "AI 요약", "AI 요약"],
        source="manual",
        now=now,
    )
    storage.upsert_note_reference(
        file_id="file-1",
        path=tmp_path / "note.md",
        kind="meeting-note",
        title="Original Title",
        now=now,
    )

    assert added == ["Meeting-Minutes", "AI-요약"]
    row = storage.get_note_metadata("file-1")
    tags = storage.list_note_tags("file-1")
    refs = storage.list_note_references("file-1")

    assert row is not None
    assert row["file_id"] == "file-1"
    assert row["note_type"] == "meeting"
    assert [t["tag"] for t in tags] == ["AI-요약", "Meeting-Minutes"]
    assert refs[0]["kind"] == "meeting-note"


def test_normalize_tag_removes_hashes_and_spaces() -> None:
    assert normalize_tag("#옵시디언 회의록") == "옵시디언-회의록"
    assert normalize_tag("  AI / PKM  ") == "AI-PKM"
    assert normalize_tags(["AI, 회의록"]) == ["AI", "회의록"]


def test_partial_metadata_update_preserves_lifecycle(tmp_path) -> None:
    storage = Storage(tmp_path / "plaud.db")
    storage.upsert_note_metadata(
        file_id="f", status="done", usage_status="vault-linked", title="Before", now=1
    )
    storage.upsert_note_metadata(file_id="f", title="After", now=2)
    row = storage.get_note_metadata("f")
    assert (row["status"], row["usage_status"]) == ("done", "vault-linked")
    assert row["title"] == "After"
    storage.upsert_note_metadata(file_id="new", title="New", now=2)
    row = storage.get_note_metadata("new")
    assert (row["status"], row["usage_status"]) == ("unread", "unused")


def test_note_folder_can_be_cleared(tmp_path) -> None:
    storage = Storage(tmp_path / "plaud.db")
    storage.upsert_note_metadata(file_id="f", folder_id="a", folder_name="A", now=1)
    storage.update_note_folder("f", folder_id=None, folder_name=None, now=2)
    row = storage.get_note_metadata("f")
    assert row["folder_id"] is None and row["folder_name"] is None


def test_batch_sync_keeps_local_state_and_updates_cloud_fields(tmp_path) -> None:
    storage = Storage(tmp_path / "plaud.db")
    storage.upsert_file(PlaudFile(id="f", filename="Before"), now=1)
    storage.mark_seen("f", now=2)
    storage.set_starred("f", True)
    storage.mark_downloaded("f", tmp_path / "audio.mp3", now=2)
    storage.upsert_files(
        [PlaudFile(id="f", filename="After"), PlaudFile(id="g", filename="New")],
        now=3,
    )
    row = storage.get_file_row("f")
    assert row["filename"] == "After"
    assert (row["seen_at"], row["starred"], row["status"]) == (2, 1, "downloaded")
    assert storage.get_file_row("g")["filename"] == "New"


def test_regenerating_metadata_keeps_completed_lifecycle(tmp_path, monkeypatch) -> None:
    from core import metadata, vault_index

    storage = Storage(tmp_path / "plaud.db")
    storage.upsert_file(PlaudFile(id="f", filename="회의 녹음"), now=1)
    storage.save_content(
        FileContent(file_id="f", summaries=[SummaryBlock(kind="auto_sum_note", body_md="회의")]),
        now=1,
    )
    storage.upsert_note_metadata(file_id="f", status="done", usage_status="archived", now=1)
    monkeypatch.setattr(metadata, "integrated_dir", lambda _: tmp_path)
    monkeypatch.setattr(vault_index, "related_wikilinks", lambda _: [])
    result = metadata.generate_note_metadata(storage, "f", vault_path=tmp_path, use_ai=False)
    assert result["status"] == "done" and result["usage_status"] == "archived"
    row = storage.get_note_metadata("f")
    assert row["status"] == "done" and row["usage_status"] == "archived"


def test_write_meeting_note_fallback_records_reference(tmp_path) -> None:
    storage = Storage(tmp_path / "plaud.db")
    now = int(time.time())
    storage.upsert_file(
        PlaudFile(id="file-1", filename="회의.m4a", start_time=1777520000000),
        now=now,
    )
    storage.save_content(
        FileContent(
            file_id="file-1",
            title="테스트 회의",
            transcript=[
                TranscriptSegment(
                    start_time=0,
                    end_time=1000,
                    speaker="Me",
                    content="다음 액션을 정리합시다.",
                )
            ],
            summaries=[SummaryBlock(kind="auto_sum_note", body_md="요약")],
            keywords=["테스트 회의"],
        ),
        now=now,
    )

    out = write_meeting_note(
        storage,
        "file-1",
        vault_path=tmp_path,
        out_path=tmp_path / "meeting.md",
        use_ai=False,
    )

    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "plaud_id: file-1" in text
    refs = storage.list_note_references("file-1")
    assert refs[0]["path"] == str(out)
