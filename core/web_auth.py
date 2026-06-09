from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from cli.onboard import DEFAULTS

from .config import DEFAULT_ENV


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
LiveValidator = Callable[[], bool]


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


def _env_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


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
    lines = [f"{key}={_env_quote(values[key])}" for key in order if values.get(key)]
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _restore_env(env_path: Path, previous: str | None) -> None:
    if previous is None:
        env_path.unlink(missing_ok=True)
    else:
        env_path.write_text(previous, encoding="utf-8")


def _default_live_validator() -> bool:
    from .auth_status import auth_status

    status = auth_status(live=True)
    return status.live_ok is True


def import_web_auth(
    capture: CaptureInput,
    *,
    env_path: Path | None = None,
    live_validator: LiveValidator | None = None,
    validate_live: bool = True,
) -> WebAuthResult:
    env_path = env_path or Path(os.environ.get("PLAUD_ENV_FILE", DEFAULT_ENV))
    parsed = _parse_capture(capture)
    if isinstance(parsed, WebAuthResult):
        return parsed

    required = {
        "authorization": _clean(parsed.authorization),
        "x_device_id": _clean(parsed.x_device_id),
        "x_pld_user": _clean(parsed.x_pld_user),
        "cookie": _clean(parsed.cookie),
    }
    missing = [key for key, value in required.items() if value is None]
    if missing:
        return WebAuthResult(
            "missing_required",
            "missing required Web Login fields: " + ", ".join(missing),
            cookie_captured=required["cookie"] is not None,
        )

    values = dict(DEFAULTS)
    if base_url := _clean(parsed.base_url):
        values["PLAUD_BASE_URL"] = base_url
    values["PLAUD_AUTHORIZATION"] = required["authorization"] or ""
    values["PLAUD_X_DEVICE_ID"] = required["x_device_id"] or ""
    values["PLAUD_X_PLD_USER"] = required["x_pld_user"] or ""
    values["PLAUD_COOKIE"] = required["cookie"] or ""

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

    try:
        previous = env_path.read_text(encoding="utf-8") if env_path.exists() else None
        _write_env(values, env_path)
    except OSError as exc:
        return WebAuthResult("write_failed", str(exc), cookie_captured=True)

    if validate_live:
        validator = live_validator or _default_live_validator
        if not validator():
            try:
                _restore_env(env_path, previous)
            except OSError as exc:
                return WebAuthResult("rollback_failed", str(exc), cookie_captured=True)
            return WebAuthResult(
                "live_auth_failed",
                "captured Plaud credentials were rejected; restored previous .env",
                cookie_captured=True,
            )

    return WebAuthResult(
        "ok",
        "credentials refreshed from Plaud Web Login",
        cookie_captured=True,
    )
