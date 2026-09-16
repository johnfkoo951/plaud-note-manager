"""Refresh Plaud credentials from a copied Plaud cURL.

The source of truth remains the user's browser-copied cURL:

    1. Open web.plaud.ai.
    2. Copy an authenticated API request as cURL.
    3. Run `uv run plaud refresh-auth` or click the app's refresh button.

This helper reads the macOS pasteboard, parses the cURL with the same parser as
`plaud onboard`, and writes the auth bundle to macOS Keychain. Tokens/cookies
are never printed or passed in a process argument.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Mapping
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from cli.onboard import parse_curl, write_env

from .config import resolve_env_path
from .secret_store import CredentialStoreError, load_credential_values


@dataclass
class RefreshResult:
    # ok | live_check_unavailable | live_auth_failed | clipboard_empty |
    # invalid_curl | pbpaste_missing | write_failed
    status: str
    detail: str = ""
    cookie_captured: bool = False
    # True only after the Keychain bundle is read back with a workspace refresh
    # token.  A copied API cURL always carries the short-lived access token, but
    # the rotating refresh token lives in browser localStorage; callers must not
    # describe the import as "one time" unless this bit is true.
    auto_refresh_armed: bool = False
    auto_refresh_detail: str = ""


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


LiveValidator = Callable[[Mapping[str, str]], str]
AutoRefreshArmer = Callable[[], object]


def _outcome_value(outcome: object, key: str) -> object | None:
    if isinstance(outcome, Mapping):
        return outcome.get(key)
    return getattr(outcome, key, None)


def _auto_refresh_is_armed(env_path: Path, *, now: int | None = None) -> bool:
    from .ws_refresh import workspace_refresh_is_armed

    return workspace_refresh_is_armed(load_credential_values(env_path), now=now)


def refresh_auth(
    *,
    env_path: Path | None = None,
    curl_text: str | None = None,
    validate_live: bool = False,
    live_validator: LiveValidator | None = None,
    arm_auto_refresh: bool = False,
    auto_refresh_armer: AutoRefreshArmer | None = None,
) -> RefreshResult:
    """Parse a copied Plaud cURL and write fresh credentials to Keychain.

    App callers request a live validation so a rejected candidate never
    replaces the last usable file. CLI/tests can keep the local-only default.
    """
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

    # Reject a locally-decodable expired JWT without touching disk. Opaque
    # tokens remain eligible for the live probe.
    from .web_auth import _default_live_validator, _token_expired

    now = int(time.time())
    if _token_expired(values["PLAUD_AUTHORIZATION"], now=now):
        return RefreshResult("live_auth_failed", "captured token is already expired")

    status = "ok"
    detail = "credentials refreshed from copied cURL"
    if validate_live:
        verdict = (live_validator or _default_live_validator)(values)
        if verdict == "rejected":
            return RefreshResult(
                "live_auth_failed",
                "Plaud rejected the copied credentials; Keychain unchanged",
                cookie_captured="PLAUD_COOKIE" in values,
            )
        if verdict == "unreachable":
            # The app presents this path specifically as validate-before-write.
            # An outage is not evidence that the pasted value is good, so keep
            # the last Keychain generation intact and let the user retry.
            return RefreshResult(
                "live_check_unavailable",
                "could not verify copied credentials; Keychain unchanged — check your network",
                cookie_captured="PLAUD_COOKIE" in values,
            )

    # `write_env()` is intentionally chatty for terminal onboarding, but this
    # helper is consumed by the app as JSON. Keep stdout clean so the Swift UI
    # can decode `plaud refresh-auth --json` reliably.
    try:
        with redirect_stdout(StringIO()):
            write_env(values, env_path, now=now)
    except (OSError, CredentialStoreError) as exc:
        return RefreshResult("write_failed", f"could not update credentials: {exc}")

    try:
        auto_refresh_armed = _auto_refresh_is_armed(env_path, now=now)
    except (OSError, CredentialStoreError) as exc:
        return RefreshResult(
            "write_failed",
            f"credentials were written but Keychain renewal state could not be read: {exc}",
            cookie_captured="PLAUD_COOKIE" in values,
        )

    auto_refresh_detail = ""
    if arm_auto_refresh and not auto_refresh_armed:
        # The user just copied this cURL from a logged-in Chrome session.  Use
        # that same session's localStorage to capture the rotating workspace
        # token now, turning the fallback into a genuinely one-time setup when
        # Chrome privacy permissions allow it.  Failure is non-destructive: the
        # already-validated access credential remains usable in Keychain.
        if auto_refresh_armer is None:
            from .auth_recover import recover

            auto_refresh_armer = recover
        try:
            outcome = auto_refresh_armer()
            outcome_status = str(_outcome_value(outcome, "status") or "unknown")
            outcome_detail = str(_outcome_value(outcome, "detail") or "").strip()
            auto_refresh_armed = _auto_refresh_is_armed(env_path, now=now)
            if not auto_refresh_armed:
                auto_refresh_detail = outcome_detail or (
                    f"browser renewal capture did not complete ({outcome_status})"
                )
        except Exception as exc:
            # Keep error text free of values from browser storage or argv.
            auto_refresh_detail = f"browser renewal capture failed ({type(exc).__name__})"

    if auto_refresh_armed:
        detail += " — automatic renewal is armed"
    elif arm_auto_refresh:
        detail += " — current access is saved, but automatic renewal is not armed"
    return RefreshResult(
        status,
        detail,
        cookie_captured="PLAUD_COOKIE" in values,
        auto_refresh_armed=auto_refresh_armed,
        auto_refresh_detail=auto_refresh_detail,
    )
