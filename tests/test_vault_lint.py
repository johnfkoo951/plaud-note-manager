from __future__ import annotations

import pytest

from core import app_config
from core.vault_lint import lint_lane, lint_text

LEGACY = """---
description: Dual-transcribed final transcript: 08-09 협의. Cross-analyzed.
author:
  - [[구요한]]
date created: 2026-08-15 11:30
date: 2026-08-10
type: transcript
status: inProgress
source: plaud
plaud_id: 7a77c241caa0902d2115b168ca972156
tags:
  - plaud
  - 22
  - 결정-지연
---

# 본문
"""


@pytest.fixture(autouse=True)
def _config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(app_config, "CONFIG_FILE", tmp_path / "config.json")


def test_lint_reports_all_standard_violations() -> None:
    _, issues, fixed = lint_text(LEGACY)

    assert fixed == []
    joined = "\n".join(issues)
    assert "space-separated time" in joined
    assert "description not double-quoted" in joined
    assert "bare wikilink" in joined
    assert "numeric-only" in joined
    assert "missing date modified" in joined
    assert "missing aliases" in joined
    assert "missing model/effort" in joined


def test_fix_rewrites_only_frontmatter_lines_and_keeps_body() -> None:
    new, issues, fixed = lint_text(LEGACY, fix=True, model_label="gpt-5.6-sol")

    assert "date created: 2026-08-15T11:30" in new
    assert "date modified: 2026-08-15T11:30" in new
    assert 'description: "Dual-transcribed final transcript: 08-09 협의. Cross-analyzed."' in new
    assert '  - "[[구요한]]"' in new
    assert "  - 22\n" not in new and "  - 결정-지연" in new
    assert "aliases: []" in new
    assert 'model: "gpt-5.6-sol"\neffort: "medium"' in new
    assert new.endswith("# 본문\n")
    # Second pass is clean.
    _, issues2, _ = lint_text(new)
    assert issues2 == []


def test_lint_lane_skips_non_plaud_notes_and_writes_fixes(tmp_path) -> None:
    lane = tmp_path / "00. Inbox/08. Transcripts/08-1. Plaud"
    lane.mkdir(parents=True)
    (lane / "a.transcript.md").write_text(LEGACY, encoding="utf-8")
    (lane / "other.md").write_text("---\ntype: note\nsource: manual\n---\nx\n", encoding="utf-8")

    dry = lint_lane(tmp_path)
    assert [r.path.name for r in dry] == ["a.transcript.md"]
    assert not dry[0].ok

    fixed = lint_lane(tmp_path, fix=True, model_label="claude-fable-5")
    assert fixed[0].fixed
    assert "date created: 2026-08-15T11:30" in (lane / "a.transcript.md").read_text(
        encoding="utf-8"
    )
    assert lint_lane(tmp_path)[0].ok
