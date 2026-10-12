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
from core.vault_index import related_notes as _real_related_notes
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

    # Integrated appears -> it wins (a second note on purpose: on_duplicate="new").
    _write_integrated(tmp)
    result = vault_send(FILE_ID, storage=storage, on_duplicate="new")
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

    # Further notes for the same recording on purpose: on_duplicate="new".
    without = vault_send(FILE_ID, storage=sandbox["storage"], on_duplicate="new")
    assert "통합 전사본" not in Path(without.path).read_text(encoding="utf-8")

    transcript_only = vault_send(
        FILE_ID, storage=sandbox["storage"], content="transcript", on_duplicate="new"
    )
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


def _wiki_dest(form: str, sandbox: dict, wiki: Path) -> str:
    """A `--dest` under the main vault whose effective folder is in the wiki."""
    if form == "absolute":
        return str(wiki / "00. Inbox")  # Path joining lets it replace the root
    if form == "parent":
        return f"../{wiki.name}/00. Inbox"  # the wiki is the main vault's sibling
    link = sandbox["vault"] / "wiki-link"
    link.symlink_to(wiki, target_is_directory=True)
    return "wiki-link/00. Inbox"


@pytest.mark.parametrize("via", ["direct", "claude"])
@pytest.mark.parametrize("form", ["absolute", "parent", "symlink"])
def test_a_dest_that_lands_in_the_wiki_is_refused(sandbox, wiki: Path, form: str, via: str) -> None:
    dest = _wiki_dest(form, sandbox, wiki)
    result = vault_send(FILE_ID, to="main", dest=dest, via=via)
    assert result.status == "wiki_unsupported"
    assert _files(wiki) == []


def test_a_dest_through_an_unconfigured_wiki_folder_is_refused(sandbox, monkeypatch) -> None:
    monkeypatch.setattr(vault_send_mod, "wiki_vault", lambda: None)
    dest = sandbox["tmp"] / "x" / vault_send_mod.WIKI_VAULT_DIRNAME / "00. Inbox"
    _write_integrated(sandbox["tmp"])
    result = vault_send(FILE_ID, to="main", dest=str(dest), storage=sandbox["storage"])
    assert result.status == "wiki_unsupported"
    assert not dest.exists()


def test_an_ordinary_dest_still_sends(sandbox, wiki: Path, monkeypatch) -> None:
    monkeypatch.setattr(vault_send_mod, "Storage", lambda: sandbox["storage"])
    result = vault_send(FILE_ID, to="main", dest="60. Collections/63. Meetings")
    assert result.status == "ok"
    assert Path(result.path).parent == sandbox["vault"] / "60. Collections/63. Meetings"


@pytest.mark.parametrize("via", ["direct", "claude-window"])
@pytest.mark.parametrize("form", ["absolute", "parent", "symlink"])
def test_cli_dest_into_the_wiki_opens_nothing(
    sandbox, wiki: Path, monkeypatch, form: str, via: str
) -> None:
    launched: list = []
    monkeypatch.setattr(cli, "launch_claude", lambda *a, **k: launched.append(a))
    dest = _wiki_dest(form, sandbox, wiki)
    argv = ["vault-send", FILE_ID, "--to", "main", "--dest", dest, "--via", via, "--json"]
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "wiki_unsupported"
    assert launched == []
    assert _files(wiki) == []


# ------------------------------------------- a second send for the same recording


def _inbox_notes(sandbox: dict) -> list[Path]:
    inbox = sandbox["vault"] / "00. Inbox"
    return sorted(inbox.glob("*.md")) if inbox.exists() else []


def test_resend_with_ask_reports_duplicate_and_writes_nothing(sandbox) -> None:
    st = sandbox["storage"]
    _write_integrated(sandbox["tmp"])
    first = vault_send(FILE_ID, storage=st)  # the default is ask
    assert (first.status, first.action) == ("ok", "created")
    second = vault_send(FILE_ID, storage=st)
    assert second.status == "duplicate"
    assert second.path == first.path
    assert "--on-duplicate skip|overwrite|new" in second.detail
    assert [e["path"] for e in second.existing] == [first.path]
    assert set(second.existing[0]) == {"path", "updated_at", "modified_after_send", "obsidian_url"}
    assert second.dest == "00. Inbox"
    assert _inbox_notes(sandbox) == [Path(first.path)]


def test_resend_with_skip_points_at_the_existing_note(sandbox) -> None:
    st = sandbox["storage"]
    _write_integrated(sandbox["tmp"])
    first = vault_send(FILE_ID, storage=st)
    before = Path(first.path).read_bytes()
    skipped = vault_send(FILE_ID, storage=st, on_duplicate="skip")
    assert (skipped.status, skipped.action, skipped.path) == ("ok", "skipped", first.path)
    assert Path(first.path).read_bytes() == before
    assert len(st.list_note_references(FILE_ID)) == 1


