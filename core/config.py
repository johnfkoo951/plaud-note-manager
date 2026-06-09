"""Load Plaud credentials from .env into a typed config object."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = PROJECT_ROOT / ".env"


class ConfigError(RuntimeError):
    """Raised when Plaud credentials are missing/unconfigured."""


class PlaudConfig(BaseModel):
    base_url: str = "https://api-apne1.plaud.ai"
    authorization: str
    x_device_id: str
    x_pld_user: str
    x_pld_tag: str = ""  # legacy header; current web API no longer sends it
    app_language: str = "en"
    app_platform: str = "web"
    edit_from: str = "web"
    origin: str = "https://web.plaud.ai"
    referer: str = "https://web.plaud.ai/"
    timezone: str = "Asia/Seoul"
    cookie: str = ""

    def headers(self) -> dict[str, str]:
        h = {
            "accept": "application/json, text/plain, */*",
            "app-language": self.app_language,
            "app-platform": self.app_platform,
            "authorization": self.authorization,
            "edit-from": self.edit_from,
            "origin": self.origin,
            "referer": self.referer,
            "timezone": self.timezone,
            "x-device-id": self.x_device_id,
            "x-pld-user": self.x_pld_user,
        }
        if self.x_pld_tag:  # only send the legacy tag header when present
            h["x-pld-tag"] = self.x_pld_tag
        if self.cookie:
            h["cookie"] = self.cookie
        return h


def load_config(env_file: Path | None = None) -> PlaudConfig:
    env_path = env_file or Path(os.environ.get("PLAUD_ENV_FILE", DEFAULT_ENV))
    if env_path.exists():
        # .env is the source of truth for credentials so re-onboarding (token
        # rotation) takes effect immediately. Trade-off: a stale PLAUD_* already
        # exported in the process env no longer shadows the file.
        load_dotenv(env_path, override=True)

    required = {
        "authorization": "PLAUD_AUTHORIZATION",
        "x_device_id": "PLAUD_X_DEVICE_ID",
        "x_pld_user": "PLAUD_X_PLD_USER",
    }
    missing = [name for name in required.values() if not os.environ.get(name)]
    if missing:
        raise ConfigError(f"missing Plaud credentials: {', '.join(missing)} (env file: {env_path})")

    return PlaudConfig(
        base_url=os.environ.get("PLAUD_BASE_URL", "https://api-apne1.plaud.ai"),
        authorization=os.environ["PLAUD_AUTHORIZATION"],
        x_device_id=os.environ["PLAUD_X_DEVICE_ID"],
        x_pld_tag=os.environ.get("PLAUD_X_PLD_TAG", ""),
        x_pld_user=os.environ["PLAUD_X_PLD_USER"],
        app_language=os.environ.get("PLAUD_APP_LANGUAGE", "en"),
        app_platform=os.environ.get("PLAUD_APP_PLATFORM", "web"),
        edit_from=os.environ.get("PLAUD_EDIT_FROM", "web"),
        origin=os.environ.get("PLAUD_ORIGIN", "https://web.plaud.ai"),
        referer=os.environ.get("PLAUD_REFERER", "https://web.plaud.ai/"),
        timezone=os.environ.get("PLAUD_TIMEZONE", "Asia/Seoul"),
        cookie=os.environ.get("PLAUD_COOKIE", ""),
    )
