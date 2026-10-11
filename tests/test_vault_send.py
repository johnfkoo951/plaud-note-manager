"""Tests for core.vault_send — integrated-first content resolution and the
direct / headless-AI vault delivery paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import cli.main as cli
import core.paths as paths_mod
import core.summarize as summarize_mod
import core.vault_index as vault_index_mod
import core.vault_send as vault_send_mod
from core.metadata import build_recording_snapshot
from core.models import FileContent, PlaudFile, TranscriptSegment
from core.storage import Storage
from core.vault_send import (
    ResolvedContent,
    build_note_markdown,
    note_context,
    resolve_vault,
    unique_note_path,
    vault_send,
    wiki_vault,
)

FILE_ID = "abc123def456"


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated artifact dirs + vault + tmp storage; no real data/ touched."""
    monkeypatch.setattr(paths_mod, "INTEGRATED_DIR", tmp_path / "integrated")
    monkeypatch.setattr(paths_mod, "SUMMARIES_DIR", tmp_path / "summaries")
    monkeypatch.setattr(paths_mod, "_override", lambda kind: None)
    # note_context looks up related notes, which opens the real data/plaud.db.
    monkeypatch.setattr(vault_index_mod, "related_notes", lambda *a, **k: [])
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("PLAUD_OBSIDIAN_VAULT", str(vault))
    monkeypatch.delenv("PLAUD_WIKI_VAULT", raising=False)
    monkeypatch.setenv("PLAUD_AUTHOR", "테스터")
    storage = Storage(tmp_path / "plaud.db")
    return {"tmp": tmp_path, "vault": vault, "storage": storage}


def _write_integrated(
    tmp: Path,
    *,
    model="claude",
    template="integrated",
    summary="통합 요약 본문",
    transcript="통합 전사본",
) -> None:
    d = tmp / "integrated" / FILE_ID
    d.mkdir(parents=True, exist_ok=True)
    front = "---\nkind: summary\n---\n\n"
    (d / f"{model}__{template}.summary.md").write_text(front + summary, encoding="utf-8")
    (d / f"{model}__{template}.transcript.md").write_text(front + transcript, encoding="utf-8")


def _write_summary(tmp: Path, *, model="codex", template="default", body="슬롯 요약") -> None:
    d = tmp / "summaries" / FILE_ID
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{model}__{template}.md").write_text(body, encoding="utf-8")


def _seed_speakers(storage: Storage) -> None:
    """Plaud's transcript carries two real names and a placeholder; the
    diarizer adds its own numbered label on top."""
    storage.upsert_file(PlaudFile(id=FILE_ID, filename="회의"), now=1)
    storage.save_content(
        FileContent(
            file_id=FILE_ID,
            title="회의",
            transcript=[
                TranscriptSegment(start_time=0, end_time=1, speaker="Speaker 3", content="a"),
                TranscriptSegment(start_time=1, end_time=2, speaker="구요한", content="b"),
                TranscriptSegment(start_time=2, end_time=3, speaker="John Smith", content="c"),
            ],
        ),
        now=1,
    )
    storage.save_cmds_transcript(
        file_id=FILE_ID,
        model="scribe",
        language="ko",
        text="a",
        segments_json=json.dumps([{"speaker": "speaker_0", "content": "a"}]),
        now=1,
    )


# ------------------------------------------------------------ vault targets


def test_resolve_vault_targets(sandbox, tmp_path: Path, monkeypatch) -> None:
    assert resolve_vault("main") == sandbox["vault"].resolve()
    assert resolve_vault("") == sandbox["vault"].resolve()
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert resolve_vault(str(other)) == other
    assert resolve_vault(str(tmp_path / "missing")) is None


def test_wiki_vault_falls_back_to_sibling(sandbox, monkeypatch) -> None:
    assert wiki_vault() is None  # no sibling yet
    sibling = sandbox["vault"].parent / "CMDS_LLM_Wiki"
    sibling.mkdir()
    assert wiki_vault() == sibling
    explicit = sandbox["tmp"] / "explicit-wiki"
    explicit.mkdir()
    monkeypatch.setenv("PLAUD_WIKI_VAULT", str(explicit))
    assert wiki_vault() == explicit.resolve()


# ----------------------------------------------------- content resolution


def test_integrated_first_fallback_chain(sandbox) -> None:
    storage = sandbox["storage"]
    tmp = sandbox["tmp"]

    # Nothing generated -> no_content.
    result = vault_send(FILE_ID, storage=storage)
    assert result.status == "no_content"

    # Slot summary only -> integrated request falls back to it.
    _write_summary(tmp)
    result = vault_send(FILE_ID, storage=storage)
    assert result.status == "ok"
    assert result.content == "summary"

    # Integrated appears -> it wins.
    _write_integrated(tmp)
    result = vault_send(FILE_ID, storage=storage)
    assert result.status == "ok"
    assert result.content == "integrated"
    assert "통합 요약 본문" in Path(result.path).read_text(encoding="utf-8")