def test_resend_with_overwrite_rewrites_in_place(sandbox) -> None:
    st = sandbox["storage"]
    _write_integrated(sandbox["tmp"], summary="처음 본문")
    first = Path(vault_send(FILE_ID, storage=st).path)
    text = first.read_text(encoding="utf-8")
    created = next(ln for ln in text.splitlines() if ln.startswith("date created:"))
    first.write_text(text.replace(created, "date created: 2026-01-01T09:00"), encoding="utf-8")
    _write_integrated(sandbox["tmp"], summary="새 본문")
    result = vault_send(FILE_ID, storage=st, on_duplicate="overwrite")
    assert (result.status, result.action, result.path) == ("ok", "overwritten", str(first))
    rewritten = first.read_text(encoding="utf-8")
    assert "새 본문" in rewritten and "처음 본문" not in rewritten
    assert "date created: 2026-01-01T09:00" in rewritten  # the note's birth is kept
    assert "date modified: 2026-01-01T09:00" not in rewritten
    assert _inbox_notes(sandbox) == [first]


def test_overwrite_keeps_the_note_in_its_current_folder(sandbox) -> None:
    st = sandbox["storage"]
    _write_integrated(sandbox["tmp"])
    first = vault_send(FILE_ID, storage=st, dest="60. Collections/63. Meetings")
    result = vault_send(FILE_ID, storage=st, on_duplicate="overwrite")
    assert (result.path, result.dest) == (first.path, "60. Collections/63. Meetings")
    assert _inbox_notes(sandbox) == []


def test_resend_with_new_keeps_a_numbered_copy(sandbox) -> None:
    st = sandbox["storage"]
    _write_integrated(sandbox["tmp"])
    first = vault_send(FILE_ID, storage=st)
    copy = vault_send(FILE_ID, storage=st, on_duplicate="new")
    assert (copy.status, copy.action) == ("ok", "created")
    assert copy.path.endswith(" (2).md")
    assert [e["path"] for e in copy.existing] == [first.path]


def test_skip_with_no_existing_note_creates(sandbox) -> None:
    _write_integrated(sandbox["tmp"])
    result = vault_send(FILE_ID, storage=sandbox["storage"], on_duplicate="skip")
    assert (result.status, result.action) == ("ok", "created")


def test_a_deleted_note_is_not_a_duplicate(sandbox) -> None:
    st = sandbox["storage"]
    _write_integrated(sandbox["tmp"])
    Path(vault_send(FILE_ID, storage=st).path).unlink()
    assert vault_send(FILE_ID, storage=st).action == "created"


def test_a_note_in_another_vault_is_not_a_duplicate(sandbox, tmp_path: Path) -> None:
    st = sandbox["storage"]
    other = tmp_path / "other-vault"
    other.mkdir()
    (other / "x.md").write_text(f"---\nplaud_id: {FILE_ID}\n---\n", encoding="utf-8")
    st.upsert_note_reference(file_id=FILE_ID, path=other / "x.md", kind="vault-send", now=1)
    _write_integrated(sandbox["tmp"])
    assert vault_send(FILE_ID, storage=st).action == "created"


def test_other_reference_kinds_do_not_count(sandbox) -> None:
    st = sandbox["storage"]
    _write_integrated(sandbox["tmp"])
    for kind in ("dual-transcript", "meeting-note"):
        note = sandbox["vault"] / f"{kind}.md"
        note.write_text("x", encoding="utf-8")
        st.upsert_note_reference(file_id=FILE_ID, path=note, kind=kind, now=1)
    assert vault_send(FILE_ID, storage=st).action == "created"


def test_a_same_name_note_naming_this_recording_counts_without_a_reference(
    sandbox, tmp_path: Path
) -> None:
    _write_integrated(sandbox["tmp"])
    first = Path(vault_send(FILE_ID, storage=sandbox["storage"]).path)
    fresh = Storage(tmp_path / "fresh.db")  # e.g. sent from another machine
    assert vault_send(FILE_ID, storage=fresh).status == "duplicate"

    first.write_text(
        first.read_text(encoding="utf-8").replace(FILE_ID, "someotherid"), encoding="utf-8"
    )
    other = Storage(tmp_path / "other.db")
    created = vault_send(FILE_ID, storage=other)
    assert created.action == "created" and created.path.endswith(" (2).md")


FOREIGN_ID = "fed987cba654"  # another recording, same length as FILE_ID


def _same_name_note(sandbox) -> Path:
    """Where a send of FILE_ID writes, occupied by whatever the test puts there."""
    _write_integrated(sandbox["tmp"])
    return Path(vault_send(FILE_ID, storage=sandbox["storage"]).path)


