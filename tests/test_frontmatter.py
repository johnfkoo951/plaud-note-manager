from __future__ import annotations

from datetime import datetime

import pytest

from core import app_config
from core.frontmatter import CmdsFrontmatter, clean_tags, iso_minute, wikilink, yaml_quote


@pytest.fixture(autouse=True)
def _config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(app_config, "CONFIG_FILE", tmp_path / "config.json")


def test_required_seven_fields_in_standard_order() -> None:
    fm = CmdsFrontmatter(
        type="transcript",
        description='Says "hi": ok',
        tags=["plaud", "transcript"],
        author="구요한",
        model="gpt-5.6-sol",
        date_created="2026-09-03T10:00",
    )
    lines = fm.lines()

    assert lines[0] == "---" and lines[-1] == "---"
    keys = [ln.split(":", 1)[0] for ln in lines[1:-1] if not ln.startswith("  ")]
    assert keys[:9] == [
        "type",
        "aliases",
        "description",
        "author",
        "model",
        "effort",
        "date created",
        "date modified",
        "tags",
    ]
    assert 'description: "Says \\"hi\\": ok"' in lines
    assert '  - "[[구요한]]"' in lines
    assert 'model: "gpt-5.6-sol"' in lines
    assert 'effort: "medium"' in lines
    assert "date modified: 2026-09-03T10:00" in lines  # defaults to created


def test_cmds_and_index_are_quoted_wikilinks_and_lane_extras_follow() -> None:
    fm = CmdsFrontmatter(
        type="meeting",
        description="x",
        cmds="📚 840 Lectures",
        index="[[🏷 Lecture Notes]]",
        plaud_id="abc123",
        dual=True,
        speakers=["구요한", "홍길동"],
        keywords=["LG", "AX: 캠프"],
        related=["홍길동", "LG AX Camp"],
        reuse_channels=["newsletter:flagged"],
        date="2026-08-10",
    )
    text = fm.render()

    assert 'CMDS: "[[📚 840 Lectures]]"' in text
    assert 'index: "[[🏷 Lecture Notes]]"' in text  # brackets not doubled
    assert "plaud_id: abc123" in text
    assert "plaud_url: https://web.plaud.ai/file/abc123" in text
    assert "dual: true" in text
    assert 'speakers:\n  - "[[구요한]]"\n  - "[[홍길동]]"' in text
    assert 'keywords:\n  - "LG"\n  - "AX: 캠프"' in text
    assert 'related:\n  - "[[홍길동]]"\n  - "[[LG AX Camp]]"' in text
    assert "reuse-channels:\n  - newsletter:flagged" in text
    assert "date: 2026-08-10" in text


def test_numeric_only_tags_are_dropped() -> None:
    assert clean_tags(["plaud", "#22", "2026", "ax-camp", "3-2"]) == ["plaud", "ax-camp"]


def test_helpers() -> None:
    assert wikilink("[[x]]") == '"[[x]]"'
    assert yaml_quote('a"b') == '"a\\"b"'
    assert iso_minute(datetime(2026, 9, 3, 7, 5)) == "2026-09-03T07:05"


def test_wiki_target_uses_advanced_uri_instead_of_wikilinks() -> None:
    fm = CmdsFrontmatter(
        type="note",
        description="x",
        source_vault="CMDSPACE_Local_MBP",
        main_vault_related=[
            {"title": "홍길동", "rel_path": "60. Collections/61. People/홍길동.md"}
        ],
    )
    text = fm.render()

    assert "source-vault: CMDSPACE_Local_MBP" in text
    assert "related:" not in text.replace("mainVaultRelated:", "")
    assert (
        'mainVaultRelated:\n  - "[Mothership: 홍길동](obsidian://advanced-uri'
        "?vault=CMDSPACE_Local_MBP"
        '&filepath=60.%20Collections%2F61.%20People%2F%ED%99%8D%EA%B8%B8%EB%8F%99.md)"'
    ) in text


def test_cross_vault_link_format() -> None:
    from core.frontmatter import MAIN_VAULT_LABEL, WIKI_VAULT_LABEL, cross_vault_link

    assert (MAIN_VAULT_LABEL, WIKI_VAULT_LABEL) == ("Mothership: ", "LLM Wiki: ")
    assert cross_vault_link(
        "LLM Wiki: LLM Wiki Pattern", "CMDS_LLM_Wiki", "20. Wiki/21. Concepts/LLM Wiki Pattern"
    ) == (
        "[LLM Wiki: LLM Wiki Pattern](obsidian://advanced-uri?vault=CMDS_LLM_Wiki"
        "&filepath=20.%20Wiki%2F21.%20Concepts%2FLLM%20Wiki%20Pattern.md)"
    )
    # A vault name with a space, and a path that already ends in .md.
    assert cross_vault_link("x", "My Vault", "a/b.md") == (
        "[x](obsidian://advanced-uri?vault=My%20Vault&filepath=a%2Fb.md)"
    )


def test_wiki_vault_related_renders_advanced_uri_never_wikilinks() -> None:
    fm = CmdsFrontmatter(
        type="note",
        description="x",
        related=["Obsidian"],
        wiki_vault_related=[
            {
                "title": "LLM Wiki Pattern",
                "rel_path": "20. Wiki/21. Concepts/LLM Wiki Pattern.md",
                "vault": "CMDS_LLM_Wiki",
            },
            {"title": "no path", "vault": "CMDS_LLM_Wiki"},  # incomplete: skipped
        ],
    )
    text = fm.render()
    assert (
        'related:\n  - "[[Obsidian]]"\nwikiVaultRelated:\n'
        '  - "[LLM Wiki: LLM Wiki Pattern](obsidian://advanced-uri?vault=CMDS_LLM_Wiki'
        '&filepath=20.%20Wiki%2F21.%20Concepts%2FLLM%20Wiki%20Pattern.md)"\n'
    ) in text
    assert "[[LLM Wiki Pattern]]" not in text and "no path" not in text


def test_main_vault_related_drops_entries_from_other_vaults() -> None:
    fm = CmdsFrontmatter(
        type="note",
        description="x",
        source_vault="Main",
        main_vault_related=[
            {"title": "Here", "rel_path": "a/Here.md", "vault": "Main"},
            {"title": "Elsewhere", "rel_path": "w/Elsewhere.md", "vault": "CMDS_LLM_Wiki"},
            {"title": "Unlabelled", "rel_path": "a/Unlabelled.md"},  # counts as Main
        ],
    )
    text = fm.render()
    assert "Mothership: Here" in text and "Mothership: Unlabelled" in text
    assert "Elsewhere" not in text
