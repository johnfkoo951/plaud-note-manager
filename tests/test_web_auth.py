from __future__ import annotations

import json

from typer.testing import CliRunner

from cli.main import app
from core.config import load_config
from core.web_auth import WebAuthCapture, import_web_auth


def _clear_auth_env(monkeypatch) -> None:
    for key in (
        "PLAUD_AUTHORIZATION",
        "PLAUD_X_DEVICE_ID",
        "PLAUD_X_PLD_USER",
        "PLAUD_X_PLD_TAG",
        "PLAUD_COOKIE",
        "PLAUD_BASE_URL",
        "PLAUD_APP_LANGUAGE",
        "PLAUD_APP_PLATFORM",
        "PLAUD_EDIT_FROM",
        "PLAUD_ORIGIN",
        "PLAUD_REFERER",
        "PLAUD_TIMEZONE",
    ):
        monkeypatch.delenv(key, raising=False)


def test_import_web_auth_writes_env_from_structured_capture(tmp_path, monkeypatch) -> None:
    _clear_auth_env(monkeypatch)
    env_path = tmp_path / ".env"
    capture = WebAuthCapture.model_validate(
        {
            "authorization": "Bearer fresh.token.value",
            "x_device_id": "device-from-webkit",
            "x_pld_user": "user-from-webkit",
            "cookie": "session=abc; workspace=cmds",
            "timezone": "Asia/Seoul",
        }
    )

    result = import_web_auth(
        capture,
        env_path=env_path,
        live_validator=lambda: True,
    )

    assert result.status == "ok"
    assert result.cookie_captured is True
    written = env_path.read_text(encoding="utf-8")
    assert "PLAUD_AUTHORIZATION='Bearer fresh.token.value'" in written
    assert "PLAUD_COOKIE='session=abc; workspace=cmds'" in written

    cfg = load_config(env_path)
    assert cfg.headers()["authorization"] == "Bearer fresh.token.value"
    assert cfg.headers()["cookie"] == "session=abc; workspace=cmds"
    assert cfg.headers()["x-device-id"] == "device-from-webkit"


def test_import_web_auth_rejects_missing_required_headers(tmp_path) -> None:
    result = import_web_auth(
        {
            "authorization": "Bearer missing.user",
            "x_device_id": "device-from-webkit",
            "cookie": "session=abc",
        },
        env_path=tmp_path / ".env",
        live_validator=lambda: True,
    )

    assert result.status == "missing_required"
    assert "x_pld_user" in result.detail


def test_import_web_auth_accepts_header_only_capture_after_live_validation(
    tmp_path, monkeypatch
) -> None:
    _clear_auth_env(monkeypatch)
    env_path = tmp_path / ".env"

    result = import_web_auth(
        {
            "authorization": "Bearer header.only",
            "x_device_id": "device-from-webkit",
            "x_pld_user": "user-from-webkit",
        },
        env_path=env_path,
        live_validator=lambda: True,
    )

    assert result.status == "ok"
    assert result.cookie_captured is False
    written = env_path.read_text(encoding="utf-8")
    assert "PLAUD_AUTHORIZATION='Bearer header.only'" in written
    assert "PLAUD_COOKIE" not in written


def test_import_web_auth_restores_previous_env_when_live_validation_fails(
    tmp_path, monkeypatch
) -> None:
    _clear_auth_env(monkeypatch)
    env_path = tmp_path / ".env"
    previous = (
        "PLAUD_AUTHORIZATION='Bearer old.token'\n"
        "PLAUD_X_DEVICE_ID='old-device'\n"
        "PLAUD_X_PLD_USER='old-user'\n"
    )
    env_path.write_text(previous, encoding="utf-8")

    result = import_web_auth(
        WebAuthCapture.model_validate_json(
            json.dumps(
                {
                    "authorization": "Bearer rejected.token",
                    "x_device_id": "new-device",
                    "x_pld_user": "new-user",
                    "cookie": "session=new",
                }
            )
        ),
        env_path=env_path,
        live_validator=lambda: False,
    )

    assert result.status == "live_auth_failed"
    assert env_path.read_text(encoding="utf-8") == previous


def test_web_auth_cli_reads_structured_json_from_stdin(tmp_path, monkeypatch) -> None:
    _clear_auth_env(monkeypatch)
    env_path = tmp_path / ".env"
    monkeypatch.setenv("PLAUD_ENV_FILE", str(env_path))
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["web-auth", "--json", "--stdin", "--skip-live"],
        input=json.dumps(
            {
                "authorization": "Bearer cli.token",
                "x_device_id": "cli-device",
                "x_pld_user": "cli-user",
                "cookie": "session=cli",
            }
        ),
    )

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["status"] == "ok"
    assert "PLAUD_AUTHORIZATION='Bearer cli.token'" in env_path.read_text(encoding="utf-8")