@pytest.mark.parametrize("policy", ["ask", "skip", "overwrite"])
def test_another_recordings_note_that_mentions_this_one_is_never_its_duplicate(
    sandbox, tmp_path: Path, policy: str
) -> None:
    # int-review-1 #1: recording B's note names A only in another field. With no
    # reference row (a note from another machine), ask called it A's note,
    # skip treated A as filed and overwrite replaced B's note.
    note = _same_name_note(sandbox)
    foreign = (
        f"---\nplaud_id: {FOREIGN_ID}\n"
        f"related_recording: https://web.plaud.ai/file/{FILE_ID}\n"
        "---\nForeign recording original body\n"
    )
    note.write_text(foreign, encoding="utf-8")
    result = vault_send(FILE_ID, storage=Storage(tmp_path / "fresh.db"), on_duplicate=policy)
    assert (result.status, result.action, result.existing) == ("ok", "created", [])
    assert result.path.endswith(" (2).md")
    assert note.read_text(encoding="utf-8") == foreign


@pytest.mark.parametrize(
    "front",
    [
        f"plaud_id: {FILE_ID}7",  # a longer id that contains this one
        f"plaud_id: {FILE_ID[:-1]}",  # a prefix of this one
        f"plaud_id: x{FILE_ID}",
        f"plaud_id: [{FILE_ID}]",  # not a scalar
        f"plaud_id: {FILE_ID}\nplaud_id: {FOREIGN_ID}",  # two identities
        f"description: notes on {FILE_ID}",  # named outside plaud_id
        f"source: https://web.plaud.ai/file/{FILE_ID}",  # a wiki field on a main note
        f"related:\n  - {FILE_ID}",
    ],
)
def test_only_this_notes_own_plaud_id_makes_it_a_duplicate(
    sandbox, tmp_path: Path, front: str
) -> None:
    note = _same_name_note(sandbox)
    note.write_text(f"---\n{front}\n---\nbody\n", encoding="utf-8")
    result = vault_send(FILE_ID, storage=Storage(tmp_path / "fresh.db"), on_duplicate="overwrite")
    assert (result.action, result.existing) == ("created", [])
    assert note.read_text(encoding="utf-8") == f"---\n{front}\n---\nbody\n"


@pytest.mark.parametrize(
    "text",
    [
        f"no front matter\nplaud_id: {FILE_ID}\n",  # the id only in the body
        f"---\nplaud_id: {FILE_ID}\nbody without a closing line\n",  # unterminated
        f"---\ntitle: x\n---\nplaud_id: {FILE_ID}\n",  # after the block
    ],
)
def test_missing_or_malformed_front_matter_never_claims_the_note(
    sandbox, tmp_path: Path, text: str
) -> None:
    note = _same_name_note(sandbox)
    note.write_text(text, encoding="utf-8")
    result = vault_send(FILE_ID, storage=Storage(tmp_path / "fresh.db"), on_duplicate="overwrite")
    assert (result.action, result.existing) == ("created", [])
    assert note.read_text(encoding="utf-8") == text


def test_a_meeting_note_draft_scan_keeps_the_send_reference(sandbox) -> None:
    # claude-review-1 regress-3: meeting-note records every vault note naming
    # the recording as a `draft`; the upsert relabelled the vault-send row,
    # so a later send to another folder missed it and wrote a second note.
    st = sandbox["storage"]
    _write_integrated(sandbox["tmp"])
    first = vault_send(FILE_ID, storage=st, dest="60. Collections/63. Meetings")
    st.upsert_note_reference(  # what write_meeting_note does for each draft it finds
        file_id=FILE_ID, path=first.path, kind="draft", title="x", now=99
    )
    again = vault_send(FILE_ID, storage=st)  # default ask, into the Inbox
    assert again.status == "duplicate"
    assert [e["path"] for e in again.existing] == [first.path]
    assert _inbox_notes(sandbox) == []


def test_a_placeholder_transcript_is_never_sent(sandbox) -> None:
    from core.integrate import NO_TRANSCRIPT

    _write_integrated(sandbox["tmp"], transcript=NO_TRANSCRIPT)
    note = Path(vault_send(FILE_ID, storage=sandbox["storage"], with_transcript=True).path)
    assert NO_TRANSCRIPT not in note.read_text(encoding="utf-8")


def test_a_real_integrated_transcript_is_still_sent(sandbox) -> None:
    _write_integrated(sandbox["tmp"], transcript="통합 전사본 실제 내용")
    note = Path(vault_send(FILE_ID, storage=sandbox["storage"], with_transcript=True).path)
    assert "통합 전사본 실제 내용" in note.read_text(encoding="utf-8")


