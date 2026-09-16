"""Tier-1 auth recovery: re-harvest a live browser session, no password entry.

When the headless refresh chain breaks (workspace refresh token expired or
revoked — `ws-refresh` reports `not_bootstrapped`/`rejected`), the fix is a
fresh `workspaceList` export from web.plaud.ai. That value lives in the
browser's localStorage of an already-logged-in session, so recovery is just
"open the site in a controlled browser and read localStorage" — never typing
credentials.

Drivers (auto order: chrome-disk → chrome → cmux):

- **chrome-disk** (CLI-standalone, zero-touch): parse Chrome's on-disk
  localStorage LevelDB directly (read-only, via ccl_chromium_reader). Needs
  no browser setting, no running Chrome, no login prompt — the highest-
  success path. The stored refresh token may lag the browser's in-memory
  rotation, but bootstrap validates with one live refresh so a stale value
  fails cleanly instead of corrupting state.
- **chrome** (CLI-standalone): AppleScript `execute javascript` against the
  user's everyday Chrome — the profile where the web.plaud.ai login actually
  lives. Requires the one-time Chrome toggle View > Developer > "Allow
  JavaScript from Apple Events".
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

import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLAUD_WEB_URL = "https://web.plaud.ai"

# Returns all namespaced workspace entries. `bootstrap_workspace` then selects
# the current Keychain JWT's exact wid, so another browser account cannot win
# merely because its localStorage key happens to be enumerated first.
CAPTURE_JS = (
    "(() => { const out = []; for (const k of Object.keys(localStorage)) { "
    'if (!k.startsWith("pld_") || !k.endsWith(":workspaceList")) continue; '
    "let v = localStorage.getItem(k); for (let i = 0; i < 2 && "
    "typeof v === 'string'; i += 1) { try { v = JSON.parse(v); } "
    "catch (_) { v = null; break; } } if (Array.isArray(v)) out.push(...v); "
    "else if (v && typeof v === 'object') out.push(v); } "
    "return JSON.stringify(out); })()"
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
        ["cmux", "browser", *target, "wait", "--load-state", "complete", "--timeout-ms", "20000"]
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


CHROME_PROFILES_DIR = Path.home() / "Library/Application Support/Google/Chrome"


def capture_via_chrome_disk(
    *,
    runner: Runner = _default_runner,  # unused; kept for driver-interface parity
    profiles_dir: Path | None = None,
) -> str:
    """Read workspaceList straight out of Chrome's localStorage LevelDB.

    Read each key's latest LevelDB revision, then compare current profiles.
    Historical rotations often share an expiry; the horizon is not a version.
    """
    try:
        from ccl_chromium_reader import ccl_chromium_localstorage as _ls
    except ImportError as exc:
        raise RecoverError(
            "driver_unavailable", f"ccl_chromium_reader not installed: {exc}"
        ) from exc

    base = profiles_dir or CHROME_PROFILES_DIR
    try:
        if not base.is_dir():
            raise RecoverError("driver_unavailable", f"Chrome profile dir not found: {base}")
        profiles = list(base.iterdir())
    except RecoverError:
        raise
    except PermissionError as exc:
        # macOS TCC commonly denies an app/CLI access to Chrome's profile even
        # though the directory exists.  This driver is only the first rung of
        # the recovery ladder; turn the denial into a normal unavailable
        # result so live Chrome / cmux can still recover the session.
        raise RecoverError(
            "driver_unavailable",
            "Chrome localStorage is protected by macOS privacy permissions",
        ) from exc
    except OSError as exc:
        raise RecoverError(
            "driver_unavailable",
            f"Chrome profile directory could not be read ({type(exc).__name__})",
        ) from exc

    import json

    best_by_workspace: dict[str, tuple[float, dict[str, Any]]] = {}
    for prof in profiles:
        leveldb = prof / "Local Storage" / "leveldb"
        if not leveldb.is_dir():
            continue
        try:
            store = _ls.LocalStoreDb(leveldb)
        except Exception:
            continue
        try:
            for host in store.iter_storage_keys():
                if host.rstrip("/") != PLAUD_WEB_URL:
                    continue
                latest_by_key: dict[str, Any] = {}
                for rec in store.iter_records_for_storage_key(host, include_deletions=True):
                    if not rec.script_key.endswith(":workspaceList"):
                        continue
                    previous = latest_by_key.get(rec.script_key)
                    if previous is None or rec.leveldb_seq_number > previous.leveldb_seq_number:
                        latest_by_key[rec.script_key] = rec
                for rec in latest_by_key.values():
                    if not rec.is_live or not rec.value:
                        continue
                    try:
                        entries: Any = rec.value
                        for _ in range(2):
                            if not isinstance(entries, str):
                                break
                            entries = json.loads(entries)
                        if isinstance(entries, dict):
                            entries = [entries]
                        if not isinstance(entries, list):
                            continue
                    except Exception:
                        continue
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        wid = entry.get("workspaceId") or entry.get("workspace_id")
                        token = entry.get("refreshToken") or entry.get("refresh_token")
                        if not wid or not token:
                            continue
                        try:
                            expiry = float(
                                entry.get("refreshExpiresAt")
                                or entry.get("refresh_expires_at")
                                or 0
                            )
                        except (TypeError, ValueError):
                            expiry = 0
                        key = str(wid)
                        if key not in best_by_workspace or expiry > best_by_workspace[key][0]:
                            best_by_workspace[key] = (expiry, entry)
        finally:
            try:
                store.close()
            except Exception:
                pass

    if not best_by_workspace:
        raise RecoverError(
            "no_session",
            "Chrome localStorage에 web.plaud.ai workspaceList가 없습니다 — "
            "Chrome에서 web.plaud.ai에 한 번 로그인해 주세요",
        )
    return json.dumps([item for _, item in best_by_workspace.values()], separators=(",", ":"))


_CHROME_HARVEST_SCRIPT = """
tell application "Google Chrome"
  if not running then error "chrome_not_running"
  set found to ""
  repeat with w in windows
    repeat with t in tabs of w
      if URL of t contains "web.plaud.ai" then
        set found to execute t javascript "{js}"
        exit repeat
      end if
    end repeat
    if found is not "" then exit repeat
  end repeat
  if found is "" then
    if (count of windows) is 0 then
      set targetWindow to make new window
    else
      set targetWindow to front window
    end if
    set newTab to make new tab at end of tabs of targetWindow with properties {{URL:"https://web.plaud.ai"}}
    delay 6
    set found to execute newTab javascript "{js}"
  end if
  return found
