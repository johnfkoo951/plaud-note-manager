from pathlib import Path
from textwrap import dedent

from core.model_registry import list_model_presets, presets_json


def write_model(path: Path, body: str) -> None:
    path.write_text(dedent(body).strip() + "\n", encoding="utf-8")


def test_model_presets_are_loaded_from_api_information_notes(tmp_path) -> None:
    write_model(
        tmp_path / "gpt.md",
        """
        ---
        provider: [[OpenAI]]
        api_name: gpt-5.4
        model_name: GPT-5.4
        description: Flagship
        status: active
        is_sota: true
        output_modalities:
          - text
        tags: [llm-model, chat]
        ---
        body
        """,
    )
    write_model(
        tmp_path / "grok.md",
        """
        ---
        provider: [[xAI]]
        api_name: grok-4.20-0309-reasoning
        model_name: Grok 4.20
        status: preview
        is_sota: true
        output_modalities:
          - text
        tags:
          - llm-model
          - reasoning
        ---
        body
        """,
    )

    presets = list_model_presets(tmp_path)

    assert [(p.provider, p.api_name) for p in presets] == [
        ("codex", "gpt-5.4"),
        ("grok", "grok-4.20-0309-reasoning"),
    ]
    assert presets[0].provider_label == "OpenAI"
    assert presets_json(tmp_path).startswith("[")


def test_model_presets_filter_inactive_or_non_text_notes(tmp_path) -> None:
    write_model(
        tmp_path / "old.md",
        """
        ---
        provider: [[Anthropic]]
        api_name: claude-old
        status: deprecated
        output_modalities:
          - text
        ---
        """,
    )
    write_model(
        tmp_path / "image.md",
        """
        ---
        provider: [[Google]]
        api_name: imagen-test
        status: active
        output_modalities:
          - image
        ---
        """,
    )
    write_model(
        tmp_path / "ok.md",
        """
        ---
        provider: [[Anthropic]]
        api_name: claude-opus-4-7
        model_name: Claude Opus 4.7
        status: active
        is_sota: true
        output_modalities:
          - text
        ---
        """,
    )

    presets = list_model_presets(tmp_path)

    assert [(p.provider, p.api_name) for p in presets] == [("claude", "claude-opus-4-7")]
