"""Contract tests for the purpose-specific `note-*` templates.

These templates are rendered through `summarize()`, which supplies exactly four
variables and unconditionally prepends its own wrapper frontmatter to the saved
file. So each template must (a) use only those four placeholders and (b) never
instruct the model to emit YAML of its own — otherwise the output file ends up
with two stacked frontmatter blocks.
"""

from __future__ import annotations

import re

import pytest

from core.templates import list_templates, load_template

NOTE_TEMPLATES = sorted(t.name for t in list_templates() if t.name.startswith("note-"))

# What summarize() actually passes — core/summarize.py summarize().
SUMMARIZE_VARS = {"transcript", "title", "keywords", "speakers"}

EXPECTED = {
    "note-meeting",
    "note-lecture",
    "note-knowledge",
    "note-consulting",
    "note-product",
    "note-interview",
    "note-spiritual",
    "note-personal",
    "note-finance",
    "note-general",
}


def test_every_expected_template_is_present() -> None:
    assert set(NOTE_TEMPLATES) == EXPECTED


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_template_declares_a_description(name: str) -> None:
    # `plaud templates` and the settings viewer both show this.
    assert load_template(name).description.strip()


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_template_uses_only_variables_summarize_supplies(name: str) -> None:
    body = load_template(name).body
    # Placeholders are `{name}` tokens; the shared-rule prose has no braces.
    used = set(re.findall(r"\{([a-z_]+)\}", body))
    assert used <= SUMMARIZE_VARS, f"{name} uses unsupported {used - SUMMARIZE_VARS}"
    # The transcript is the point; a template that forgets it summarizes nothing.
    assert "transcript" in used


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_template_renders_with_every_placeholder_substituted(name: str) -> None:
    rendered = load_template(name).render(
        transcript="[0.0s] speaker_0: 안녕하세요",
        title="테스트 녹음",
        keywords="a, b",
        speakers="speaker_0",
    )
    assert "{transcript}" not in rendered
    assert "테스트 녹음" in rendered
    assert not re.search(r"\{(transcript|title|keywords|speakers)\}", rendered)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_template_forbids_emitting_frontmatter(name: str) -> None:
    # summarize() already prepends file_id/provider/model/template frontmatter.
    body = load_template(name).body
    assert "YAML 프론트매터를 쓰지 않는다" in body


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_template_forbids_invention(name: str) -> None:
    body = load_template(name).body
    assert "전사본에 없는 것을 만들지 않는다" in body


def test_output_sections_are_markdown_headings() -> None:
    for name in EXPECTED:
        body = load_template(name).body
        spec = body.split("# 출력 형식", 1)[1]
        assert re.search(r"^## ", spec, re.MULTILINE), f"{name} declares no sections"


def test_finance_template_guards_numbers_hardest() -> None:
    # Wrong amounts are the expensive failure for this one, so the rule is
    # stated in the template itself rather than left to the shared preamble.
    body = load_template("note-finance").body
    assert "추정 금지" in body
    assert "확인 필요" in body
