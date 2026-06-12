from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from cli.onboard import DEFAULTS

from .auth_status import _decode_jwt_payload
from .client import PlaudAPIError, PlaudClient
from .config import PlaudConfig, resolve_env_path, write_env_file


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


@dataclass(frozen=True, slots=True)
class WebAuthResult:
    status: str
    detail: str = ""
    cookie_captured: bool = False


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


def _write_env(values: Mapping[str, str], env_path: Path) -> None:
    order = (
        "PLAUD_BASE_URL",
        "PLAUD_AUTHORIZATION",
        "PLAUD_X_DEVICE_ID",
        "PLAUD_X_PLD_USER",
        "PLAUD_X_PLD_TAG",
        "PLAUD_COOKIE",
        "PLAUD_APP_LANGUAGE",
        "PLAUD_APP_PLATFORM",
        "PLAUD_EDIT_FROM",
        "PLAUD_ORIGIN",
        "PLAUD_REFERER",
        "PLAUD_TIMEZONE",
    )
    write_env_file({key: values[key] for key in order if values.get(key)}, env_path)


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
    """Probe the candidate credentials in memory — .env is never read here."""
    cfg = PlaudConfig(
        base_url=values.get("PLAUD_BASE_URL", "https://api-apne1.plaud.ai"),
        authorization=values["PLAUD_AUTHORIZATION"],
        x_device_id=values["PLAUD_X_DEVICE_ID"],
        x_pld_user=values["PLAUD_X_PLD_USER"],
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
        # Only 401/403 is a genuine rejection; spurious 500s from the Plaud
        # backend and status-less network errors mean "could not verify".
        return "rejected" if exc.status_code in (401, 403) else "unreachable"
    return "ok"


def import_web_auth(
    capture: CaptureInput,
    *,
    env_path: Path | None = None,
    live_validator: LiveValidator | None = None,
    validate_live: bool = True,
) -> WebAuthResult:
    """Validate-before-write: probe the candidate credentials in memory and only
    touch .env once they look usable — no rollback path needed."""
    env_path = resolve_env_path(env_path)
    parsed = _parse_capture(capture)
    if isinstance(parsed, WebAuthResult):
        return parsed

    required = {
        "authorization": _clean(parsed.authorization),
        "x_device_id": _clean(parsed.x_device_id),
        "x_pld_user": _clean(parsed.x_pld_user),
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
    values["PLAUD_X_PLD_USER"] = required["x_pld_user"] or ""
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
    # no network call, .env untouched.
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
                "Plaud rejected the captured credentials; .env unchanged",
                cookie_captured=cookie is not None,
            )
        if verdict == "unreachable":
            # Non-destructive: save the capture anyway, but flag it unverified.
            status = "live_check_unavailable"
            detail = "credentials saved but could not be verified — check your network connection"

    try:
        _write_env(values, env_path)
    except OSError as exc:
        return WebAuthResult("write_failed", str(exc), cookie_captured=cookie is not None)

    return WebAuthResult(status, detail, cookie_captured=cookie is not None)
