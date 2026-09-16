"""OAuth / subscription login state for the four LLM providers.

The app never handles provider passwords or tokens. Each vendor CLI owns its
own OAuth flow and credential cache; this module only *inspects* that state
and tells the user which interactive login command to run:

| provider | CLI      | login command      | credential cache                 |
|----------|----------|--------------------|----------------------------------|
| claude   | claude   | claude auth login  | `claude auth status` (JSON)      |
| codex    | codex    | codex login        | ~/.codex/auth.json               |
| gemini   | gemini   | gemini (pick "Login with Google") | ~/.gemini/oauth_creds.json |
| grok     | grok     | grok login         | ~/.grok/auth.json                |

When a provider's backend is `cli`, `summarize._run_cli` strips that
provider's API-key env var so the subscription session is used instead of
metered API billing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from . import app_config
from .model_registry import PROVIDER_LABELS

PROVIDERS = ("claude", "codex", "gemini", "grok")

LOGIN_COMMANDS: dict[str, list[str]] = {
    "claude": ["claude", "auth", "login"],
    "codex": ["codex", "login"],
    "gemini": ["gemini"],
    "grok": ["grok", "login"],
}

LOGIN_NOTES: dict[str, str] = {
    "claude": "Opens the Anthropic OAuth page; Claude Max/Pro subscription is used.",
    "codex": "ChatGPT sign-in (Plus/Pro/Team) — no OPENAI_API_KEY needed.",
    "gemini": "Run `gemini` once, choose “Login with Google”, then /quit.",
    "grok": "SuperGrok sign-in via auth.x.ai in the browser.",
}

API_KEY_ENV: dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "codex": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "grok": "XAI_API_KEY",
}

CLI_BINARY: dict[str, str] = {p: p for p in PROVIDERS}


@dataclass
class ProviderAuth:
    provider: str
    label: str
    backend: str
    cli_installed: bool
    cli_path: str
    oauth_logged_in: bool | None  # None = could not determine
    account: str
    api_key_env: str
    api_key_set: bool
    login_command: str
    note: str

    @property
    def ready(self) -> bool:
        if self.backend == "api":
            return self.api_key_set
        return self.cli_installed and self.oauth_logged_in is not False

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ready"] = self.ready
        return data


def _home() -> Path:
    return Path(os.environ.get("PLAUD_LLM_AUTH_HOME") or Path.home())


def _which(provider: str) -> str:
    from .summarize import _resolve_binary

    return _resolve_binary(provider, CLI_BINARY[provider]) or ""


def _run(argv: list[str], timeout: int = 15) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _claude_state(binary: str) -> tuple[bool | None, str]:
    proc = _run([binary, "auth", "status"])
    if proc is None or proc.returncode != 0:
        return None, ""
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return ("logged in" in (proc.stdout or "").lower()) or None, ""
    logged = bool(data.get("loggedIn"))
    parts = [str(data.get("email") or ""), str(data.get("subscriptionType") or "")]
    return logged, " · ".join(p for p in parts if p)


def _codex_state(binary: str) -> tuple[bool | None, str]:
    auth_file = _home() / ".codex" / "auth.json"
    proc = _run([binary, "login", "status"])
    if proc is not None and proc.returncode == 0:
        text = (proc.stdout or proc.stderr or "").strip()
        if "logged in" in text.lower():
            method = "ChatGPT" if "chatgpt" in text.lower() else "API key"
            return True, method
        if "not logged" in text.lower():
            return False, ""
    if auth_file.exists():
        try:
            data = json.loads(auth_file.read_text(encoding="utf-8"))
            mode = str(data.get("auth_mode") or "")
            return bool(data.get("tokens") or data.get("OPENAI_API_KEY")), mode
        except (OSError, json.JSONDecodeError):
            return None, ""
    return False, ""


def _gemini_state(_binary: str) -> tuple[bool | None, str]:
    creds = _home() / ".gemini" / "oauth_creds.json"
    if creds.exists():
        return True, "Google OAuth"
    settings = _home() / ".gemini" / "settings.json"
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            auth_type = str(
                (data.get("security") or {}).get("auth", {}).get("selectedType")
                or data.get("selectedAuthType")
                or ""
            )
            if auth_type and "key" in auth_type.lower():
                return False, f"configured for {auth_type}"
        except (OSError, json.JSONDecodeError):
            pass
    return False, ""


def _grok_state(_binary: str) -> tuple[bool | None, str]:
    auth_file = _home() / ".grok" / "auth.json"
    if not auth_file.exists():
        return False, ""
    try:
        data = json.loads(auth_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, ""
    if isinstance(data, dict) and any("auth.x.ai" in str(k) for k in data):
        return True, "SuperGrok OAuth"
    return bool(data), ""


_STATE = {
    "claude": _claude_state,
    "codex": _codex_state,
    "gemini": _gemini_state,
    "grok": _grok_state,
}


def inspect(provider: str) -> ProviderAuth:
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider: {provider}")
    binary = _which(provider)
    installed = bool(binary)
    logged: bool | None = False
    account = ""
    if installed:
        logged, account = _STATE[provider](binary)
    env = API_KEY_ENV[provider]
    return ProviderAuth(
        provider=provider,
        label=PROVIDER_LABELS.get(provider, provider),
        backend=app_config.backend_for(provider),
        cli_installed=installed,
        cli_path=binary,
        oauth_logged_in=logged if installed else None,
        account=account,
        api_key_env=env,
        api_key_set=bool(os.environ.get(env)),
        login_command=" ".join(LOGIN_COMMANDS[provider]),
        note=LOGIN_NOTES[provider],
    )


def inspect_all() -> list[ProviderAuth]:
    return [inspect(p) for p in PROVIDERS]


def launch_login(provider: str) -> int:
    """Run the vendor's interactive login in the *current* terminal.

    Inherits stdin/stdout so the browser hand-off and device-code prompts
    work. Returns the CLI's exit code; 127 when the CLI is not installed.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider: {provider}")
    binary = _which(provider)
    if not binary:
        return 127
    argv = [binary, *LOGIN_COMMANDS[provider][1:]]
    env = {k: v for k, v in os.environ.items() if k != API_KEY_ENV[provider]}
    return subprocess.call(argv, env=env)


def is_installed(provider: str) -> bool:
    return bool(shutil.which(CLI_BINARY.get(provider, provider)))