def test_specific_artifact_selection(sandbox) -> None:
    tmp = sandbox["tmp"]
    _write_integrated(tmp, model="claude", template="integrated", summary="클로드 통합")
    _write_integrated(tmp, model="codex", template="integrated", summary="코덱스 통합")
    result = vault_send(FILE_ID, storage=sandbox["storage"], model="codex", template="integrated")
    assert result.status == "ok"
    assert "코덱스 통합" in Path(result.path).read_text(encoding="utf-8")


def test_transcript_content_and_with_transcript(sandbox) -> None:
    tmp = sandbox["tmp"]
    _write_integrated(tmp)
    with_t = vault_send(FILE_ID, storage=sandbox["storage"], with_transcript=True)
    body = Path(with_t.path).read_text(encoding="utf-8")
    assert "## Transcript" in body and "통합 전사본" in body

    without = vault_send(FILE_ID, storage=sandbox["storage"])
    assert "통합 전사본" not in Path(without.path).read_text(encoding="utf-8")

    transcript_only = vault_send(FILE_ID, storage=sandbox["storage"], content="transcript")
    assert transcript_only.status == "ok"
    assert "통합 전사본" in Path(transcript_only.path).read_text(encoding="utf-8")


# ------------------------------------------------------------- note format


def test_note_frontmatter_and_dest(sandbox) -> None:
    tmp = sandbox["tmp"]
    _write_integrated(tmp)
    result = vault_send(FILE_ID, storage=sandbox["storage"], dest="60. Collections/63. Meetings")
    assert result.status == "ok"
    note = Path(result.path)
    assert note.parent == sandbox["vault"] / "60. Collections/63. Meetings"
    text = note.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert f"plaud_id: {FILE_ID}" in text
    assert "source: plaud" in text
    assert '- "[[테스터]]"' in text
    assert "content_kind: integrated (claude__integrated)" in text
    assert result.obsidian_url.startswith("obsidian://open?vault=vault&file=")


def test_unique_note_path_collision(tmp_path: Path) -> None:
    (tmp_path / "20260710_회의.md").write_text("x", encoding="utf-8")
    p = unique_note_path(tmp_path, "20260710", "회의")
    assert p.name == "20260710_회의 (2).md"


def test_build_note_without_summary_uses_transcript() -> None:
    resolved = ResolvedContent(kind="transcript", label="raw", summary="", transcript="전사만")
    text = build_note_markdown(
        file_id=FILE_ID,
        title="T",
        date="2026-07-10",
        note_type="note",
        tags=["plaud"],
        resolved=resolved,
        with_transcript=False,
    )
    assert "전사만" in text  # transcript-only notes still carry the body


def test_note_context_keeps_full_names_and_drops_placeholders(sandbox) -> None:
    storage = sandbox["storage"]
    _seed_speakers(storage)
    ctx = note_context(storage, FILE_ID, build_recording_snapshot(storage, FILE_ID))
    assert ctx["speakers"] == ["John Smith", "구요한"]


def test_sent_note_has_no_fragment_speaker_links(sandbox) -> None:
    _seed_speakers(sandbox["storage"])
    _write_integrated(sandbox["tmp"])
    result = vault_send(FILE_ID, storage=sandbox["storage"])
    assert result.status == "ok"
    text = Path(result.path).read_text(encoding="utf-8")
    assert '"[[구요한]]"' in text
    assert "> 참석자: [[John Smith]] [[구요한]]" in text
    for fragment in ("[[Speaker]]", "[[3]]", "[[Speaker 3]]", "[[speaker_0]]", "[[John]]"):
        assert fragment not in text


# -------------------------------------------------------------- claude via


def test_via_claude_writes_model_output(sandbox, monkeypatch) -> None:
    _write_integrated(sandbox["tmp"])
    monkeypatch.setattr(summarize_mod, "model_available", lambda m: True)
    monkeypatch.setattr(
        summarize_mod,
        "run_model",
        lambda model, prompt, model_id=None, timeout=0: "---\ntype: note\n---\n\nAI가 정리한 본문",
    )
    result = vault_send(FILE_ID, storage=sandbox["storage"], via="claude")
    assert result.status == "ok"
    assert "AI가 정리한 본문" in Path(result.path).read_text(encoding="utf-8")


def test_via_claude_wraps_frontmatterless_output(sandbox, monkeypatch) -> None:
    _write_integrated(sandbox["tmp"])
    monkeypatch.setattr(summarize_mod, "model_available", lambda m: True)
    monkeypatch.setattr(
        summarize_mod,
        "run_model",
        lambda model, prompt, model_id=None, timeout=0: "frontmatter 없는 응답",
    )
    result = vault_send(FILE_ID, storage=sandbox["storage"], via="claude")
    assert result.status == "ok"
    text = Path(result.path).read_text(encoding="utf-8")
    assert text.startswith("---\n")  # deterministic wrapper kicked in
    assert "frontmatter 없는 응답" in text


