from pathlib import Path

import pytest

from core import summarize


# -------- _api_key_env mapping --------


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude", "ANTHROPIC_API_KEY"),
        ("codex", "OPENAI_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("grok", "XAI_API_KEY"),
        ("unknown", ""),
    ],
)
def test_api_key_env_mapping(model: str, expected: str) -> None:
    assert summarize._api_key_env(model) == expected


# -------- model_unavailable_message branches --------


def _patch_backend(monkeypatch, backend: str) -> None:
    monkeypatch.setattr(summarize.app_config, "backend_for", lambda _model: backend)


def test_unavailable_message_cli_known_model(monkeypatch) -> None:
    _patch_backend(monkeypatch, "cli")
    msg = summarize.model_unavailable_message("claude")
    assert "`claude` CLI not installed" in msg
    assert "Switch claude to api" in msg


def test_unavailable_message_cli_grok_points_to_install(monkeypatch) -> None:
    # Grok now HAS a CLI backend (Grok Build, SuperGrok OAuth); when the binary
    # is missing the message offers install-or-switch like the other CLIs.
    _patch_backend(monkeypatch, "cli")
    monkeypatch.setattr(summarize, "_resolve_binary", lambda model, name: None)
    msg = summarize.model_unavailable_message("grok")
    assert "`grok` CLI not installed" in msg
    assert "Switch grok to api" in msg


def test_unavailable_message_cli_unknown_model(monkeypatch) -> None:
    _patch_backend(monkeypatch, "cli")
    msg = summarize.model_unavailable_message("nope")
    assert msg == "unknown CLI model: nope"


def test_unavailable_message_api_known_model(monkeypatch) -> None:
    _patch_backend(monkeypatch, "api")
    msg = summarize.model_unavailable_message("claude")
    assert msg == "ANTHROPIC_API_KEY not set for claude API backend."


def test_unavailable_message_api_unknown_model(monkeypatch) -> None:
    _patch_backend(monkeypatch, "api")
    msg = summarize.model_unavailable_message("nope")
    assert msg == "unknown API model: nope"


def test_unavailable_message_bogus_backend(monkeypatch) -> None:
    _patch_backend(monkeypatch, "weirdbackend")
    msg = summarize.model_unavailable_message("claude")
    assert msg == "unknown backend for claude: weirdbackend"


# -------- _zshrc_value --------


def _write_zshrc(monkeypatch, tmp_path: Path, body: str) -> None:
    (tmp_path / ".zshrc").write_text(body, encoding="utf-8")
    monkeypatch.setattr(summarize.Path, "home", staticmethod(lambda: tmp_path))


def test_zshrc_value_double_quoted_with_spaces(monkeypatch, tmp_path) -> None:
    # POST-FIX: double-quoted values may contain spaces.
    _write_zshrc(monkeypatch, tmp_path, 'export FOO="a b c"\n')
    assert summarize._zshrc_value("FOO") == "a b c"


def test_zshrc_value_single_quoted(monkeypatch, tmp_path) -> None:
    _write_zshrc(monkeypatch, tmp_path, "export BAR='secret value'\n")
    assert summarize._zshrc_value("BAR") == "secret value"


def test_zshrc_value_bare(monkeypatch, tmp_path) -> None:
    _write_zshrc(monkeypatch, tmp_path, "export BAZ=plain-token-123\n")
    assert summarize._zshrc_value("BAZ") == "plain-token-123"


def test_zshrc_value_absent_var(monkeypatch, tmp_path) -> None:
    _write_zshrc(monkeypatch, tmp_path, 'export OTHER="value"\n')
    assert summarize._zshrc_value("MISSING") is None


def test_zshrc_value_no_zshrc_file(monkeypatch, tmp_path) -> None:
    # No .zshrc written at all -> file does not exist -> None.
    monkeypatch.setattr(summarize.Path, "home", staticmethod(lambda: tmp_path))
    assert summarize._zshrc_value("ANYTHING") is None