end tell
"""


def capture_via_chrome(
    *,
    runner: Runner = _default_runner,
) -> str:
    """Read workspaceList from the user's everyday Chrome session.

    Uses AppleScript `execute javascript`, which Chrome gates behind
    View > Developer > "Allow JavaScript from Apple Events" — a one-time,
    user-controlled toggle. This is usually the highest-success driver
    because the main Chrome profile is where the user actually stays
    logged in to web.plaud.ai.
    """
    js = CAPTURE_JS.replace("\\", "\\\\").replace('"', '\\"')
    script = _CHROME_HARVEST_SCRIPT.replace("{js}", js).replace("{{", "{").replace("}}", "}")
    try:
        result = runner(["osascript", "-e", script])
    except Exception as exc:
        raise RecoverError("driver_unavailable", f"osascript failed: {exc}") from exc
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        if "Executing JavaScript through AppleScript is turned off" in err:
            raise RecoverError(
                "chrome_js_disabled",
                "Chrome 메뉴 View > Developer > 'Allow JavaScript from Apple Events'를 "
                "한 번 켜면 이후 auth-recover가 Chrome 세션에서 무인 복구됩니다",
            )
        if "chrome_not_running" in err:
            raise RecoverError("driver_unavailable", "Google Chrome is not running")
        # AppleScript stderr can include source fragments. Keep only its
        # numeric code so a browser/storage value can never reach JSON or UI.
        code_match = re.search(r"\((-?\d+)\)\s*$", err)
        code = f" (error {code_match.group(1)})" if code_match else ""
        raise RecoverError("driver_unavailable", f"Chrome AppleScript failed{code}")
    value = _clean_eval_output(result.stdout)
    if not value or value == "missing value":
        raise RecoverError(
            "no_session",
            "Chrome의 web.plaud.ai 탭에 로그인 세션이 없습니다 — 로그인 후 다시 실행하세요",
        )
    return value


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


def _default_precheck() -> Any:
    """Is the existing headless chain still able to mint a token?"""
    from .ws_refresh import refresh_workspace_token

    return refresh_workspace_token(only_if_needed=True)


def _prioritized_failure(failures: list[RecoverOutcome]) -> RecoverOutcome:
    """Return the most actionable failure without leaking captured values.

    ``auto`` intentionally tries cmux last, but "cmux is not installed" must
    not erase an earlier Chrome privacy denial or login instruction.  Status
    class is ranked first and ties keep driver order (the first diagnosis).
    Other attempts are summarized by driver/status only; their raw details may
    originate at a browser/storage boundary and are deliberately not joined.
    """
    assert failures

    def priority(outcome: RecoverOutcome) -> int:
        if outcome.status.startswith("bootstrap_"):
            return 500
        return {
            "chrome_js_disabled": 450,
            "no_session": 400,
            "capture_failed": 350,
            "driver_unavailable": 100,
        }.get(outcome.status, 300)

    _, preferred = max(
        enumerate(failures),
        key=lambda pair: (priority(pair[1]), -pair[0]),
    )
    attempted = ", ".join(f"{item.driver}={item.status}" for item in failures)
    detail = preferred.detail
    if len(failures) > 1:
        detail = f"{detail} (attempted: {attempted})"
    return RecoverOutcome(
        status=preferred.status,
        detail=detail,
        driver=preferred.driver,
        bootstrap=preferred.bootstrap,
    )


def recover(
    *,
    driver: str = "auto",
    runner: Runner = _default_runner,
    bootstrap: Callable[[str], Any] | None = None,
    precheck: Callable[[], Any] | None = None,
    force: bool = False,
) -> RecoverOutcome:
    """Capture workspaceList from a live browser session and re-arm refresh.

    When the existing headless chain still works, that is the answer — no
    browser is touched. This matters beyond speed: each bootstrap consumes a
    rotation, which would strand the browser's stored copy one step behind.
    """
    if bootstrap is None:
        from .ws_refresh import bootstrap_workspace

        bootstrap = bootstrap_workspace

    if not force:
        try:
            payload = _outcome_dict((precheck or _default_precheck)())
        except Exception:
            payload = {}
        if str(payload.get("status") or "") in ("ok", "fresh"):
            return RecoverOutcome(
                status="ok",
                detail=str(payload.get("detail") or "existing headless refresh still works"),
                driver="ws-refresh",
                bootstrap=payload,
            )
        if str(payload.get("status") or "") in ("unreachable", "write_failed", "invalid_payload"):
            # An outage or local persistence failure is not evidence that the
            # stored chain died. Do not replace it from a possibly stale browser.
            return RecoverOutcome(
                status=str(payload["status"]),
                detail=str(payload.get("detail") or "automatic renewal is temporarily unavailable"),
                driver="ws-refresh",
                bootstrap=payload,
            )

    if driver not in ("auto", "cmux", "chrome", "chrome-disk"):
        return RecoverOutcome(
            status="driver_unavailable",
            detail=(
                f"unknown driver '{driver}' "
                "(CLI supports: chrome-disk, chrome, cmux; aside runs agent-side)"
            ),
            driver=driver,
        )

    # auto: zero-touch disk read first, live-Chrome AppleScript next, cmux last.
    drivers: list[tuple[str, Any]] = []
    if driver in ("auto", "chrome-disk"):
        drivers.append(("chrome-disk", capture_via_chrome_disk))
    if driver in ("auto", "chrome"):
        drivers.append(("chrome", capture_via_chrome))
    if driver in ("auto", "cmux"):
        drivers.append(("cmux", capture_via_cmux))

    from .ws_refresh import parse_workspace_list

    # Each rung is capture -> validate -> bootstrap. A rung that captures but
    # fails to bootstrap (e.g. Chrome's on-disk token was already rotated away
    # by the browser) must NOT end the ladder: the next, live-session driver
    # is precisely the one that can still succeed.
    failures: list[RecoverOutcome] = []
    for name, capture in drivers:
        try:
            text = capture(runner=runner)
        except RecoverError as exc:
            failures.append(RecoverOutcome(status=exc.status, detail=exc.detail, driver=name))
            continue
        except Exception as exc:
            # One optional browser driver must never abort the whole ladder.
            # Keep the detail deliberately value-free: a driver exception can
            # originate while parsing storage that contains credentials.
            failures.append(
                RecoverOutcome(
                    status="driver_unavailable",
                    detail=f"{name} capture failed ({type(exc).__name__})",
                    driver=name,
                )
            )
            continue

        try:
            entries = parse_workspace_list(text)
        except Exception as exc:
            failures.append(
                RecoverOutcome(
                    status="capture_failed",
                    detail=f"captured value is not a workspaceList export: {exc}",
                    driver=name,
                )
            )
            continue
        if not entries:
            failures.append(
                RecoverOutcome(
                    status="capture_failed",
                    detail="captured workspaceList is empty",
                    driver=name,
                )
            )
            continue

        payload = _outcome_dict(bootstrap(text))
        status = str(payload.get("status") or "unknown")
        if status == "ok":
            return RecoverOutcome(
                status="ok",
                detail=str(payload.get("detail") or ""),
                driver=name,
                bootstrap=payload,
            )
        if status in ("unreachable", "write_failed", "invalid_payload"):
            return RecoverOutcome(
                status=f"bootstrap_{status}",
                detail=str(payload.get("detail") or ""),
                driver=name,
                bootstrap=payload,
            )
        failures.append(
            RecoverOutcome(
                status=f"bootstrap_{status}",
                detail=str(payload.get("detail") or ""),
                driver=name,
                bootstrap=payload,
            )
        )

    return _prioritized_failure(failures)


def _outcome_dict(outcome: Any) -> dict[str, Any]:
    if isinstance(outcome, dict):
        return outcome
    from dataclasses import asdict, is_dataclass

    if is_dataclass(outcome):
        return asdict(outcome)
    return {"status": str(outcome)}
