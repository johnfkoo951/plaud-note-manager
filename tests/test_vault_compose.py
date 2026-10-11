"""Composing a templated note into a chosen vault.

The two vaults get different artifacts under different contracts, so most of
these tests are about keeping the wiki path from drifting back into main-vault
shapes.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from core import vault_compose as vc
from core.models import FileContent, PlaudFile, TranscriptSegment
from core.storage import Storage

NOW = int(time.time())


@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    st = Storage(tmp_path / "plaud.db")
    st.upsert_file(PlaudFile(id="f1", filename="09-14 세미나", duration=1000), now=NOW)
    st.save_content(
        FileContent(
            file_id="f1",
            title="09-14 세미나",
            transcript=[
                TranscriptSegment(
                    start_time=0, end_time=900, speaker="구요한", content="안녕하세요"
                )
            ],
            keywords=["옵시디언"],
        ),
        now=NOW,
    )
    return st


@pytest.fixture()
def vaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    main = tmp_path / "CMDSPACE_Local_MBP"
    wiki = tmp_path / "CMDS_LLM_Wiki"
    for v in (main, wiki):
        v.mkdir()
    from core import app_config, vault_index
    from core import vault_send as vs

    monkeypatch.setattr(app_config, "obsidian_vault", lambda: main)
    monkeypatch.setattr(vs, "wiki_vault", lambda: wiki)
    # note_context looks up related notes, which opens the real data/plaud.db.
    monkeypatch.setattr(vault_index, "related_notes", lambda *a, **k: [])
    return {"main": main, "wiki": wiki}


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep generated summaries out of the developer's real `data/`.

    summarize() writes through paths.summary_path, rooted at module-level
    directory constants. Without this a run leaves stray files in the working
    tree, and a later test silently reuses one as a cache hit — which is
    exactly how the model-failure test first passed for the wrong reason.
    """
    from core import paths as paths_mod

    root = tmp_path / "data"
    monkeypatch.setattr(paths_mod, "DATA_DIR", root)
    monkeypatch.setattr(paths_mod, "TRANSCRIPTS_DIR", root / "transcripts")
    monkeypatch.setattr(paths_mod, "SUMMARIES_DIR", root / "summaries")
    monkeypatch.setattr(paths_mod, "INTEGRATED_DIR", root / "integrated")
    # A configured override would point back at the real tree.
    monkeypatch.setattr(paths_mod, "_override", lambda kind: None)