@pytest.mark.parametrize("policy", ["ask", "skip", "overwrite"])
def test_a_stale_reference_row_never_claims_another_recordings_note(sandbox, policy: str) -> None:
    # claude-review-1 regress-2: A's note was moved out of the Inbox, then B
    # (same date, same title) was sent to A's old path, which A's reference
    # row still names. Re-sending A reported B's note; overwrite replaced it.
    st = sandbox["storage"]
    for file_id in (FILE_ID, FOREIGN_ID):
        st.upsert_file(PlaudFile(id=file_id, filename="Weekly meeting", start_time=1), now=1)
        folder = sandbox["tmp"] / "integrated" / file_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "claude__integrated.summary.md").write_text(
            f"---\nkind: summary\n---\n\nbody of {file_id}", encoding="utf-8"
        )
    a_note = Path(vault_send(FILE_ID, storage=st).path)
    moved = sandbox["vault"] / "60. Collections" / a_note.name
    moved.parent.mkdir(parents=True)
    a_note.rename(moved)
    b_note = Path(vault_send(FOREIGN_ID, storage=st).path)
    assert b_note == a_note  # B now sits where A's row points
    b_text = b_note.read_text(encoding="utf-8")
    result = vault_send(FILE_ID, storage=st, on_duplicate=policy)
    assert (result.status, result.action) == ("ok", "created")
    assert str(b_note) not in [e["path"] for e in result.existing]
    assert b_note.read_text(encoding="utf-8") == b_text


_A, _B = FILE_ID.encode(), FOREIGN_ID.encode()


@pytest.mark.parametrize(
    "raw",
    [
        # core-09 r4: a lone CR is a YAML line break, so B's identity hides
        # behind what looked like one comment line.
        pytest.param(
            b"---\nplaud_id: " + _A + b"\n# comment\rplaud_id: " + _B + b"\n---\nForeign body\n",
            id="r4-lone-cr",
        ),
        # core-09 r4: decoding with errors="ignore" deleted the byte that kept
        # this from being A's id.
        pytest.param(b"---\nplaud_id: abc123\xffdef456\n---\nForeign body\n", id="r4-invalid-utf8"),
        pytest.param(b"---\r\nplaud_id: " + _A + b"\r\n---\r\nbody\r\n", id="crlf"),
        pytest.param(b"---\nplaud_id: " + _A + b"\ntitle: a\x00b\n---\n", id="nul"),
        pytest.param(b"---\nplaud_id: " + _A + b"\ntitle: a\x0bb\n---\n", id="vertical-tab"),
        pytest.param(b"---\nplaud_id: " + _A + b"\ntitle: a\x7fb\n---\n", id="del"),
        pytest.param(
            b"---\nplaud_id: " + _A + b"\n# c\xc2\x85plaud_id: " + _B + b"\n---\n", id="nel"
        ),
        pytest.param(
            b"---\nplaud_id: " + _A + b"\n# c\xe2\x80\xa8plaud_id: " + _B + b"\n---\n",
            id="line-separator",
        ),
        pytest.param(b"\xef\xbb\xbf---\nplaud_id: " + _A + b"\n---\n", id="bom"),
    ],
)
def test_bytes_outside_plain_utf8_lines_mean_no_claim(sandbox, tmp_path: Path, raw: bytes) -> None:
    note = _same_name_note(sandbox)
    note.write_bytes(raw)
    result = vault_send(FILE_ID, storage=Storage(tmp_path / "fresh.db"), on_duplicate="overwrite")
    assert (result.action, result.existing) == ("created", [])
    assert note.read_bytes() == raw


def test_a_delimiter_cut_by_the_read_limit_is_no_delimiter(sandbox, tmp_path: Path) -> None:
    # core-09 r5: the 64 KiB read ended right after the `---` that begins a
    # longer line, which then passed for the closing line; B's identity and
    # the real end of the block lay beyond the window.
    prefix = b"---\nplaud_id: " + _A + b"\n#"
    prefix += b"x" * (65533 - len(prefix) - 1) + b"\n"
    raw = prefix + b"---not-a-delimiter: x\nplaud_id: " + _B + b"\n---\nForeign body\n"
    assert len(raw) == 65595
    note = _same_name_note(sandbox)
    note.write_bytes(raw)
    result = vault_send(FILE_ID, storage=Storage(tmp_path / "fresh.db"), on_duplicate="overwrite")
    assert (result.action, result.existing) == ("created", [])
    assert note.read_bytes() == raw


@pytest.mark.parametrize(
    "raw",
    [
        # A large block whose closing line is complete inside the window.
        pytest.param(
            b"---\nplaud_id: " + _A + b"\n#" + b"x" * 60000 + b"\n---\n" + b"y" * 10000,
            id="large-but-complete",
        ),
        # The file ends on the closing line itself (no newline after it).
        pytest.param(b"---\nplaud_id: " + _A + b"\n---", id="ends-at-the-delimiter"),
    ],
)
def test_a_complete_delimiter_line_still_closes_the_block(
    sandbox, tmp_path: Path, raw: bytes
) -> None:
    note = _same_name_note(sandbox)
    note.write_bytes(raw)
    result = vault_send(FILE_ID, storage=Storage(tmp_path / "fresh.db"))
    assert result.status == "duplicate"
    assert [e["path"] for e in result.existing] == [str(note)]


