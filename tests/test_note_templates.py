"""Contract tests for the purpose-specific `note-*` templates.

These templates are rendered through `summarize()`, which supplies exactly four
variables and unconditionally prepends its own wrapper frontmatter to the saved
file. So each template must (a) use only those four placeholders and (b) never
instruct the model to emit YAML of its own — otherwise the output file ends up
with two stacked frontmatter blocks.
"""

from __future__ import annotations

import re
from collections import Counter

import pytest

from core.paths import TEMPLATES_DIR
from core.templates import (
    KIND_INTEGRATED,
    KIND_INTERNAL,
    KIND_SUMMARY,
    list_templates,
    load_template,
    parse_frontmatter,
)

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


# What integrate() passes on top of title/keywords/speakers.
INTEGRATE_VARS = {"cmds_transcript", "plaud_transcript", "plaud_summaries", "transcription_context"}

SHIPPED_KINDS = {
    "default": KIND_INTEGRATED,
    "integrated": KIND_INTEGRATED,
    "classify": KIND_INTERNAL,
    "metadata": KIND_INTERNAL,
    "speaker-names": KIND_INTERNAL,
    "vault-meeting-note": KIND_INTERNAL,
    **dict.fromkeys(EXPECTED, KIND_SUMMARY),
    "cmds-meeting": KIND_SUMMARY,
    "cmds-lecture": KIND_SUMMARY,
    "cmds-coaching": KIND_SUMMARY,
    "meeting": KIND_SUMMARY,
    "lecture": KIND_SUMMARY,
}


def _placeholders(body: str) -> set[str]:
    return set(re.findall(r"\{([a-z_]+)\}", body))


def test_every_expected_template_is_present() -> None:
    assert set(NOTE_TEMPLATES) == EXPECTED


def test_every_shipped_template_loads() -> None:
    names = sorted(p.stem for p in TEMPLATES_DIR.glob("*.md"))
    assert len(names) == 21
    for name in names:
        load_template(name)  # list_templates() would swallow a failure


def test_every_shipped_template_declares_its_kind() -> None:
    for path in sorted(TEMPLATES_DIR.glob("*.md")):
        _lines, meta, _body = parse_frontmatter(path.read_bytes().decode("utf-8"))
        # Declared, not defaulted: a missing line would also load as summary.
        assert meta.get("kind") in (KIND_SUMMARY, KIND_INTEGRATED, KIND_INTERNAL), path.name
    kinds = {t.name: t.kind for t in list_templates()}
    assert kinds == SHIPPED_KINDS
    assert Counter(kinds.values()) == {KIND_INTEGRATED: 2, KIND_INTERNAL: 4, KIND_SUMMARY: 15}


@pytest.mark.parametrize("name", sorted(n for n, k in SHIPPED_KINDS.items() if k == KIND_SUMMARY))
def test_summary_templates_take_the_transcript_and_no_integrate_inputs(name: str) -> None:
    used = _placeholders(load_template(name).body)
    assert "transcript" in used
    assert not used & INTEGRATE_VARS, f"{name} uses {used & INTEGRATE_VARS}"


@pytest.mark.parametrize(
    "name", sorted(n for n, k in SHIPPED_KINDS.items() if k == KIND_INTEGRATED)
)
def test_integrated_templates_take_the_integrate_inputs_and_no_transcript(name: str) -> None:
    used = _placeholders(load_template(name).body)
    assert used & INTEGRATE_VARS
    assert "transcript" not in used


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
