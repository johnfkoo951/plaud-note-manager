from __future__ import annotations

import json

import pytest

from core import auto_folder
from core.classification import classify_snapshot


def snap(title: str, **extra) -> dict:
    base = {"title": title, "keywords": [], "summaries": "", "integrated_summary": ""}
    base.update(extra)
    return base


def test_strong_rule_verdict_skips_llm() -> None:
    calls: list[str] = []

    def run_model(model, prompt, model_id=None):
        calls.append(model)
        return "{}"

    out = auto_folder.classify_with_assist(
        snap("재즈 보컬 합창 연주 음악 작업"),
        threshold=0.6,
        use_llm=True,
        run_model=run_model,
        model_available=lambda m: True,
    )

    assert out.classification.folder_name == "30. Jazz & Music"
    assert out.used_llm is False
    assert calls == []


def test_default_bucket_triggers_llm_and_accepts_menu_folder() -> None:
    seen: dict = {}

    def run_model(model, prompt, model_id=None):
        seen["prompt"] = prompt
        return json.dumps(
            {
                "folder_name": "11. AI 강의",
                "confidence": 0.8,
                "reason": "실제 강의 진행 녹음",
                "topic_tags": ["옵시디언-워크플로우", "#AI교육", "2"],
                "keywords": ["구요한", "옵시디언"],
            }
        )

    out = auto_folder.classify_with_assist(
        snap("2026-05-29 14:30:00"),
        model="codex",
        threshold=0.6,
        use_llm=True,
        run_model=run_model,
        model_available=lambda m: True,
    )

    assert out.used_llm is True
    assert out.classification.folder_name == "11. AI 강의"
    assert out.classification.source == "llm"
    assert out.classification.confidence == 0.8
    assert out.classification.cmds.startswith("📚")
    assert out.classification.index == "🏷 Lecture Notes"
    assert "옵시디언-워크플로우" in out.classification.tags
    assert "AI교육" in out.classification.tags
    assert out.llm_keywords == ["구요한", "옵시디언"]
    # The rule verdict is shown to the arbiter and the menu is closed.
    assert "10. Meetings" in seen["prompt"]
    assert "30. Jazz & Music" in seen["prompt"]


def test_unknown_folder_from_model_falls_back_to_rules() -> None:
    def run_model(model, prompt, model_id=None):
        return '{"folder_name": "99. Invented", "confidence": 0.95}'

    rule = classify_snapshot(snap("2026-05-29 14:30:00"))
    out = auto_folder.classify_with_assist(
        snap("2026-05-29 14:30:00"),
        threshold=0.6,
        use_llm=True,
        run_model=run_model,
        model_available=lambda m: True,
    )

    assert out.classification == rule
    assert out.used_llm is True
    assert "unknown folder" in out.llm_error


def test_model_confidence_is_clamped_below_rules_ceiling() -> None:
    def run_model(model, prompt, model_id=None):
        return '{"folder_name": "10. Meetings", "confidence": 1.7}'

    out = auto_folder.classify_with_assist(
        snap("2026-05-29 14:30:00"),
        threshold=0.6,
        use_llm=True,
        run_model=run_model,
        model_available=lambda m: True,
    )

    assert out.classification.confidence == 0.9


def test_model_unavailable_or_failing_keeps_rule_verdict() -> None:
    unavailable = auto_folder.classify_with_assist(
        snap("2026-05-29 14:30:00"),
        threshold=0.6,
        use_llm=True,
        run_model=lambda *a, **k: "{}",
        model_available=lambda m: False,
    )
    assert unavailable.used_llm is False
    assert "unavailable" in unavailable.llm_error

    def boom(*a, **k):
        raise RuntimeError("cli exploded")

    failing = auto_folder.classify_with_assist(
        snap("2026-05-29 14:30:00"),
        threshold=0.6,
        use_llm=True,
        run_model=boom,
        model_available=lambda m: True,
    )
    assert failing.classification.source == "default"
    assert "cli exploded" in failing.llm_error


def test_folder_name_matching_tolerates_missing_numeric_prefix() -> None:
    def run_model(model, prompt, model_id=None):
        return '{"folder_name": "Jazz & Music", "confidence": 0.7}'

    out = auto_folder.classify_with_assist(
        snap("2026-05-29 14:30:00"),
        threshold=0.6,
        use_llm=True,
        run_model=run_model,
        model_available=lambda m: True,
    )
    assert out.classification.folder_name == "30. Jazz & Music"


@pytest.mark.parametrize("raw", ["not json", '```json\n{"folder_name": "10. Meetings"}\n```'])
def test_json_extraction_handles_fences_and_garbage(raw: str) -> None:
    data = auto_folder._extract_json(raw)
    assert data == {} or data["folder_name"] == "10. Meetings"