def _never_claimed(sandbox, tmp_path: Path, text: str) -> None:
    """Sending FILE_ID with `overwrite` and no reference row leaves `text` alone."""
    note = _same_name_note(sandbox)
    note.write_text(text, encoding="utf-8")
    result = vault_send(FILE_ID, storage=Storage(tmp_path / "fresh.db"), on_duplicate="overwrite")
    assert (result.action, result.existing) == ("created", [])
    assert note.read_text(encoding="utf-8") == text


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            f'---\ndescription: "Reference for another recording:\nplaud_id: {FILE_ID}\n"\n'
            f'"plaud_id": {FOREIGN_ID}\n---\nForeign recording original body\n',
            id="r1-multiline-description-and-quoted-key",
        ),
        pytest.param(
            f'---\ndescription: ["Reference for another recording:\nplaud_id: {FILE_ID}\n'
            f'# end"]\n---\nForeign recording original body\n',
            id="r2-flow-collection-wrapping-a-multiline-scalar",
        ),
        pytest.param(
            f"---\nplaud_id: {FILE_ID}\nplaud_id\t: {FOREIGN_ID}\n---\nForeign body\n",
            id="r3-tab-before-the-colon-after",
        ),
        pytest.param(
            f"---\nplaud_id\t: {FOREIGN_ID}\nplaud_id: {FILE_ID}\n---\nForeign body\n",
            id="r3-tab-before-the-colon-before",
        ),
    ],
)
def test_the_reviewers_cases_never_claim_the_note(sandbox, tmp_path: Path, text: str) -> None:
    _never_claimed(sandbox, tmp_path, text)


# Each block also carries this recording's own, correct `plaud_id`: one
# construct the reader does not model, anywhere, is enough to claim nothing.
UNSUPPORTED = [
    pytest.param("tags: [a, b]", id="flow-sequence"),
    pytest.param("meta: {a: 1}", id="flow-mapping"),
    pytest.param("notes: |\n  text", id="block-scalar-literal"),
    pytest.param("notes: >-\n  folded\n  text", id="block-scalar-folded"),
    pytest.param('description: "line one\n  line two"', id="multiline-double-quoted"),
    pytest.param("description: 'line one\n  line two'", id="multiline-single-quoted"),
    pytest.param("description: plain\n  continued", id="multiline-plain"),
    pytest.param('description: "never closed', id="unclosed-quote"),
    pytest.param("ref: &anchor value", id="anchor"),
    pytest.param("ref: *anchor", id="alias"),
    pytest.param("ref: !!str value", id="tag"),
    pytest.param('"title": quoted key', id="quoted-key"),
    pytest.param("meta:\n  nested: 1", id="nested-mapping"),
    pytest.param("tags:\n- a", id="list-at-column-0"),
    pytest.param("tags:\n\t- a", id="tab-indent"),
    pytest.param("tags:\n  - a\n    - b", id="uneven-list-indent"),
    pytest.param("? key\n: value", id="complex-key"),
    pytest.param('title: "a\\qb"', id="unknown-escape"),
    pytest.param("title: a: b", id="mapping-in-a-value"),
    # The grammar's own edges: no tab anywhere, keys of ASCII words, comments
    # only at column 0, items only at two spaces.
    pytest.param("title\t: x", id="tab-before-the-colon"),
    pytest.param("ti\ttle: x", id="tab-inside-a-key"),
    pytest.param("title: x\t# c", id="tab-before-a-comment"),
    pytest.param("title :x", id="space-before-the-colon"),
    pytest.param("title:x", id="no-space-after-the-colon"),
    pytest.param("title: # only a comment", id="comment-only-value"),
    pytest.param("제목: x", id="non-ascii-key"),
    pytest.param("date  created: x", id="double-space-in-a-key"),
    pytest.param("  # indented comment", id="indented-comment"),
    pytest.param("tags: [] # none", id="empty-list-with-a-comment"),
    pytest.param("tags:\n   - a", id="three-space-item"),
    pytest.param("  - orphan item", id="item-without-a-key"),
]


@pytest.mark.parametrize(
    "front",
    [
        pytest.param(f"plaud_id: {FILE_ID}\nplaud_id: {FILE_ID}", id="plaud_id-twice-same-value"),
        pytest.param(f"plaud_id: {FILE_ID}\nsource: plaud\nsource: plaud", id="source-twice"),
    ],
)
def test_an_identity_key_given_twice_means_no_claim(sandbox, tmp_path: Path, front: str) -> None:
    _never_claimed(sandbox, tmp_path, f"---\n{front}\n---\nbody\n")


@pytest.mark.parametrize("construct", UNSUPPORTED)
@pytest.mark.parametrize("where", ["before", "after"])
def test_an_unsupported_construct_anywhere_means_no_claim(
    sandbox, tmp_path: Path, construct: str, where: str
) -> None:
    lines = [f"plaud_id: {FILE_ID}", construct]
    if where == "before":
        lines.reverse()
    _never_claimed(sandbox, tmp_path, "---\n" + "\n".join(lines) + "\n---\nbody\n")


