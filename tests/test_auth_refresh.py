import json

import pytest
from typer.testing import CliRunner

import core.refresh_auth as refresh_mod
from cli.main import app
from core.config import load_config
from core.refresh_auth import RefreshResult, refresh_auth
from core.secret_store import load_credential_values, update_credential_values
from tests.test_auth_status import _make_jwt

FAR_FUTURE = 4_102_444_800

VALID_CURL = """
curl 'https://api-apne1.plaud.ai/filetag/' \\
  -H 'authorization: Bearer test.token.value' \\
  -H 'x-device-id: device-123' \\
  -H 'x-pld-user: user-1234567890123456' \\
  -H 'x-pld-tag: legacy-tag' \\
  -H 'cookie: sessionid=abc; workspace=cmds'
"""

CURRENT_WEB_CURL = """
curl 'https://api-apne1.plaud.ai/summary/community/templates/weekly_recommend' \\
  -H 'accept: application/json, text/plain, */*' \\
  -H 'app-language: en' \\
  -H 'app-platform: web' \\
  -H 'authorization: bearer header.payload.signature' \\
  -H 'content-type: application/json' \\
  -b 'session=abc; preference=ko' \\
  -H 'edit-from: web' \\
  -H 'origin: https://web.plaud.ai' \\
  -H 'timezone: Asia/Seoul' \\
  -H 'x-device-id: current-device' \\
  --data-raw '{"language_os":"en"}'
"""


def _workspace_curl(workspace_id: str, *, expires_at: int = FAR_FUTURE) -> str:
    token = _make_jwt({"exp": expires_at, "wid": workspace_id})
    return f"""
curl 'https://api-apne1.plaud.ai/file/simple/web' \\
  -H 'authorization: bearer {token}' \\
  -H 'x-device-id: workspace-device'
"""


def _seed_workspace_renewal(
    env_path, *, workspace_id: str = "ws_abc", refresh_expires_at: int = FAR_FUTURE
) -> None:
    update_credential_values(
        {
            "PLAUD_AUTHORIZATION": (
                "bearer " + _make_jwt({"exp": FAR_FUTURE, "wid": workspace_id})
            ),
            "PLAUD_X_DEVICE_ID": "stored-device",
            "PLAUD_WORKSPACE_ID": workspace_id,
            "PLAUD_WS_REFRESH_TOKEN": "stored-refresh-token",
            "PLAUD_WS_REFRESH_EXPIRES_AT": str(refresh_expires_at),
        },
        env_path,
    )


def test_refresh_auth_keeps_curl_clipboard_concept_and_cookie(tmp_path, monkeypatch) -> None:
    for key in (
        "PLAUD_AUTHORIZATION",
        "PLAUD_X_DEVICE_ID",
        "PLAUD_X_PLD_USER",
        "PLAUD_X_PLD_TAG",
        "PLAUD_COOKIE",
    ):
        monkeypatch.delenv(key, raising=False)

    env_path = tmp_path / ".env"

    result = refresh_auth(env_path=env_path, curl_text=VALID_CURL)

    assert result.status == "ok"
    assert result.cookie_captured is True
    written = env_path.read_text(encoding="utf-8")
    assert "PLAUD_COOKIE='sessionid=abc; workspace=cmds'" in written

    cfg = load_config(env_path)
    assert cfg.headers()["cookie"] == "sessionid=abc; workspace=cmds"
    assert cfg.headers()["x-pld-tag"] == "legacy-tag"


