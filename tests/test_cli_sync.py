from typer.testing import CliRunner

import cli.main as cli
from core.models import FileContent, FileListPage, PlaudFile, SummaryBlock
from core.storage import Storage


def _client(monkeypatch, storage, client_type):
    class Client(client_type):
        def __init__(self, _cfg):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(cli, "PlaudClient", Client)
    monkeypatch.setattr(cli, "Storage", lambda: storage)
    monkeypatch.setattr(cli, "load_config", lambda: None)
    monkeypatch.setattr(cli, "_maybe_auto_metadata", lambda _storage: None)


def test_sync_paginates_even_when_server_caps_page_size(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "test.db")
    requests = []

    class Client:
        def list_folders(self):
            return []

        def list_files(self, *, limit, skip, is_trash):
            requests.append((skip, is_trash))
            if is_trash:
                return FileListPage()
            return FileListPage(total=3, items=[PlaudFile(id=str(skip))])

    _client(monkeypatch, storage, Client)
    result = CliRunner().invoke(cli.app, ["sync"])
    assert result.exit_code == 0, result.output
    assert storage.active_file_ids() == {"0", "1", "2"}
    assert requests == [(0, 0), (1, 0), (2, 0), (0, 1)]


def test_sync_repeated_page_fails_instead_of_looping(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "test.db")

    class Client:
        def list_folders(self):
            return []

        def list_files(self, **_kwargs):
            return FileListPage(total=3, items=[PlaudFile(id="same")])

    _client(monkeypatch, storage, Client)
    result = CliRunner().invoke(cli.app, ["sync"])
    assert result.exit_code == 1
    assert "pagination did not advance" in result.output


def test_sync_content_partial_failure_has_nonzero_exit_and_keeps_successes(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "test.db")
    storage.upsert_files([PlaudFile(id="good"), PlaudFile(id="bad")], now=1)

    class Client:
        def file_content(self, file_id):
            if file_id == "bad":
                raise RuntimeError("network unavailable")
            return FileContent(
                file_id=file_id, summaries=[SummaryBlock(kind="auto_sum_note", body_md="saved")]
            )

    _client(monkeypatch, storage, Client)
    result = CliRunner().invoke(cli.app, ["sync-content", "--parallel", "2"])
    assert result.exit_code == 1
    assert "fetched 2/2; cached 1; deferred-empty 0; failed 1" in result.output
    assert storage.get_content_row("good") is not None
    assert storage.get_content_row("bad") is None
    assert storage.files_without_content() == []


def test_sync_content_rejects_zero_parallelism():
    result = CliRunner().invoke(cli.app, ["sync-content", "--parallel", "0"])
    assert result.exit_code == 2


def test_sync_content_defers_empty_results_and_manual_force_retries_only_uncached(
    tmp_path, monkeypatch
):
    storage = Storage(tmp_path / "test.db")
    storage.upsert_files([PlaudFile(id="empty", edit_time=10), PlaudFile(id="good")], now=1)
    requests = []

    class Client:
        def file_content(self, file_id):
            requests.append(file_id)
            if file_id == "empty":
                return FileContent(file_id=file_id, folder_ids=["folder"])
            return FileContent(
                file_id=file_id, summaries=[SummaryBlock(kind="auto_sum_note", body_md="saved")]
            )

    _client(monkeypatch, storage, Client)
    monkeypatch.setattr(cli.time, "time", lambda: 1000)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["sync-content"])
    assert result.exit_code == 0, result.output
    assert "fetched 2/2; cached 1; deferred-empty 1; failed 0" in result.output
    assert sorted(requests) == ["empty", "good"]
    assert storage.get_content_row("empty") is None
    with storage._connect() as conn:
        assert conn.execute("SELECT folder_id FROM file_folders WHERE file_id='empty'").fetchone()[
            0
        ] == ("folder")

    requests.clear()
    result = runner.invoke(cli.app, ["sync-content"])
    assert result.exit_code == 0, result.output
    assert "1 uncached files deferred for retry" in result.output
    assert "all files already cached" not in result.output
    assert requests == []

    result = runner.invoke(cli.app, ["sync-content", "--force"])
    assert result.exit_code == 0, result.output
    assert requests == ["empty"]

    requests.clear()
    storage.upsert_file(PlaudFile(id="empty", edit_time=11), now=1001)
    result = runner.invoke(cli.app, ["sync-content"])
    assert result.exit_code == 0, result.output
    assert requests == ["empty"]


def test_sync_content_cooldown_does_not_load_auth(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "test.db")
    storage.upsert_file(PlaudFile(id="empty"), now=1)
    storage.record_content_fetch_attempt("empty", source_edit_time=None, outcome="empty", now=1000)
    monkeypatch.setattr(cli, "Storage", lambda: storage)
    monkeypatch.setattr(cli, "_maybe_auto_metadata", lambda _storage: None)
    monkeypatch.setattr(cli.time, "time", lambda: 1001)

    def unexpected_auth():
        raise AssertionError("No network credentials needed for deferred content")

    monkeypatch.setattr(cli, "load_config", unexpected_auth)
    result = CliRunner().invoke(cli.app, ["sync-content"])
    assert result.exit_code == 0, result.output
    assert "1 uncached files deferred for retry" in result.output
