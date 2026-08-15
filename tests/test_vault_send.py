"""Tests for core.vault_send — integrated-first content resolution and the
direct / headless-AI vault delivery paths."""

from __future__ import annotations

from pathlib import Path

import pytest

import core.paths as paths_mod
import core.summarize as summarize_mod
from core.storage import Storage
from core.vault_send import (
    ResolvedContent,
    build_note_markdown,
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
