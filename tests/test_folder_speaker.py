"""Single-folder enforcement, server transcript relabel, and Grok CLI backend."""

from pathlib import Path

import pytest

from core import summarize
from core.client import PlaudAPIError, PlaudClient
from core.config import PlaudConfig
from core.storage import Storage


def _client() -> PlaudClient:
    return PlaudClient(
        PlaudConfig(
            authorization="Bearer test",
            x_device_id="device",
            x_pld_tag="tag",
            x_pld_user="user",
        )
    )


# ---------- single-folder enforcement ----------


def test_set_file_folders_rejects_multiple_folders() -> None:
    client = _client()
    with pytest.raises(ValueError, match="single folder"):
        client.set_file_folders("f1", ["a", "b"])


def test_set_file_folders_allows_single_and_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(client, "_patch_json", lambda path, payload: calls.append((path, payload)))
    client.set_file_folders("f1", ["a"])
    client.set_file_folders("f1", [])
    assert calls == [
        ("/file/f1", {"filetag_id_list": ["a"]}),
        ("/file/f1", {"filetag_id_list": []}),
    ]


def test_storage_files_with_multiple_folders(tmp_path: Path) -> None:
    storage = Storage(db_path=tmp_path / "test.db")
    with storage._connect() as conn:
        conn.executemany(
            "INSERT INTO file_folders (file_id, folder_id) VALUES (?, ?)",
            [("multi", "a"), ("multi", "b"), ("single", "c")],
        )
    broken = storage.files_with_multiple_folders()
    assert broken == [("multi", ["a", "b"])]


# ---------- server transcript speaker relabel ----------


def test_rename_transcript_speakers_patches_full_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    segments = [
        {
            "content": "hello",
            "start_time": 0,
            "end_time": 1000,
            "speaker": "Speaker 1",
            "original_speaker": "Speaker 1",
            "embeddingKey": None,
        },
        {
            "content": "renamed before",
            "start_time": 1000,
            "end_time": 2000,
            "speaker": "구요한",
            "original_speaker": "Speaker 2",
            "embeddingKey": None,
        },
        {
            "content": "no original field",
            "start_time": 2000,
            "end_time": 3000,
            "speaker": "Speaker 3",
        },
    ]
    pushed: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(client, "raw_transcript", lambda file_id: segments)
    monkeypatch.setattr(
        client, "update_transcript", lambda file_id, segs: pushed.append((file_id, segs))
    )

    changed = client.rename_transcript_speakers(
        "f1", {"Speaker 1": "구요한", "Speaker 3": "백승태"}
    )

    assert changed == 2
    assert len(pushed) == 1
    sent = pushed[0][1]
    assert sent[0]["speaker"] == "구요한"
    assert sent[0]["original_speaker"] == "Speaker 1"  # preserved, not overwritten
    assert sent[1]["speaker"] == "구요한"  # untouched (not in mapping)
    assert sent[2]["speaker"] == "백승태"
    assert sent[2]["original_speaker"] == "Speaker 3"  # backfilled from current


def test_rename_transcript_speakers_skips_push_when_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    monkeypatch.setattr(
        client,
        "raw_transcript",
        lambda file_id: [{"content": "x", "speaker": "A", "start_time": 0, "end_time": 1}],
    )
    monkeypatch.setattr(
        client,
        "update_transcript",
        lambda *a: pytest.fail("must not PATCH when nothing changed"),
    )
    assert client.rename_transcript_speakers("f1", {"Z": "Y"}) == 0


def test_rename_transcript_speakers_requires_server_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    monkeypatch.setattr(client, "raw_transcript", lambda file_id: [])
    with pytest.raises(PlaudAPIError, match="no server transcript"):
        client.rename_transcript_speakers("f1", {"A": "B"})


# ---------- Grok CLI backend ----------


class _Proc:
    returncode = 0
    stdout = "summary text\n"
    stderr = ""


def test_grok_cli_passes_prompt_as_argv_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setenv("XAI_API_KEY", "should-be-stripped")
    monkeypatch.setattr(summarize.subprocess, "run", fake_run)
    monkeypatch.setattr(summarize, "_resolve_binary", lambda model, name: "/fake/grok")

    out = summarize._run_cli("grok", "hello prompt", timeout=10)

    assert out == "summary text"
    assert captured["argv"][0] == "/fake/grok"
    assert captured["argv"][-1] == "hello prompt"  # argv, not stdin
    assert "-p" in captured["argv"]
    assert captured["kwargs"]["input"] is None
    assert "XAI_API_KEY" not in captured["kwargs"]["env"]


def test_grok_cli_rejects_oversized_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(summarize, "_resolve_binary", lambda model, name: "/fake/grok")
    big = "x" * (summarize.PROMPT_ARG_LIMIT + 1)
    with pytest.raises(summarize.ModelFailed, match="prompt too large"):
        summarize._run_cli("grok", big, timeout=10)


def test_claude_cli_still_uses_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(summarize.subprocess, "run", fake_run)
    monkeypatch.setattr(summarize, "_resolve_binary", lambda model, name: "/fake/claude")

    summarize._run_cli("claude", "hello", timeout=10)

    assert captured["kwargs"]["input"] == "hello"
    assert "hello" not in captured["argv"]
