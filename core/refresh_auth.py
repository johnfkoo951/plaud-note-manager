"""Refresh Plaud credentials from a copied Plaud cURL.

The source of truth remains the user's browser-copied cURL:

    1. Open web.plaud.ai.
    2. Copy an authenticated API request as cURL.
    3. Run `uv run plaud refresh-auth` or click the app's refresh button.

This helper reads the macOS pasteboard, parses the cURL with the same parser as
`plaud onboard`, and writes `.env`. Tokens/cookies are never printed.
"""

from __future__ import annotations

import subprocess
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from cli.onboard import parse_curl, write_env

from .config import resolve_env_path


@dataclass
class RefreshResult:
    status: str  # ok | clipboard_empty | invalid_curl | pbpaste_missing | write_failed
    detail: str = ""
    cookie_captured: bool = False


def _read_pasteboard() -> str:
    try:
        proc = subprocess.run(
            ["pbpaste"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(str(exc)) from exc
    return proc.stdout


def refresh_auth(*, env_path: Path | None = None, curl_text: str | None = None) -> RefreshResult:
    """Parse a copied Plaud cURL and write fresh credentials to `.env`."""
    env_path = resolve_env_path(env_path)  # honor PLAUD_ENV_FILE like every reader
    if curl_text is None:
        try:
            curl_text = _read_pasteboard()
        except RuntimeError as exc:
            return RefreshResult("pbpaste_missing", f"could not read macOS pasteboard: {exc}")

    if not curl_text.strip():
        return RefreshResult(
            "clipboard_empty",
            "Copy a Plaud API request as cURL from web.plaud.ai, then retry.",
        )

    try:
        values = parse_curl(curl_text)
    except SystemExit as exc:
        return RefreshResult("invalid_curl", str(exc))

    # `write_env()` is intentionally chatty for terminal onboarding, but this
    # helper is consumed by the app as JSON. Keep stdout clean so the Swift UI
    # can decode `plaud refresh-auth --json` reliably.
    try:
        with redirect_stdout(StringIO()):
            write_env(values, env_path)
    except OSError as exc:
        return RefreshResult("write_failed", f"could not write {env_path.name}: {exc}")
    return RefreshResult(
        "ok",
        "credentials refreshed from copied cURL",
        cookie_captured="PLAUD_COOKIE" in values,
    )