def test_refresh_auth_accepts_current_weekly_curl_without_legacy_user(
    tmp_path, monkeypatch
) -> None:
    for key in (
        "PLAUD_AUTHORIZATION",
        "PLAUD_X_DEVICE_ID",
        "PLAUD_X_PLD_USER",
        "PLAUD_COOKIE",
        "PLAUD_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    env_path = tmp_path / ".env"
    result = refresh_auth(env_path=env_path, curl_text=CURRENT_WEB_CURL)

    assert result.status == "ok"
    assert result.cookie_captured is True
    cfg = load_config(env_path)
    assert cfg.base_url == "https://api-apne1.plaud.ai"
    assert cfg.headers()["authorization"] == "bearer header.payload.signature"
    assert cfg.headers()["cookie"] == "session=abc; preference=ko"
    assert "x-pld-user" not in cfg.headers()
    assert "PLAUD_X_PLD_USER" not in env_path.read_text(encoding="utf-8")


def test_refresh_auth_accepts_single_line_curl_and_region_host(tmp_path, monkeypatch) -> None:
    for key in ("PLAUD_AUTHORIZATION", "PLAUD_X_DEVICE_ID", "PLAUD_X_PLD_USER"):
        monkeypatch.delenv(key, raising=False)

    curl = (
        "curl 'https://api-eu1.plaud.ai/filetag/' "
        "-H 'authorization: Bearer single.line.token' -H 'x-device-id: one-line-device'"
    )
    env_path = tmp_path / ".env"

    assert refresh_auth(env_path=env_path, curl_text=curl).status == "ok"
    cfg = load_config(env_path)
    assert cfg.base_url == "https://api-eu1.plaud.ai"
    assert "x-pld-user" not in cfg.headers()


def test_refresh_auth_rejects_non_plaud_target_without_writing(tmp_path) -> None:
    env_path = tmp_path / ".env"
    curl = (
        "curl 'https://example.com/' "
        "-H 'authorization: Bearer should.not.persist' -H 'x-device-id: nope'"
    )

    result = refresh_auth(env_path=env_path, curl_text=curl)

    assert result.status == "invalid_curl"
    assert "api-*.plaud.ai" in result.detail
    assert not env_path.exists()


def test_refresh_auth_rejects_plaud_lookalike_host_without_writing(tmp_path) -> None:
    env_path = tmp_path / ".env"
    curl = (
        "curl 'https://api-attacker.plaud.ai.example.com/file/simple/web' "
        "-H 'authorization: Bearer should.not.persist' -H 'x-device-id: nope'"
    )

    result = refresh_auth(env_path=env_path, curl_text=curl)

    assert result.status == "invalid_curl"
    assert not env_path.exists()


def test_refresh_auth_rejects_blank_required_header_values(tmp_path) -> None:
    env_path = tmp_path / ".env"
    curl = (
        "curl 'https://api-apne1.plaud.ai/file/simple/web' "
        "-H 'authorization: ' -H 'x-device-id: device'"
    )

    result = refresh_auth(env_path=env_path, curl_text=curl)

    assert result.status == "invalid_curl"
    assert "PLAUD_AUTHORIZATION" in result.detail
    assert not env_path.exists()


def test_refresh_auth_rejects_non_bearer_authorization(tmp_path) -> None:
    env_path = tmp_path / ".env"
    curl = (
        "curl 'https://api-apne1.plaud.ai/file/simple/web' "
        "-H 'authorization: Basic abc' -H 'x-device-id: device'"
    )

    result = refresh_auth(env_path=env_path, curl_text=curl)

    assert result.status == "invalid_curl"
    assert "Bearer" in result.detail
    assert not env_path.exists()


def test_refresh_auth_rejects_unreasonably_large_clipboard(tmp_path) -> None:
    result = refresh_auth(
        env_path=tmp_path / ".env",
        curl_text="curl " + ("x" * 1_000_001),
    )

    assert result.status == "invalid_curl"
    assert "unexpectedly large" in result.detail


@pytest.mark.parametrize(
    "unsafe_option",
    [
        "-H 'authorization: Bearer token\x01suffix'",
        "-H 'x-device-id: device\x7fsuffix'",
        "-H 'timezone: Asia/Seoul\x1f'",
        "-H 'cookie: session=abc\tadmin=true'",
        "-b 'session=abc\radmin=true'",
        "--cookie='session=abc\nadmin=true'",
    ],
)
def test_refresh_auth_rejects_controls_in_every_credential_source(
    tmp_path, unsafe_option: str
) -> None:
    env_path = tmp_path / ".env"
    curl = (
        "curl 'https://api-apne1.plaud.ai/file/simple/web' "
        "-H 'authorization: Bearer safe-token' -H 'x-device-id: safe-device' " + unsafe_option
    )

    result = refresh_auth(env_path=env_path, curl_text=curl)

    assert result.status == "invalid_curl"
    assert "control character" in result.detail
    assert "safe-token" not in result.detail
    assert not env_path.exists()


def test_refresh_auth_stays_quiet_for_json_callers(tmp_path, capsys) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("PLAUD_AUTHORIZATION='old'\n", encoding="utf-8")
    curl = """
curl 'https://api-apne1.plaud.ai/filetag/' \\
  -H 'authorization: Bearer new.token.value' \\
  -H 'x-device-id: device-123' \\
  -H 'x-pld-user: user-1234567890123456'
"""

    result = refresh_auth(env_path=env_path, curl_text=curl)

    assert result.status == "ok"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "PLAUD_AUTHORIZATION='Bearer new.token.value'" in env_path.read_text(encoding="utf-8")


def test_refresh_auth_reports_invalid_curl(tmp_path) -> None:
    env_path = tmp_path / ".env"

    result = refresh_auth(
        env_path=env_path,
        curl_text="curl 'https://api-apne1.plaud.ai/filetag/' -H 'foo: bar'",
    )

    assert result.status == "invalid_curl"
    assert "missing required headers" in result.detail
    assert not env_path.exists()


def test_refresh_auth_live_rejection_keeps_previous_env_byte_identical(tmp_path) -> None:
    env_path = tmp_path / ".env"
    previous = "PLAUD_AUTHORIZATION='Bearer old.token'\nPLAUD_X_DEVICE_ID='old-device'\n"
    env_path.write_text(previous, encoding="utf-8")

    result = refresh_auth(
        env_path=env_path,
        curl_text=CURRENT_WEB_CURL,
        validate_live=True,
        live_validator=lambda values: "rejected",
    )

    assert result.status == "live_auth_failed"
    assert "header.payload.signature" not in result.detail
    assert env_path.read_text(encoding="utf-8") == previous


def test_refresh_auth_unreachable_live_check_keeps_previous_env_byte_identical(tmp_path) -> None:
    env_path = tmp_path / ".env"
    previous = "PLAUD_AUTHORIZATION='Bearer old.token'\nPLAUD_X_DEVICE_ID='old-device'\n"
    env_path.write_text(previous, encoding="utf-8")

    result = refresh_auth(
        env_path=env_path,
        curl_text=CURRENT_WEB_CURL,
        validate_live=True,
        live_validator=lambda values: "unreachable",
    )

    assert result.status == "live_check_unavailable"
    assert "Keychain unchanged" in result.detail
    assert env_path.read_text(encoding="utf-8") == previous


def test_refresh_auth_auto_arms_from_logged_in_browser_after_valid_import(tmp_path) -> None:
    env_path = tmp_path / ".env"
    calls = 0

    def arm_from_browser():
        nonlocal calls
        calls += 1
        update_credential_values(
            {
                "PLAUD_WORKSPACE_ID": "workspace-1",
                "PLAUD_WS_REFRESH_TOKEN": "rotating-refresh-token",
                "PLAUD_WS_REFRESH_EXPIRES_AT": str(FAR_FUTURE),
            },
            env_path,
        )
        return {"status": "ok", "detail": "captured browser renewal"}

    result = refresh_auth(
        env_path=env_path,
        curl_text=_workspace_curl("workspace-1"),
        validate_live=True,
        live_validator=lambda values: "ok",
        arm_auto_refresh=True,
        auto_refresh_armer=arm_from_browser,
    )

    assert result.status == "ok"
    assert result.auto_refresh_armed is True
    assert calls == 1
    assert "automatic renewal is armed" in result.detail


def test_refresh_auth_preserves_only_matching_current_workspace_renewal(tmp_path) -> None:
    env_path = tmp_path / ".env"
    _seed_workspace_renewal(env_path)

    result = refresh_auth(
        env_path=env_path,
        curl_text=_workspace_curl("ws_abc"),
        validate_live=True,
        live_validator=lambda values: "ok",
    )

    assert result.status == "ok"
    assert result.auto_refresh_armed is True
    values = load_credential_values(env_path)
    assert values["PLAUD_WS_REFRESH_TOKEN"] == "stored-refresh-token"
    assert values["PLAUD_WORKSPACE_ID"] == "ws_abc"


@pytest.mark.parametrize(
    ("candidate_curl", "refresh_expires_at"),
    [
        (CURRENT_WEB_CURL, FAR_FUTURE),
        (_workspace_curl("ws_other"), FAR_FUTURE),
        (_workspace_curl("ws_abc"), 1),
    ],
    ids=["opaque-access", "workspace-mismatch", "expired-refresh-horizon"],
)
def test_refresh_auth_drops_unbound_or_expired_workspace_renewal(
    tmp_path, candidate_curl: str, refresh_expires_at: int
) -> None:
    env_path = tmp_path / ".env"
    _seed_workspace_renewal(env_path, refresh_expires_at=refresh_expires_at)

    result = refresh_auth(
        env_path=env_path,
        curl_text=candidate_curl,
        validate_live=True,
        live_validator=lambda values: "ok",
    )

    assert result.status == "ok"
    assert result.auto_refresh_armed is False
    values = load_credential_values(env_path)
    assert "PLAUD_WS_REFRESH_TOKEN" not in values
    assert "PLAUD_WS_REFRESH_EXPIRES_AT" not in values
    assert "PLAUD_WORKSPACE_ID" not in values


def test_refresh_auth_rejects_expired_candidate_without_reporting_old_renewal_armed(
    tmp_path,
) -> None:
    env_path = tmp_path / ".env"
    _seed_workspace_renewal(env_path)

    result = refresh_auth(
        env_path=env_path,
        curl_text=_workspace_curl("ws_abc", expires_at=1),
    )

    assert result.status == "live_auth_failed"
    assert result.auto_refresh_armed is False
    # Validate-before-write keeps the prior generation intact, but never
    # advertises its renewal as belonging to the rejected candidate.
    assert load_credential_values(env_path)["PLAUD_WS_REFRESH_TOKEN"] == "stored-refresh-token"


def test_refresh_auth_browser_arm_failure_keeps_valid_access_credential(tmp_path) -> None:
    env_path = tmp_path / ".env"

    result = refresh_auth(
        env_path=env_path,
        curl_text=CURRENT_WEB_CURL,
        validate_live=True,
        live_validator=lambda values: "ok",
        arm_auto_refresh=True,
        auto_refresh_armer=lambda: {"status": "no_session", "detail": "no browser session"},
    )

    assert result.status == "ok"
    assert result.auto_refresh_armed is False
    assert result.auto_refresh_detail == "no browser session"
    assert load_config(env_path).headers()["authorization"] == "bearer header.payload.signature"


def test_current_capture_removes_legacy_user_from_same_process(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "PLAUD_AUTHORIZATION='Bearer old.token'\n"
        "PLAUD_X_DEVICE_ID='old-device'\n"
        "PLAUD_X_PLD_USER='legacy-user'\n",
        encoding="utf-8",
    )
    assert load_config(env_path).headers()["x-pld-user"] == "legacy-user"
    assert refresh_auth(env_path=env_path, curl_text=CURRENT_WEB_CURL).status == "ok"

    assert "x-pld-user" not in load_config(env_path).headers()


def test_refresh_auth_reports_empty_clipboard(tmp_path) -> None:
    result = refresh_auth(env_path=tmp_path / ".env", curl_text="   \n  ")

    assert result.status == "clipboard_empty"


def test_refresh_auth_reports_missing_pbpaste(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"

    def boom(*args, **kwargs):
        raise FileNotFoundError("pbpaste")

    monkeypatch.setattr(refresh_mod.subprocess, "run", boom)

    result = refresh_auth(env_path=env_path)  # curl_text=None → pasteboard path

    assert result.status == "pbpaste_missing"
    assert not env_path.exists()


def test_refresh_auth_honors_plaud_env_file(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / "custom.env"
    monkeypatch.setenv("PLAUD_ENV_FILE", str(env_path))

    result = refresh_auth(curl_text=VALID_CURL)

    assert result.status == "ok"
    assert "PLAUD_AUTHORIZATION='Bearer test.token.value'" in env_path.read_text(encoding="utf-8")


def test_refresh_auth_cli_json_stdin_never_prints_tokens(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    monkeypatch.setenv("PLAUD_ENV_FILE", str(env_path))
    runner = CliRunner()

    result = runner.invoke(app, ["refresh-auth", "--json", "--stdin"], input=VALID_CURL)

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert set(body) == {
        "status",
        "detail",
        "cookie_captured",
        "auto_refresh_armed",
        "auto_refresh_detail",
    }
    assert body["status"] == "ok"
    assert "test.token.value" not in result.stdout  # bearer token stays off stdout
    assert env_path.exists()


def test_refresh_auth_cli_json_forwards_explicit_auto_arm(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_refresh_auth(**kwargs):
        seen.update(kwargs)
        return RefreshResult(
            "ok",
            "stored",
            auto_refresh_armed=True,
            auto_refresh_detail="browser renewal captured",
        )

    monkeypatch.setattr(refresh_mod, "refresh_auth", fake_refresh_auth)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["refresh-auth", "--json", "--stdin", "--validate-live", "--arm-auto-refresh"],
        input=VALID_CURL,
    )

    assert result.exit_code == 0
    assert seen["arm_auto_refresh"] is True
    assert seen["validate_live"] is True
    body = json.loads(result.stdout)
    assert body["auto_refresh_armed"] is True
    assert body["auto_refresh_detail"] == "browser renewal captured"


def test_refresh_auth_cli_json_stdin_garbage_exits_0(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLAUD_ENV_FILE", str(tmp_path / ".env"))
    runner = CliRunner()

    result = runner.invoke(app, ["refresh-auth", "--json", "--stdin"], input="not a curl")

    assert result.exit_code == 0  # JSON mode always exits 0
    assert json.loads(result.stdout)["status"] == "invalid_curl"


def test_refresh_auth_cli_empty_clipboard_exits_2(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLAUD_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setattr(refresh_mod, "_read_pasteboard", lambda: "")
    runner = CliRunner()

    result = runner.invoke(app, ["refresh-auth"])

    assert result.exit_code == 2


def test_refresh_auth_cli_missing_pbpaste_exits_3(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLAUD_ENV_FILE", str(tmp_path / ".env"))

    def boom() -> str:
        raise RuntimeError("pbpaste not found")

    monkeypatch.setattr(refresh_mod, "_read_pasteboard", boom)
    runner = CliRunner()

    result = runner.invoke(app, ["refresh-auth"])

    assert result.exit_code == 3
