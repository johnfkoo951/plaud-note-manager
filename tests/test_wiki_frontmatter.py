"""The wiki vault's raw-source contract, which differs from the mothership's.

Eleven required fields instead of seven, its own `type` vocabulary, and an
author policy that is the opposite of the main vault's — these tests pin the
differences so a future edit cannot quietly collapse the two contracts.
"""

from __future__ import annotations

import pytest

from core import wiki_frontmatter as wf
from core.wiki_frontmatter import (
    ORIGINAL_CONTENT_HEADING,
    WikiRawSourceFrontmatter,
    WikiSchemaError,
    agent_label,
    build_raw_source_note,
)


def _fm(**over) -> WikiRawSourceFrontmatter:
    base = dict(
        description="Seminar transcript on Obsidian plugin development.",
        collection_purpose="강의 설계 재사용",
        source="https://web.plaud.ai/file/abc123",
        agent="Claude",
        model="claude-opus-5",
        effort="medium",
    )
    base.update(over)
    return WikiRawSourceFrontmatter(**base)


def _keys(fm: WikiRawSourceFrontmatter) -> list[str]:
    out = []
    for line in fm.lines():
        if line.startswith(("---", "  ", " ")):
            continue
        if ":" in line:
            out.append(line.split(":", 1)[0])
    return out


# ----- the eleven required fields --------------------------------------


def test_all_eleven_required_fields_are_emitted() -> None:
    required = {
        "type",
        "aliases",
        "description",
        "author",
        "date created",
        "date modified",
        "tags",
        "source",
        "date ingested",
        "category",
        "collectionPurpose",
    }
    assert required <= set(_keys(_fm()))


def test_type_is_the_wiki_vocabulary_not_the_mothership_one() -> None:
    # `transcript` and `meeting` do not exist in the wiki type vocabulary.
    assert "type: raw-source" in _fm().lines()


def test_category_defaults_to_transcripts() -> None:
    assert "category: Transcripts" in _fm().lines()


def test_status_defaults_to_the_standard_ingest_value() -> None:
    assert "status: ingested" in _fm().lines()


# ----- author policy is inverted from the main vault --------------------


def test_author_records_contributors_and_the_agent_not_a_fixed_owner() -> None:
    lines = _fm(authors=["구요한", "안창현"], agent="Claude").lines()
    block = lines[lines.index("author:") + 1 : lines.index("author:") + 4]
    assert block == ['  - "[[구요한]]"', '  - "[[안창현]]"', "  - Claude"]


def test_model_and_effort_follow_author_immediately() -> None:
    keys = _keys(_fm())
    assert keys[keys.index("author") + 1] == "model"
    assert keys[keys.index("author") + 2] == "effort"


def test_agent_label_names_the_agent_not_the_model_id() -> None:
    assert agent_label("claude") == "Claude"
    assert agent_label("codex") == "Codex"
    assert agent_label("") == "unknown"


# ----- validation -------------------------------------------------------


def test_collection_purpose_is_required() -> None:
    # The schema says the user states this; it must not be inferred.
    with pytest.raises(WikiSchemaError, match="collectionPurpose"):
        _fm(collection_purpose="  ").lines()


def test_description_and_source_are_required() -> None:
    with pytest.raises(WikiSchemaError, match="description"):
        _fm(description="").lines()
    with pytest.raises(WikiSchemaError, match="source"):
        _fm(source="").lines()


def test_author_cannot_be_entirely_absent() -> None:
    with pytest.raises(WikiSchemaError, match="author"):
        _fm(agent="", authors=[]).lines()


@pytest.mark.parametrize("bad", ["Transcript", "transcripts", "Video"])
def test_category_is_held_to_the_enum(bad: str) -> None:
    with pytest.raises(WikiSchemaError, match="category"):
        _fm(category=bad).lines()


def test_status_is_held_to_the_enum() -> None:
    # The mothership's `inProgress` is not a wiki status.
    with pytest.raises(WikiSchemaError, match="status"):
        _fm(status="inProgress").lines()