def test_the_apps_own_note_format_is_still_found(sandbox, tmp_path: Path) -> None:
    from core.frontmatter import CmdsFrontmatter

    front = CmdsFrontmatter(
        type="meeting",
        description='Plaud recording note: "주간 회의". Generated from the integrated output.',
        tags=["plaud", "meeting"],
        aliases=["주간 회의", "weekly: sync"],
        author="테스터",
        model="claude",
        date_created="2026-05-29T14:30",
        date="2026-05-29",
        cmds="📚 840 Lectures",
        index="🏷 Meeting Notes",
        plaud_id=FILE_ID,
        dual=True,
        speakers=["구요한", "John Smith"],
        attendees=["구요한"],
        keywords=["옵시디언", "AI #교육"],
        related=["Obsidian"],
        reuse_channels=["newsletter:idea"],
        extra={"content_kind": "integrated (claude__integrated)"},
    ).render()
    sparse = CmdsFrontmatter(type="note", description="x", plaud_id=FILE_ID).render()
    assert "aliases: []" in sparse and "tags: []" in sparse
    for text in (front, sparse):
        note = _same_name_note(sandbox)
        note.write_text(text + "\n\nbody\n", encoding="utf-8")
        result = vault_send(FILE_ID, storage=Storage(tmp_path / f"{len(text)}.db"))
        assert result.status == "duplicate", text
        assert [e["path"] for e in result.existing] == [str(note)]


@pytest.mark.parametrize(
    "text",
    [
        f'---\nplaud_id: "{FILE_ID}"\n---\n',
        f"---\nplaud_id: '{FILE_ID}'  \n---\n",
        f"---\nplaud_id: {FILE_ID} # sent from the laptop\n---\n",
        f'---\ntitle: "주간 회의 · 2026"\nplaud_id: {FILE_ID}\n---\n',  # UTF-8 beyond ASCII
        # core-09 r1 #2: a quoted id followed by a comment.
        f'---\nplaud_id: "{FILE_ID}" # correct owner\n---\nOriginal\n',
        f"---\nplaud_id: '{FILE_ID}'  # after two spaces\n---\n",
        # Rule 2 items, the `[]` literal, a multi-word key.
        f"---\ntags:\n  - a\n  - 'b' # c\naliases: []\ndate created: x\nplaud_id: {FILE_ID}\n---\n",
        # Rules 3 and 4: a column-0 comment and blank lines.
        f"---\n# a comment\nplaud_id: {FILE_ID}\n\n   \n---\n",
    ],
)
def test_a_genuine_same_recording_note_is_still_found(sandbox, tmp_path: Path, text: str) -> None:
    note = _same_name_note(sandbox)
    note.write_text(text, encoding="utf-8")
    result = vault_send(FILE_ID, storage=Storage(tmp_path / "fresh.db"))
    assert result.status == "duplicate"
    assert [e["path"] for e in result.existing] == [str(note)]


# ----- related links stay inside the vault they point into ----------------


@pytest.fixture()
def linked(sandbox, monkeypatch: pytest.MonkeyPatch, index_note):
    """The sandbox with the real related-note lookup and a wiki next to it."""
    monkeypatch.setattr(vault_index_mod, "related_notes", _real_related_notes)
    wiki = sandbox["tmp"] / "CMDS_LLM_Wiki"
    wiki.mkdir()
    monkeypatch.setenv("PLAUD_WIKI_VAULT", str(wiki))
    st = sandbox["storage"]
    st.upsert_file(PlaudFile(id=FILE_ID, filename="회의"), now=1)
    st.save_content(
        FileContent(
            file_id=FILE_ID,
            title="회의",
            keywords=["Obsidian", "LLM Wiki Pattern", "Talk"],
            transcript=[TranscriptSegment(start_time=0, end_time=1, speaker="A", content="x")],
        ),
        now=1,
    )
    index_note(sandbox["vault"], "Tools/Obsidian.md")
    index_note(wiki, "20. Wiki/21. Concepts/LLM Wiki Pattern.md", note_type="concept")
    index_note(wiki, "10. Raw Sources/Talk.md", note_type="raw-source")
    return {**sandbox, "wiki": wiki}


def test_a_main_send_links_wiki_pages_by_advanced_uri_only(linked) -> None:
    _write_integrated(linked["tmp"])
    text = Path(vault_send(FILE_ID, storage=linked["storage"]).path).read_text(encoding="utf-8")
    assert 'related:\n  - "[[Obsidian]]"\nwikiVaultRelated:\n' in text
    link = (
        "[LLM Wiki: LLM Wiki Pattern](obsidian://advanced-uri?vault=CMDS_LLM_Wiki"
        "&filepath=20.%20Wiki%2F21.%20Concepts%2FLLM%20Wiki%20Pattern.md)"
    )
    assert f'  - "{link}"' in text  # front matter
    assert f"- {link}" in text  # the body's 관련 노트
    assert "[[LLM Wiki Pattern]]" not in text and "[[Talk]]" not in text
    assert "LLM Wiki: Talk" not in text  # raw sources are never linked


