from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from cli.onboard import DEFAULTS

from .auth_status import _decode_jwt_payload
from .client import PlaudAPIError, PlaudClient
from .config import PlaudConfig, resolve_env_path
from .secret_store import (
    CredentialStoreError,
    credential_lock,
    load_credential_values,
    update_credential_values,
)
from .ws_refresh import (
    _disarm,
    _refresh_workspace_token_locked,
    credential_env_updates,
)


class WebAuthCapture(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    authorization: str | None = None
    x_device_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("x_device_id", "x-device-id", "xDeviceID"),
    )
    x_pld_user: str | None = Field(
        default=None,
        validation_alias=AliasChoices("x_pld_user", "x-pld-user", "xPldUser"),
    )
    cookie: str | None = None
    x_pld_tag: str | None = Field(
        default=None,
        validation_alias=AliasChoices("x_pld_tag", "x-pld-tag", "xPldTag"),
    )
    base_url: str | None = None
    app_language: str | None = Field(
        default=None,
        validation_alias=AliasChoices("app_language", "app-language", "appLanguage"),
    )
    app_platform: str | None = Field(
        default=None,
        validation_alias=AliasChoices("app_platform", "app-platform", "appPlatform"),
    )
    edit_from: str | None = Field(
        default=None,
        validation_alias=AliasChoices("edit_from", "edit-from", "editFrom"),
    )
    origin: str | None = None
    referer: str | None = None
    timezone: str | None = None
    # Raw web.plaud.ai localStorage `workspaceList` JSON. Carries the workspace
    # *refresh* token, which never appears in request headers — capturing it
    # here is what arms automatic refresh from a single embedded login.
    workspace_list: str | None = Field(
        default=None,
        validation_alias=AliasChoices("workspace_list", "workspaceList"),
    )


@dataclass(frozen=True, slots=True)
class WebAuthResult:
    status: str
    detail: str = ""
    cookie_captured: bool = False
    auto_refresh_armed: bool = False  # workspace refresh token captured + stored


CaptureInput = WebAuthCapture | Mapping[str, str | None]
# Probes the assembled candidate values; returns "ok" | "rejected" | "unreachable".
LiveValidator = Callable[[Mapping[str, str]], str]


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_capture(capture: CaptureInput) -> WebAuthCapture | WebAuthResult:
    if isinstance(capture, WebAuthCapture):
        return capture
    try:
        return WebAuthCapture.model_validate(capture)
    except ValidationError as exc:
        return WebAuthResult("invalid_payload", exc.errors()[0]["msg"])


def _write_env(
    values: Mapping[str, str], env_path: Path, *, workspace_list_json: str | None = None
) -> tuple[bool, str]:
    """Atomically save capture and immediately prove/rotate a refresh token."""

    with credential_lock(env_path):
        existing_refresh = load_credential_values(env_path, already_locked=True).get(
            "PLAUD_WS_REFRESH_TOKEN"
        )
        candidate_needs_verification = bool(workspace_list_json and not existing_refresh)
        updates = credential_env_updates(
            values,
            env_path,
            workspace_list_json=workspace_list_json,
            already_locked=True,
        )
        update_credential_values(updates, env_path, already_locked=True)

        refresh_detail = ""
        if workspace_list_json:
            outcome = _refresh_workspace_token_locked(
                env_path=env_path, now=int(time.time()), only_if_needed=False
            )
            # A same-workspace Keychain token may already have rotated beyond
            # WebKit's copy.  Try it first; only if it is conclusively rejected
            # do we arm and verify the newly captured browser candidate.
            if outcome.status == "rejected" and existing_refresh:
                candidate_needs_verification = True
                retry_updates = credential_env_updates(
                    values,
                    env_path,
                    workspace_list_json=workspace_list_json,
                    already_locked=True,
                )
                update_credential_values(retry_updates, env_path, already_locked=True)
                outcome = _refresh_workspace_token_locked(
                    env_path=env_path, now=int(time.time()), only_if_needed=False
                )
            if outcome.status not in ("ok", "fresh"):
                refresh_detail = outcome.detail or outcome.status
                if candidate_needs_verification:
                    # Do not advertise or retain an unproved browser token as
                    # ready. The access session remains saved; WebKit can retry.
                    _disarm(env_path, already_locked=True)

        token_present = bool(
            load_credential_values(env_path, already_locked=True).get("PLAUD_WS_REFRESH_TOKEN")
        )
        armed = token_present and (not candidate_needs_verification or not refresh_detail)
        return armed, refresh_detail


def _token_expired(authorization: str, *, now: int | None = None) -> bool:
    """True only when the captured JWT decodes AND its exp is in the past."""
    token = authorization
    for prefix in ("bearer ", "Bearer "):
        if token.startswith(prefix):
            token = token[len(prefix) :]
            break
    claims = _decode_jwt_payload(token.strip())
    if not claims:
        return False  # opaque token — let the live probe decide
    exp = claims.get("exp")
    now = int(time.time()) if now is None else now
    return isinstance(exp, (int, float)) and int(exp) <= now


