"""Tier-1 auth recovery: re-harvest a live browser session, no password entry.

When the headless refresh chain breaks (workspace refresh token expired or
revoked — `ws-refresh` reports `not_bootstrapped`/`rejected`), the fix is a
fresh `workspaceList` export from web.plaud.ai. That value lives in the
browser's localStorage of an already-logged-in session, so recovery is just
"open the site in a controlled browser and read localStorage" — never typing
credentials.

Drivers:

- **cmux** (CLI-standalone): drives the cmux native browser via the `cmux
  browser` commands (open → wait → eval). Works only if the cmux browser
  profile has a live web.plaud.ai login.
- **aside / any MCP browser** (agent-level): a Claude session with browser
  tools runs `CAPTURE_JS` itself and pipes the result to
  `plaud ws-bootstrap --stdin`. Not callable from this module — documented in
  agent/AGENT.md.

If no driver can produce a value, the caller falls through to Tier 2 (the
app's Auth sheet / Embedded Web Login).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

PLAUD_WEB_URL = "https://web.plaud.ai"

# Returns the raw workspaceList JSON, or "" when the session isn't logged in.
# Shared verbatim by every driver (cmux eval, aside repl, devtools console).
CAPTURE_JS = (
    "(() => { const k = Object.keys(localStorage).find(k => "
    'k.startsWith("pld_") && k.endsWith(":workspaceList")); '
    'return k ? localStorage.getItem(k) : ""; })()'
)


class RecoverError(RuntimeError):
    """Driver-level failure (browser unreachable, no session, bad output)."""

    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class RecoverOutcome:
    status: str  # ok | no_session | driver_unavailable | capture_failed | bootstrap_<status>
    detail: str = ""
    driver: str = ""
    bootstrap: dict[str, Any] = field(default_factory=dict)


Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _default_runner(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=60)


def capture_via_cmux(
    *,
    runner: Runner = _default_runner,
    poll_seconds: int = 15,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Open web.plaud.ai in the cmux browser and read workspaceList."""
    if shutil.which("cmux") is None:
        raise RecoverError("driver_unavailable", "cmux CLI not installed")

    try:
        opened = runner(["cmux", "browser", "open", PLAUD_WEB_URL, "--focus", "false"])
    except Exception as exc:
        raise RecoverError("driver_unavailable", f"cmux browser open failed: {exc}") from exc
    if opened.returncode != 0:
        raise RecoverError(
            "driver_unavailable",
            f"cmux browser open failed: {(opened.stderr or opened.stdout).strip()[:200]}",
        )
    surface = _parse_surface(opened.stdout)
    target = [surface] if surface else []

    # Page load, then the SPA hydrating localStorage, are both async — poll
    # eval instead of trusting a single wait.
    runner(
        ["cmux", "browser", *target, "wait", "--load-state", "complete",
         "--timeout-ms", "20000"]
    )
    deadline = time.monotonic() + poll_seconds
    value = ""
    while True:
        result = runner(["cmux", "browser", *target, "eval", "--script", CAPTURE_JS])
        if result.returncode == 0:
            value = _clean_eval_output(result.stdout)
            if value:
                return value
        if time.monotonic() >= deadline:
            break
        sleep(1.0)
    raise RecoverError(
        "no_session",
        "web.plaud.ai has no logged-in session in the cmux browser — "
        "log in there once (cmux browser stays open), then re-run auth-recover",
    )


def _parse_surface(stdout: str) -> str:
    """Extract a `surface:N` handle from `cmux browser open` output, if any."""
    for token in stdout.replace(",", " ").split():
        if token.startswith("surface:"):
            return token.strip("()[]")
    return ""


def _clean_eval_output(stdout: str) -> str:
    text = stdout.strip()
    if text in ('""', "''", "null", "undefined"):
        return ""
    # Some eval implementations quote string results.
    if len(text) >= 2 and text[0] == text[-1] == '"' and not text.startswith('"['):
        import json

        try:
            return str(json.loads(text))
        except Exception:
            return text
    return text


def recover(
    *,
    driver: str = "auto",
    runner: Runner = _default_runner,
    bootstrap: Callable[[str], Any] | None = None,
) -> RecoverOutcome:
    """Capture workspaceList from a live browser session and re-arm refresh."""
    if bootstrap is None:
        from .ws_refresh import bootstrap_workspace

        bootstrap = bootstrap_workspace

    if driver not in ("auto", "cmux"):
        return RecoverOutcome(
            status="driver_unavailable",
            detail=f"unknown driver '{driver}' (CLI supports: cmux; aside runs agent-side)",
            driver=driver,
        )

    try:
        text = capture_via_cmux(runner=runner)
    except RecoverError as exc:
        return RecoverOutcome(status=exc.status, detail=exc.detail, driver="cmux")

    from .ws_refresh import parse_workspace_list

    try:
        entries = parse_workspace_list(text)
    except Exception as exc:
        return RecoverOutcome(
            status="capture_failed",
            detail=f"captured value is not a workspaceList export: {exc}",
            driver="cmux",
        )
    if not entries:
        return RecoverOutcome(
            status="capture_failed",
            detail="captured workspaceList is empty",
            driver="cmux",
        )

    outcome = bootstrap(text)
    payload = _outcome_dict(outcome)
    status = str(payload.get("status") or "unknown")
    return RecoverOutcome(
        status="ok" if status == "ok" else f"bootstrap_{status}",
        detail=str(payload.get("detail") or ""),
        driver="cmux",
        bootstrap=payload,
    )


def _outcome_dict(outcome: Any) -> dict[str, Any]:
    if isinstance(outcome, dict):
        return outcome
    from dataclasses import asdict, is_dataclass

    if is_dataclass(outcome):
        return asdict(outcome)
    return {"status": str(outcome)}
