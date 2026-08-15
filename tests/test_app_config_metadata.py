from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import app_config


@pytest.fixture()
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "CONFIG_FILE", path)
    return path


def test_metadata_model_defaults_to_codex(config_file: Path) -> None:
    assert app_config.metadata_model() == "codex"


def test_metadata_model_set_and_read_back(config_file: Path) -> None:
    app_config.set_metadata_model("claude")
    assert app_config.metadata_model() == "claude"
    assert json.loads(config_file.read_text())["metadata_model"] == "claude"


def test_metadata_model_falls_back_to_classify_model(config_file: Path) -> None:
    # Older configs (pre-v0.6) have no metadata_model key; an explicit empty
    # value must defer to classify_model, not silently pick codex.
    config_file.write_text(
        json.dumps({"metadata_model": "", "classify_model": "gemini"}),
        encoding="utf-8",
    )
    assert app_config.metadata_model() == "gemini"


def test_auto_metadata_default_on_and_toggle(config_file: Path) -> None:
    assert app_config.auto_metadata_enabled() is True
    app_config.set_auto_metadata(False)
    assert app_config.auto_metadata_enabled() is False
    app_config.set_auto_metadata(True)
    assert app_config.auto_metadata_enabled() is True


def test_auto_metadata_limit_guards_bad_values(config_file: Path) -> None:
    assert app_config.auto_metadata_limit() == 20
    cfg = app_config.load()
    cfg["auto_metadata_limit"] = 0
    app_config.save(cfg)
    assert app_config.auto_metadata_limit() == 1
    cfg["auto_metadata_limit"] = "nope"
    app_config.save(cfg)
    assert app_config.auto_metadata_limit() == 20