# ----- YAML safety ------------------------------------------------------


def test_free_text_is_double_quoted() -> None:
    lines = _fm(
        description='He said: "ship it" — now',
        collection_purpose="목적: 재사용 #1",
    ).lines()
    desc = next(ln for ln in lines if ln.startswith("description:"))
    purpose = next(ln for ln in lines if ln.startswith("collectionPurpose:"))
    # A bare `: ` inside a plain scalar silently corrupts the rest of the block.
    assert desc.startswith('description: "') and desc.endswith('"')
    assert purpose.startswith('collectionPurpose: "') and purpose.endswith('"')


def test_numeric_only_tags_are_dropped() -> None:
    lines = _fm(tags=["plaud", "22", "2026", "옵시디언"]).lines()
    tags = [ln.strip("- ").strip() for ln in lines if ln.startswith("  - ")]
    assert "22" not in tags and "2026" not in tags
    assert "plaud" in tags


def test_cross_vault_links_use_advanced_uri_never_wikilinks() -> None:
    lines = _fm(
        source_vault="CMDSPACE_Local_MBP",
        main_vault_related=[{"title": "Pipeline", "rel_path": "30. Permanent Notes/Pipeline"}],
    ).lines()
    link = next(ln for ln in lines if "advanced-uri" in ln)
    assert "obsidian://advanced-uri?vault=CMDSPACE_Local_MBP" in link
    assert "[[" not in link


def test_main_vault_related_skips_non_main_entries() -> None:
    lines = _fm(
        source_vault="CMDSPACE_Local_MBP",
        main_vault_related=[
            {"title": "Pipeline", "rel_path": "30. Notes/Pipeline", "vault": "CMDSPACE_Local_MBP"},
            {"title": "Concept", "rel_path": "20. Wiki/Concept", "vault": "CMDS_LLM_Wiki"},
        ],
    ).lines()
    links = [ln for ln in lines if "advanced-uri" in ln]
    assert links == [
        '  - "[Mothership: Pipeline](obsidian://advanced-uri?vault=CMDSPACE_Local_MBP'
        '&filepath=30.%20Notes%2FPipeline.md)"'
    ]


def test_main_vault_related_needs_a_source_vault_to_be_addressable() -> None:
    lines = _fm(main_vault_related=[{"title": "Pipeline", "rel_path": "x/Pipeline"}]).lines()
    assert not any("mainVaultRelated" in ln for ln in lines)


# ----- body -------------------------------------------------------------


def test_body_preserves_the_original_under_its_required_heading() -> None:
    note = build_raw_source_note(
        frontmatter=_fm(),
        title="세미나",
        original_content="[0:02] 구요한: 안녕하세요",
        summary="## 핵심 요약\n\n시연함.",
    )
    assert ORIGINAL_CONTENT_HEADING in note
    assert "[0:02] 구요한: 안녕하세요" in note
    # The original must come after the summary, and be marked as the record.
    assert note.index("## 핵심 요약") < note.index(ORIGINAL_CONTENT_HEADING)
    assert "source of record" in note


def test_a_summary_with_its_own_heading_is_not_nested_under_another() -> None:
    note = build_raw_source_note(
        frontmatter=_fm(), title="T", original_content="body", summary="## 핵심 요약\n\n내용"
    )
    assert "## Summary" not in note


def test_a_summary_without_a_heading_gets_one() -> None:
    note = build_raw_source_note(
        frontmatter=_fm(), title="T", original_content="body", summary="평문 요약"
    )
    assert "## Summary" in note


def test_a_raw_source_with_no_original_content_is_refused() -> None:
    with pytest.raises(WikiSchemaError, match="original content"):
        build_raw_source_note(frontmatter=_fm(), title="T", original_content="   ")


def test_enums_match_the_vault_schema() -> None:
    assert wf.CATEGORIES == (
        "Articles",
        "Papers",
        "Books",
        "Transcripts",
        "Clippings",
        "AI Research",
    )
    assert wf.STATUSES == ("ingested", "stub", "reading", "completed")