def test_note_context_does_not_reuse_stored_titles_from_other_vaults(linked, index_note) -> None:
    st = linked["storage"]
    index_note(linked["tmp"] / "GOBI", "GobiOnly.md")
    st.upsert_note_metadata(file_id=FILE_ID, metadata={"related": ["GobiOnly", "Obsidian"]}, now=1)
    ctx = note_context(st, FILE_ID, build_recording_snapshot(st, FILE_ID))
    assert ctx["related"] == ["Obsidian"]
    assert [n["vault"] for n in ctx["main_related"]] == ["vault"]
    assert [n["title"] for n in ctx["wiki_related"]] == ["LLM Wiki Pattern"]


def test_note_context_writes_no_wikilinks_outside_the_main_vault(linked) -> None:
    st = linked["storage"]
    other = linked["tmp"] / "Other"
    other.mkdir()
    ctx = note_context(st, FILE_ID, build_recording_snapshot(st, FILE_ID), dest_root=other)
    assert ctx["related"] == []
    assert [n["title"] for n in ctx["main_related"]] == ["Obsidian"]  # still for cross links


def test_a_send_outside_the_main_vault_gets_no_wikilinks(linked, tmp_path: Path) -> None:
    # core-08 r1 #1: an absolute or `..` --dest writes into another vault, but
    # the main vault's titles were still written there as `[[…]]`.
    _write_integrated(linked["tmp"])
    other = tmp_path / "Other"
    other.mkdir()
    for dest in (str(other / "Inbox"), "../Other/ParentInbox"):
        result = vault_send(FILE_ID, storage=linked["storage"], dest=dest, on_duplicate="new")
        path = Path(result.path).resolve()
        assert result.status == "ok" and path.is_relative_to(other.resolve()), dest
        text = path.read_text(encoding="utf-8")
        assert "[[Obsidian]]" not in text, dest
        assert "[LLM Wiki: LLM Wiki Pattern]" in text  # an addressed link is still fine
    inside = vault_send(
        FILE_ID, storage=linked["storage"], dest="60. Collections", on_duplicate="new"
    )
    assert "[[Obsidian]]" in Path(inside.path).read_text(encoding="utf-8")  # a main subfolder


def test_related_links_refresh_the_index_and_survive_its_failure(sandbox, monkeypatch) -> None:
    calls: list[str] = []

    def broken(**_k):
        calls.append("refresh")
        raise RuntimeError("database is locked")

    monkeypatch.setattr(vault_index_mod, "refresh_if_stale", broken)
    _write_integrated(sandbox["tmp"])
    assert vault_send(FILE_ID, storage=sandbox["storage"]).status == "ok"
    assert calls == ["refresh"]


def test_the_duplicate_check_precedes_the_model_call(sandbox, monkeypatch) -> None:
    _write_integrated(sandbox["tmp"])
    vault_send(FILE_ID, storage=sandbox["storage"])

    def boom(*_a, **_k):
        raise AssertionError("the model ran before the duplicate check")

    monkeypatch.setattr(summarize_mod, "model_available", lambda m: True)
    monkeypatch.setattr(summarize_mod, "run_model", boom)
    assert vault_send(FILE_ID, storage=sandbox["storage"], via="claude").status == "duplicate"


def test_modified_after_send_is_reported(sandbox) -> None:
    import os
    import time

    st = sandbox["storage"]
    _write_integrated(sandbox["tmp"])
    first = vault_send(FILE_ID, storage=st)
    assert vault_send(FILE_ID, storage=st).existing[0]["modified_after_send"] is False
    later = time.time() + 60
    os.utime(first.path, (later, later))
    assert vault_send(FILE_ID, storage=st).existing[0]["modified_after_send"] is True


def test_an_unknown_on_duplicate_value_raises(sandbox) -> None:
    with pytest.raises(ValueError, match="ask, skip, overwrite, new"):
        vault_send(FILE_ID, storage=sandbox["storage"], on_duplicate="bogus")


def test_cli_on_duplicate_defaults_to_ask(sandbox, monkeypatch) -> None:
    monkeypatch.setattr(vault_send_mod, "Storage", lambda: sandbox["storage"])
    _write_integrated(sandbox["tmp"])
    runner = CliRunner()
    first = json.loads(runner.invoke(cli.app, ["vault-send", FILE_ID, "--json"]).stdout)
    assert (first["status"], first["action"], first["existing"]) == ("ok", "created", [])

    again = runner.invoke(cli.app, ["vault-send", FILE_ID, "--json"])
    assert again.exit_code == 0
    data = json.loads(again.stdout)
    assert data["status"] == "duplicate" and data["existing"][0]["path"] == first["path"]

    plain = runner.invoke(cli.app, ["vault-send", FILE_ID])
    assert plain.exit_code == 1
    assert "already in the vault" in plain.output

    skipped = runner.invoke(cli.app, ["vault-send", FILE_ID, "--on-duplicate", "skip", "--json"])
    assert json.loads(skipped.stdout)["action"] == "skipped"

    bad = runner.invoke(cli.app, ["vault-send", FILE_ID, "--on-duplicate", "bogus"])
    assert bad.exit_code != 0
    assert len(_inbox_notes(sandbox)) == 1


