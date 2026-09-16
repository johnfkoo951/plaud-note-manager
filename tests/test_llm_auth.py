from __future__ import annotations

import json
import subprocess

import pytest

from core import app_config, llm_auth


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(app_config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setenv("PLAUD_LLM_AUTH_HOME", str(tmp_path))
    for env in llm_auth.API_KEY_ENV.values():
        monkeypatch.delenv(env, raising=False)


def _fake_run(outputs: dict[str, tuple[int, str]]):
    def run(argv, capture_output=True, text=True, timeout=15):
        key = " ".join(argv[1:])
        code, out = outputs.get(key, (1, ""))
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")

    return run


def test_claude_status_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_auth, "_which", lambda p: "/fake/claude")
    monkeypatch.setattr(
        llm_auth.subprocess,
        "run",
        _fake_run(
            {
                "auth status": (
                    0,
                    json.dumps({"loggedIn": True, "email": "a@b.c", "subscriptionType": "max"}),
                )
            }
        ),
    )

    r = llm_auth.inspect("claude")

    assert r.cli_installed and r.oauth_logged_in is True
    assert r.account == "a@b.c · max"
    assert r.backend == "cli" and r.ready
    assert r.login_command == "claude auth login"


def test_codex_status_text_and_auth_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(llm_auth, "_which", lambda p: "/fake/codex")
    monkeypatch.setattr(
        llm_auth.subprocess, "run", _fake_run({"login status": (0, "Logged in using ChatGPT")})
    )
    assert llm_auth.inspect("codex").account == "ChatGPT"

    # CLI status unavailable → fall back to ~/.codex/auth.json
    monkeypatch.setattr(llm_auth.subprocess, "run", _fake_run({}))
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "auth.json").write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": {"x": 1}}), encoding="utf-8"
    )
    r = llm_auth.inspect("codex")
    assert r.oauth_logged_in is True and r.account == "chatgpt"


def test_gemini_and_grok_use_credential_caches(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(llm_auth, "_which", lambda p: f"/fake/{p}")

    assert llm_auth.inspect("gemini").oauth_logged_in is False
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini" / "oauth_creds.json").write_text("{}", encoding="utf-8")
    assert llm_auth.inspect("gemini").oauth_logged_in is True

    assert llm_auth.inspect("grok").oauth_logged_in is False
    (tmp_path / ".grok").mkdir()
    (tmp_path / ".grok" / "auth.json").write_text(
        json.dumps({"https://auth.x.ai::abc": {"token": "t"}}), encoding="utf-8"
    )
    r = llm_auth.inspect("grok")
    assert r.oauth_logged_in is True and r.account == "SuperGrok OAuth"


def test_missing_cli_is_not_ready_and_api_backend_needs_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_auth, "_which", lambda p: "")
    r = llm_auth.inspect("grok")
    assert not r.cli_installed and r.oauth_logged_in is None and not r.ready

    app_config.set_backend("grok", "api")
    assert not llm_auth.inspect("grok").ready
    monkeypatch.setenv("XAI_API_KEY", "k")
    assert llm_auth.inspect("grok").ready


def test_launch_login_strips_api_key_and_inherits_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(llm_auth, "_which", lambda p: "/fake/codex")
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-leak")

    def call(argv, env=None):
        captured["argv"] = argv
        captured["env"] = env
        return 0

    monkeypatch.setattr(llm_auth.subprocess, "call", call)

    assert llm_auth.launch_login("codex") == 0
    assert captured["argv"] == ["/fake/codex", "login"]
    assert "OPENAI_API_KEY" not in captured["env"]
    monkeypatch.setattr(llm_auth, "_which", lambda p: "")
    assert llm_auth.launch_login("codex") == 127