def test_via_claude_model_failure_is_reported(sandbox, monkeypatch) -> None:
    _write_integrated(sandbox["tmp"])
    monkeypatch.setattr(summarize_mod, "model_available", lambda m: True)

    def boom(*a, **k):
        raise RuntimeError("cli exploded")

    monkeypatch.setattr(summarize_mod, "run_model", boom)
    result = vault_send(FILE_ID, storage=sandbox["storage"], via="claude")
    assert result.status == "model_failed"
    assert "cli exploded" in result.detail


# ------------------------------------------------------------ bookkeeping


def test_reference_recorded(sandbox) -> None:
    _write_integrated(sandbox["tmp"])
    result = vault_send(FILE_ID, storage=sandbox["storage"])
    refs = sandbox["storage"].list_note_references(FILE_ID)
    assert any(r["kind"] == "vault-send" and r["path"] == result.path for r in refs)


# ------------------------------------------------- the wiki takes raw sources only


@pytest.fixture()
def wiki(sandbox, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A configured wiki; Storage and the model blow up if a refused send reaches them."""
    root = sandbox["tmp"] / "CMDS_LLM_Wiki"
    (root / "10. Raw Sources").mkdir(parents=True)
    monkeypatch.setattr(vault_send_mod, "wiki_vault", lambda: root)

    def boom(*_a, **_k):
        raise AssertionError("a refused wiki send reached the database or the model")

    monkeypatch.setattr(vault_send_mod, "Storage", boom)
    monkeypatch.setattr(cli, "Storage", boom)
    monkeypatch.setattr(summarize_mod, "run_model", boom)
    _write_integrated(sandbox["tmp"])
    return root


def _files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


@pytest.mark.parametrize("via", ["direct", "claude"])
@pytest.mark.parametrize("to", ["wiki", " Wiki ", "WIKI"])
def test_wiki_keyword_is_refused_before_anything_is_opened(wiki: Path, to: str, via: str) -> None:
    result = vault_send(FILE_ID, to=to, via=via)
    assert result.status == "wiki_unsupported"
    assert "vault-compose" in result.detail and "--to wiki" in result.detail
    assert _files(wiki) == []


def test_wiki_refusal_precedes_vault_resolution(sandbox, monkeypatch) -> None:
    # No wiki configured anywhere: still a refusal, not no_vault.
    monkeypatch.setattr(vault_send_mod, "wiki_vault", lambda: None)
    assert vault_send(FILE_ID, to="wiki").status == "wiki_unsupported"


@pytest.mark.parametrize("inside", ["", "10. Raw Sources"])
def test_paths_into_the_wiki_are_refused(wiki: Path, inside: str) -> None:
    result = vault_send(FILE_ID, to=str(wiki / inside) if inside else str(wiki))
    assert result.status == "wiki_unsupported"
    assert _files(wiki) == []


def test_unconfigured_wiki_is_recognised_by_its_folder_name(sandbox, monkeypatch) -> None:
    root = sandbox["tmp"] / "elsewhere" / vault_send_mod.WIKI_VAULT_DIRNAME / "00. Inbox"
    root.mkdir(parents=True)
    monkeypatch.setattr(vault_send_mod, "wiki_vault", lambda: None)
    assert vault_send(FILE_ID, to=str(root), storage=sandbox["storage"]).status == (
        "wiki_unsupported"
    )
    assert _files(root) == []


def test_is_wiki_target_and_resolve_vault_share_the_keyword(sandbox, monkeypatch) -> None:
    other = sandbox["tmp"] / "wiki-ish"
    other.mkdir()
    root = sandbox["tmp"] / "the-wiki"
    root.mkdir()
    monkeypatch.setattr(vault_send_mod, "wiki_vault", lambda: root)
    for spelling in ("wiki", " Wiki ", "WIKI"):
        assert vault_send_mod.is_wiki_target(spelling)
        assert resolve_vault(spelling) == root
    assert not vault_send_mod.is_wiki_target("main", sandbox["vault"])
    assert not vault_send_mod.is_wiki_target(str(other), other)
    assert vault_send_mod.is_wiki_target(str(root), root)


def test_main_note_carries_no_wiki_only_fields(sandbox) -> None:
    _write_integrated(sandbox["tmp"])
    text = Path(vault_send(FILE_ID, storage=sandbox["storage"]).path).read_text(encoding="utf-8")
    assert "source-vault:" not in text
    assert "mainVaultRelated:" not in text


def test_cli_vault_send_to_the_wiki_is_refused(wiki: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["vault-send", FILE_ID, "--to", "wiki", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "wiki_unsupported"

    result = runner.invoke(cli.app, ["vault-send", FILE_ID, "--to", "wiki"])
    assert result.exit_code == 1
    assert "vault-compose" in result.output
    assert _files(wiki) == []


def test_cli_claude_window_to_the_wiki_launches_nothing(wiki: Path, monkeypatch) -> None:
    launched: list = []
    monkeypatch.setattr(cli, "launch_claude", lambda *a, **k: launched.append(a))
    result = CliRunner().invoke(
        cli.app, ["vault-send", FILE_ID, "--to", str(wiki), "--via", "claude-window", "--json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert (data["status"], data["via"]) == ("wiki_unsupported", "claude-window")
    assert launched == []