def test_cli_claude_window_duplicate_launches_nothing(sandbox, monkeypatch) -> None:
    _write_integrated(sandbox["tmp"])
    vault_send(FILE_ID, storage=sandbox["storage"])
    monkeypatch.setattr(cli, "Storage", lambda: sandbox["storage"])
    launched: list = []
    monkeypatch.setattr(cli, "launch_claude", lambda *a, **k: launched.append(a))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["vault-send", FILE_ID, "--via", "claude-window", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "duplicate"

    overwrite = runner.invoke(
        cli.app, ["vault-send", FILE_ID, "--via", "claude-window", "--on-duplicate", "overwrite"]
    )
    assert overwrite.exit_code != 0
    assert launched == []


def test_cli_claude_window_new_names_a_free_file_and_keeps_the_old_one(
    sandbox, monkeypatch
) -> None:
    monkeypatch.setattr(cli, "Storage", lambda: sandbox["storage"])
    prompts: list[str] = []
    monkeypatch.setattr(cli, "launch_claude", lambda prompt, **k: prompts.append(prompt))
    _write_integrated(sandbox["tmp"])
    argv = ["vault-send", FILE_ID, "--via", "claude-window", "--json"]

    CliRunner().invoke(cli.app, argv)  # nothing in the vault yet
    first_name = Path(vault_send(FILE_ID, storage=sandbox["storage"]).path).name
    assert f"`{first_name}`" in prompts[0]  # the exact name, so a later send can find it

    result = CliRunner().invoke(cli.app, [*argv, "--on-duplicate", "new"])
    assert result.exit_code == 0, result.output
    lines = prompts[1].splitlines()
    numbered = first_name.removesuffix(".md") + " (2).md"
    name_line = next(ln for ln in lines if "파일명" in ln)
    assert f"`{numbered}`" in name_line  # a free name, never the occupied one
    assert f"`{first_name}`" not in name_line
    keep_line = next(ln for ln in lines if "덮어쓰지" in ln)
    assert first_name in keep_line  # the existing note is named as one to leave alone


# ----- a wiki nested inside the main vault is still the wiki -----------------


@pytest.fixture()
def nested_wiki(sandbox, monkeypatch: pytest.MonkeyPatch) -> dict:
    """A configured wiki inside the main vault, holding a raw source that a
    legacy vault-send reference points at."""
    root = sandbox["vault"] / "nested-wiki"
    root.mkdir()
    monkeypatch.setattr(vault_send_mod, "wiki_vault", lambda: root)
    note = root / "immutable-source.md"
    note.write_text(
        f"---\nsource: https://web.plaud.ai/file/{FILE_ID}\n---\nORIGINAL\n", encoding="utf-8"
    )
    sandbox["storage"].upsert_note_reference(file_id=FILE_ID, path=note, kind="vault-send", now=1)
    _write_integrated(sandbox["tmp"], summary="NEW MAIN NOTE")
    return {"root": root, "note": note, "bytes": note.read_bytes()}


@pytest.mark.parametrize("policy", ["ask", "overwrite"])
def test_a_main_send_never_treats_a_nested_wiki_note_as_its_duplicate(
    sandbox, nested_wiki, policy
) -> None:
    result = vault_send(FILE_ID, to="main", storage=sandbox["storage"], on_duplicate=policy)
    assert (result.status, result.action) == ("ok", "created")
    assert not Path(result.path).resolve().is_relative_to(nested_wiki["root"].resolve())
    assert nested_wiki["note"].read_bytes() == nested_wiki["bytes"]


def test_an_overwrite_target_in_the_wiki_is_refused_before_the_model(
    sandbox, nested_wiki, monkeypatch
) -> None:
    # Belt and braces: even if a wiki note reached the candidates, overwrite
    # re-checks its target before any model call or write.
    wiki_entry = {
        "path": str(nested_wiki["note"]),
        "updated_at": 1,
        "modified_after_send": False,
        "obsidian_url": "",
    }
    monkeypatch.setattr(vault_send_mod, "send_duplicates", lambda *a, **k: [wiki_entry])

    def boom(*_a, **_k):
        raise AssertionError("the model ran for a wiki overwrite target")

    monkeypatch.setattr(summarize_mod, "model_available", lambda m: True)
    monkeypatch.setattr(summarize_mod, "run_model", boom)
    result = vault_send(
        FILE_ID, to="main", storage=sandbox["storage"], via="claude", on_duplicate="overwrite"
    )
    assert result.status == "wiki_unsupported"
    assert nested_wiki["note"].read_bytes() == nested_wiki["bytes"]


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
