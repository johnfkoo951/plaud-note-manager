from core.config import load_config
from core.refresh_auth import refresh_auth


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
    curl = """
curl 'https://api-apne1.plaud.ai/filetag/' \\
  -H 'authorization: Bearer test.token.value' \\
  -H 'x-device-id: device-123' \\
  -H 'x-pld-user: user-1234567890123456' \\
  -H 'x-pld-tag: legacy-tag' \\
  -H 'cookie: sessionid=abc; workspace=cmds'
"""

    result = refresh_auth(env_path=env_path, curl_text=curl)

    assert result.status == "ok"
    assert result.cookie_captured is True
    written = env_path.read_text(encoding="utf-8")
    assert "PLAUD_COOKIE='sessionid=abc; workspace=cmds'" in written

    cfg = load_config(env_path)
    assert cfg.headers()["cookie"] == "sessionid=abc; workspace=cmds"
    assert cfg.headers()["x-pld-tag"] == "legacy-tag"


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
    assert "PLAUD_AUTHORIZATION='Bearer new.token.value'" in env_path.read_text(
        encoding="utf-8"
    )