def _default_live_validator(values: Mapping[str, str]) -> str:
    """Probe the candidate credentials in memory — Keychain is not read here."""
    cfg = PlaudConfig(
        base_url=values.get("PLAUD_BASE_URL", "https://api-apne1.plaud.ai"),
        authorization=values["PLAUD_AUTHORIZATION"],
        x_device_id=values["PLAUD_X_DEVICE_ID"],
        x_pld_user=values.get("PLAUD_X_PLD_USER", ""),
        x_pld_tag=values.get("PLAUD_X_PLD_TAG", ""),
        app_language=values.get("PLAUD_APP_LANGUAGE", "en"),
        app_platform=values.get("PLAUD_APP_PLATFORM", "web"),
        edit_from=values.get("PLAUD_EDIT_FROM", "web"),
        origin=values.get("PLAUD_ORIGIN", "https://web.plaud.ai"),
        referer=values.get("PLAUD_REFERER", "https://web.plaud.ai/"),
        timezone=values.get("PLAUD_TIMEZONE", "Asia/Seoul"),
        cookie=values.get("PLAUD_COOKIE", ""),
    )
    try:
        # 10s timeout keeps the app's 40s watchdog comfortable.
        with PlaudClient(cfg, timeout=10.0) as client:
            client.list_files(limit=1)
    except PlaudAPIError as exc:
        # HTTP 401/403 and Plaud's HTTP-200 business status -419 are genuine
        # auth rejection; 5xx and status-less network errors are inconclusive.
        return "rejected" if exc.is_auth_rejection else "unreachable"
    return "ok"


def import_web_auth(
    capture: CaptureInput,
    *,
    env_path: Path | None = None,
    live_validator: LiveValidator | None = None,
    validate_live: bool = True,
) -> WebAuthResult:
    """Validate-before-write: probe the candidate credentials in memory before
    replacing the atomic Keychain bundle — no rollback path needed."""
    env_path = resolve_env_path(env_path)
    parsed = _parse_capture(capture)
    if isinstance(parsed, WebAuthResult):
        return parsed

    required = {
        "authorization": _clean(parsed.authorization),
        "x_device_id": _clean(parsed.x_device_id),
    }
    missing = [key for key, value in required.items() if value is None]
    cookie = _clean(parsed.cookie)
    if missing:
        return WebAuthResult(
            "missing_required",
            "missing required Web Login fields: " + ", ".join(missing),
            cookie_captured=cookie is not None,
        )

    values = dict(DEFAULTS)
    if base_url := _clean(parsed.base_url):
        values["PLAUD_BASE_URL"] = base_url
    values["PLAUD_AUTHORIZATION"] = required["authorization"] or ""
    values["PLAUD_X_DEVICE_ID"] = required["x_device_id"] or ""
    if x_pld_user := _clean(parsed.x_pld_user):
        values["PLAUD_X_PLD_USER"] = x_pld_user
    if cookie:
        values["PLAUD_COOKIE"] = cookie

    optional = {
        "PLAUD_X_PLD_TAG": parsed.x_pld_tag,
        "PLAUD_APP_LANGUAGE": parsed.app_language,
        "PLAUD_APP_PLATFORM": parsed.app_platform,
        "PLAUD_EDIT_FROM": parsed.edit_from,
        "PLAUD_ORIGIN": parsed.origin,
        "PLAUD_REFERER": parsed.referer,
        "PLAUD_TIMEZONE": parsed.timezone,
    }
    for key, value in optional.items():
        if clean := _clean(value):
            values[key] = clean

    # Local expiry pre-check: an already-expired capture is a known rejection —
    # no network call, Keychain untouched.
    if _token_expired(values["PLAUD_AUTHORIZATION"]):
        return WebAuthResult(
            "live_auth_failed",
            "captured token is already expired — log in again",
            cookie_captured=cookie is not None,
        )

    status = "ok"
    detail = "credentials refreshed from Plaud Web Login"
    if validate_live:
        verdict = (live_validator or _default_live_validator)(values)
        if verdict == "rejected":
            return WebAuthResult(
                "live_auth_failed",
                "Plaud rejected the captured credentials; Keychain unchanged",
                cookie_captured=cookie is not None,
            )
        if verdict == "unreachable":
            # Non-destructive: save the capture anyway, but flag it unverified.
            status = "live_check_unavailable"
            detail = "credentials saved but could not be verified — check your network connection"

    try:
        armed, refresh_detail = _write_env(
            values, env_path, workspace_list_json=_clean(parsed.workspace_list)
        )
    except (OSError, CredentialStoreError) as exc:
        return WebAuthResult("write_failed", str(exc), cookie_captured=cookie is not None)

    if armed and status == "ok":
        detail += " — automatic renewal verified and stored in macOS Keychain"
    elif status == "ok":
        detail += " — connected, but automatic renewal is not ready"
        if refresh_detail:
            detail += f" ({refresh_detail})"
    return WebAuthResult(
        status, detail, cookie_captured=cookie is not None, auto_refresh_armed=armed
    )