@pytest.fixture()
def stub_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never spawn a real vendor CLI from a test."""
    from core import summarize as sm

    monkeypatch.setattr(sm, "model_available", lambda m: True)
    monkeypatch.setattr(sm, "run_model", lambda *a, **k: "## 핵심 요약\n\n시연함.")


# ----- collectionPurpose is the user's, not ours ------------------------


def test_wiki_refuses_without_a_collection_purpose(storage, vaults, stub_model) -> None:
    result = vc.compose(
        "f1", template="note-lecture", to="wiki", storage=storage, collection_purpose="  "
    )
    assert result.status == "needs_purpose"
    # Nothing may be written while a required field is missing.
    assert (
        not list((vaults["wiki"] / vc.WIKI_RAW_DEST).glob("*.md"))
        if (vaults["wiki"] / vc.WIKI_RAW_DEST).exists()
        else True
    )


def test_wiki_writes_into_the_category_subfolder_the_vault_already_uses(
    storage, vaults, stub_model
) -> None:
    result = vc.compose(
        "f1",
        template="note-lecture",
        to="wiki",
        collection_purpose="강의 설계 재사용",
        storage=storage,
    )
    assert result.status == "ok"
    # `10. Raw Sources` is what the profile resolver matches, but the vault
    # files each category in its own subfolder underneath.
    assert result.dest == "10. Raw Sources/14. Transcripts"
    written = Path(result.path)
    assert written.is_file()
    assert written.parent.name == "14. Transcripts"


def test_every_category_maps_to_an_existing_subfolder() -> None:
    from core.wiki_frontmatter import CATEGORIES

    assert set(vc.WIKI_RAW_SUBFOLDER) == set(CATEGORIES)
    for category in CATEGORIES:
        assert vc.wiki_raw_dest(category).startswith("10. Raw Sources/")


def test_filename_follows_the_vaults_date_slug_convention(storage, vaults, stub_model) -> None:
    result = vc.compose(
        "f1", template="note-lecture", to="wiki", collection_purpose="p", storage=storage
    )
    name = Path(result.path).name
    # Existing raw sources look like 2026-04-12-drive-talk-context-engineering.md
    assert re.match(r"^\d{4}-\d{2}-\d{2}-.+\.md$", name), name
    assert " " not in name


def test_slug_keeps_korean_and_drops_punctuation() -> None:
    assert vc.wiki_slug("세미나: 도구는 내가 만든다") == "세미나-도구는-내가-만든다"
    assert vc.wiki_slug("!!!") == "plaud-transcript"


def test_slug_drops_the_plaud_title_date_the_filename_already_carries() -> None:
    # Otherwise the name reads 2026-09-14-09-14-....
    assert vc.wiki_slug("09-14 세미나") == "세미나"
    # A bare number pair that is not a leading date stays put.
    assert vc.wiki_slug("Room 09-14 layout").startswith("Room")


# ----- speaker labels are not people ------------------------------------
# The parser itself (is_placeholder_speaker / human_speakers) lives in
# core.metadata and is tested in test_metadata_helpers.py.


def test_author_block_holds_no_placeholder_wikilinks(storage, vaults, stub_model) -> None:
    # A "[[speaker_0]]" author asserts a numbered placeholder wrote the note,
    # and leaves a dangling link in the vault.
    from core.models import FileContent, TranscriptSegment

    storage.save_content(
        FileContent(
            file_id="f1",
            title="09-14 세미나",
            transcript=[
                TranscriptSegment(start_time=0, end_time=9, speaker="Speaker 3", content="a"),
                TranscriptSegment(start_time=9, end_time=18, speaker="구요한", content="b"),
            ],
            keywords=[],
        ),
        now=NOW,
    )
    result = vc.compose(
        "f1", template="note-lecture", to="wiki", collection_purpose="p", storage=storage
    )
    text = Path(result.path).read_text(encoding="utf-8")
    author_block = text.split("author:", 1)[1].split("model:", 1)[0]
    assert "speaker_" not in author_block
    assert "Speaker" not in author_block


def test_date_created_is_the_recording_date_not_the_ingest_time(
    storage, vaults, stub_model
) -> None:
    result = vc.compose(
        "f1", template="note-lecture", to="wiki", collection_purpose="p", storage=storage
    )
    text = Path(result.path).read_text(encoding="utf-8")
    created = next(ln for ln in text.splitlines() if ln.startswith("date created:"))
    ingested = next(ln for ln in text.splitlines() if ln.startswith("date ingested:"))
    # `date ingested` is when it was pulled in; `date created` is the recording.
    assert created != ingested
    assert "raw-source" in text.split("tags:", 1)[1].split("source:", 1)[0]


# ----- the wiki artifact is a raw source, not a main-vault note ---------


def test_wiki_note_satisfies_the_raw_source_contract(storage, vaults, stub_model) -> None:
    result = vc.compose(
        "f1",
        template="note-lecture",
        to="wiki",
        collection_purpose="강의 설계 재사용",
        storage=storage,
    )
    text = Path(result.path).read_text(encoding="utf-8")

    assert "type: raw-source" in text
    assert "category: Transcripts" in text
    assert 'collectionPurpose: "강의 설계 재사용"' in text
    assert "status: ingested" in text
    assert "date ingested:" in text
    # The original must survive verbatim, under the required heading.
    assert "## Original Content" in text
    assert "안녕하세요" in text
    # Main-vault-only vocabulary must not leak across.
    assert "type: transcript" not in text
    assert "status: inProgress" not in text
    assert "\nCMDS:" not in text
    assert "\nindex:" not in text


def test_wiki_note_names_the_agent_rather_than_the_vault_owner(storage, vaults, stub_model) -> None:
    result = vc.compose(
        "f1", template="note-lecture", to="wiki", collection_purpose="p", storage=storage
    )
    text = Path(result.path).read_text(encoding="utf-8")
    author_block = text.split("author:", 1)[1].split("model:", 1)[0]
    # The speaker is a contributor; the agent is recorded beside them.
    assert "구요한" in author_block
    assert "Claude" in author_block or "Codex" in author_block or "Grok" in author_block


# ----- guards -----------------------------------------------------------


def test_a_recording_with_no_transcript_cannot_become_a_raw_source(
    tmp_path, vaults, stub_model
) -> None:
    st = Storage(tmp_path / "empty.db")
    st.upsert_file(PlaudFile(id="empty", filename="x"), now=NOW)
    result = vc.compose(
        "empty", template="note-lecture", to="wiki", collection_purpose="p", storage=st
    )
    assert result.status == "no_transcript"


def test_dry_run_writes_nothing(storage, vaults, stub_model) -> None:
    result = vc.compose(
        "f1",
        template="note-lecture",
        to="wiki",
        collection_purpose="p",
        dry_run=True,
        storage=storage,
    )
    assert result.status == "ok"
    assert not Path(result.path).exists()


def test_model_failure_is_reported_not_raised(storage, vaults, monkeypatch) -> None:
    from core import summarize as sm

    monkeypatch.setattr(sm, "model_available", lambda m: False)
    monkeypatch.setattr(sm, "model_unavailable_message", lambda m: "quota exhausted")
    result = vc.compose(
        "f1", template="note-lecture", to="wiki", collection_purpose="p", storage=storage
    )
    assert result.status == "model_failed"
    assert "quota" in result.detail


def test_vault_choices_lists_only_existing_vaults(vaults) -> None:
    keys = [c["key"] for c in vc.vault_choices()]
    assert keys == ["main", "wiki"]


def test_compose_result_is_json_serializable(storage, vaults, stub_model) -> None:
    result = vc.compose(
        "f1",
        template="note-lecture",
        to="wiki",
        collection_purpose="p",
        dry_run=True,
        storage=storage,
    )
    assert json.loads(json.dumps(result.as_dict()))["status"] == "ok"
