"""Empty Plaud results must not be cached as final (the 'no content' bug)."""

from pathlib import Path

from core.models import FileContent, PlaudFile, SummaryBlock, TranscriptSegment
from core.storage import CONTENT_EMPTY_RETRY_SECONDS, CONTENT_FAILURE_RETRY_SECONDS, Storage


def _content(file_id: str, *, transcript=False, summary=False, folders=None) -> FileContent:
    return FileContent(
        file_id=file_id,
        title="t",
        transcript=(
            [TranscriptSegment(start_time=0, end_time=1, content="hi")] if transcript else []
        ),
        outline=[],
        summaries=([SummaryBlock(kind="auto_sum_note", body_md="s")] if summary else []),
        keywords=[],
        folder_ids=folders or [],
    )


def test_is_empty_property() -> None:
    assert _content("a").is_empty is True
    assert _content("a", transcript=True).is_empty is False
    assert _content("a", summary=True).is_empty is False


def test_save_content_skips_empty_but_keeps_folders(tmp_path: Path) -> None:
    storage = Storage(db_path=tmp_path / "t.db")
    storage.save_content(_content("a", folders=["f1"]), now=1)
    # No content row was written (empty result not cached)…
    assert storage.get_content_row("a") is None
    assert "a" not in storage.cached_file_ids()
    # …but the folder assignment still synced.
    with storage._connect() as conn:
        folders = [
            r[0] for r in conn.execute("SELECT folder_id FROM file_folders WHERE file_id='a'")
        ]
    assert folders == ["f1"]


def test_save_content_persists_nonempty(tmp_path: Path) -> None:
    storage = Storage(db_path=tmp_path / "t.db")
    storage.save_content(_content("a", transcript=True), now=1)
    assert storage.get_content_row("a") is not None
    assert "a" in storage.cached_file_ids()


def test_delete_empty_content_clears_stale_rows(tmp_path: Path) -> None:
    storage = Storage(db_path=tmp_path / "t.db")
    # Force an empty row in the way the old buggy backfill did (direct insert).
    with storage._connect() as conn:
        conn.execute(
            "INSERT INTO file_content (file_id, title, transcript, outline, summary_md, "
            "summary_extra, keywords, fetched_at) VALUES ('stale','t','[]','[]',NULL,'[]','[]',1)"
        )
        conn.execute(
            "INSERT INTO file_content (file_id, title, transcript, outline, summary_md, "
            "summary_extra, keywords, fetched_at) VALUES ('good','t','[{\"x\":1}]','[]','sum','[]','[]',1)"
        )
    cleared = storage.delete_empty_content()
    assert cleared == ["stale"]
    assert storage.get_content_row("stale") is None
    assert storage.get_content_row("good") is not None


def test_empty_attempt_is_persistent_but_expires_and_revision_change_bypasses_it(tmp_path):
    path = tmp_path / "t.db"
    storage = Storage(path)
    storage.upsert_file(PlaudFile(id="a", edit_time=10), now=1)
    storage.record_content_fetch_attempt("a", source_edit_time=10, outcome="empty", now=100)

    reopened = Storage(path)
    assert reopened.files_without_content(now=101) == []
    assert [r["id"] for r in reopened.files_without_content(now=101, include_deferred=True)] == [
        "a"
    ]
    assert [
        r["id"] for r in reopened.files_without_content(now=100 + CONTENT_EMPTY_RETRY_SECONDS)
    ] == ["a"]
    assert reopened.get_content_row("a") is None
    reopened.upsert_file(PlaudFile(id="a", edit_time=11), now=102)
    assert [r["id"] for r in reopened.files_without_content(now=102)] == ["a"]


def test_failure_backoff_with_null_revision_and_success_clears_attempt(tmp_path):
    storage = Storage(tmp_path / "t.db")
    storage.upsert_file(PlaudFile(id="a"), now=1)
    storage.record_content_fetch_attempt("a", source_edit_time=None, outcome="failed", now=100)
    assert storage.files_without_content(now=101) == []
    assert [
        r["id"] for r in storage.files_without_content(now=100 + CONTENT_FAILURE_RETRY_SECONDS)
    ] == ["a"]
    storage.save_content(_content("a", summary=True, folders=["f1"]), now=102)
    # Even a late failed request cannot reintroduce a retry for cached content.
    storage.record_content_fetch_attempt("a", source_edit_time=None, outcome="failed", now=103)
    assert storage.files_without_content(now=104, include_deferred=True) == []
    with storage._connect() as conn:
        assert conn.execute("SELECT count(*) FROM content_fetch_attempts").fetchone()[0] == 0
    assert storage.get_content_row("a")["summary_md"] == "s"


def test_stale_attempt_does_not_defer_new_revision_or_change_existing_content(tmp_path):
    storage = Storage(tmp_path / "t.db")
    storage.upsert_file(PlaudFile(id="a", edit_time=11), now=1)
    storage.record_content_fetch_attempt("a", source_edit_time=10, outcome="empty", now=100)
    assert [r["id"] for r in storage.files_without_content(now=101)] == ["a"]
    storage.save_content(_content("a", transcript=True, folders=["f1"]), now=102)
    before = dict(storage.get_content_row("a"))
    storage.save_content(_content("a", folders=["f1"]), now=103)
    assert dict(storage.get_content_row("a")) == before


def test_v3_migration_adds_attempt_table_and_preserves_source_and_folder_links(tmp_path):
    path = tmp_path / "t.db"
    storage = Storage(path)
    storage.upsert_file(PlaudFile(id="a", edit_time=10), now=1)
    storage.save_content(_content("a", transcript=True, folders=["f1"]), now=2)
    before = dict(storage.get_content_row("a"))
    with storage._connect() as conn:
        conn.execute("DROP TABLE content_fetch_attempts")
        conn.execute("PRAGMA user_version = 3")
    reopened = Storage(path)
    assert dict(reopened.get_content_row("a")) == before
    with reopened._connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute("SELECT count(*) FROM content_fetch_attempts").fetchone()[0] == 0
        assert tuple(conn.execute("SELECT file_id, folder_id FROM file_folders").fetchone()) == (
            "a",
            "f1",
        )
