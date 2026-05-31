"""User-tunable application config: backend mode per model + output paths.

Lives at `data/config.json`. CLI commands `plaud config-*` and the SwiftUI
Settings sheet read/write the same file so changes are instantly visible to
both surfaces.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Literal

from .paths import DATA_DIR

CONFIG_FILE = DATA_DIR / "config.json"

Backend = Literal["cli", "api"]

DEFAULT_CONFIG: dict = {
    "backends": {
        "claude": "cli",
        "codex": "cli",
        "gemini": "cli",
        "grok": "api",
    },
    "models": {
        # Used only when backend = api. CLI mode picks whatever the CLI
        # defaults to.
        "claude": "claude-opus-4-7",
        "codex": "gpt-5.5",
        "gemini": "gemini-3.1-pro-preview",
        "grok": "grok-4.20-0309-reasoning",
    },
    "paths": {
        # Empty string = fall back to the project default.
        "transcripts": "",
        "summaries": "",
        "integrated": "",
    },
    # Personal / environment-specific locations. Empty = unset; a fresh clone
    # gets safe empty defaults and the user opts in via env or `plaud config-*`.
    "obsidian_vault": "",
    "author": "",
    "api_info_dir": "",
}


def load() -> dict:
    if not CONFIG_FILE.exists():
        return deepcopy(DEFAULT_CONFIG)
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return deepcopy(DEFAULT_CONFIG)
    # Merge with defaults so new keys are filled in.
    merged = deepcopy(DEFAULT_CONFIG)
    for key, val in data.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key].update(val)
        else:
            merged[key] = val
    return merged


def save(config: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def backend_for(model: str) -> Backend:
    return load().get("backends", {}).get(model, "cli")  # type: ignore[return-value]


def model_id_for(model: str) -> str:
    return load().get("models", {}).get(model, "")


def path_override(kind: str) -> Path | None:
    raw = load().get("paths", {}).get(kind, "")
    if not raw:
        return None
    return Path(os.path.expanduser(raw)).resolve()


def obsidian_vault() -> Path | None:
    """Resolved Obsidian vault path: env > config > None."""
    raw = os.environ.get("PLAUD_OBSIDIAN_VAULT") or load().get("obsidian_vault", "")
    if not raw:
        return None
    return Path(os.path.expanduser(raw)).resolve()


def author() -> str:
    """Author name for generated notes: env > config > empty string."""
    return os.environ.get("PLAUD_AUTHOR") or load().get("author", "") or ""


def api_info_dir() -> Path | None:
    """CMDS API Information directory: env > config > derived from vault > None."""
    raw = os.environ.get("PLAUD_API_INFO_DIR") or load().get("api_info_dir", "")
    if raw:
        return Path(os.path.expanduser(raw)).resolve()
    vault = obsidian_vault()
    if vault:
        return vault / "40. Docs/49. API Information"
    return None


def set_backend(model: str, backend: Backend) -> None:
    cfg = load()
    cfg["backends"][model] = backend
    save(cfg)


def set_model_id(model: str, model_id: str) -> None:
    cfg = load()
    cfg["models"][model] = model_id
    save(cfg)


def set_path(kind: str, path: str) -> None:
    cfg = load()
    cfg["paths"][kind] = path
    save(cfg)


def set_obsidian_vault(path: str) -> None:
    cfg = load()
    cfg["obsidian_vault"] = path
    save(cfg)


def set_author(name: str) -> None:
    cfg = load()
    cfg["author"] = name
    save(cfg)
