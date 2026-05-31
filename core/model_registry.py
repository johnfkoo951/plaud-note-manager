"""Model preset registry sourced from the user's CMDS API Information notes."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import app_config

PROVIDER_IDS = {
    "anthropic": "claude",
    "openai": "codex",
    "google": "gemini",
    "xai": "grok",
}

PROVIDER_LABELS = {
    "claude": "Anthropic",
    "codex": "OpenAI",
    "gemini": "Google",
    "grok": "xAI",
}

FALLBACK_PRESETS = [
    ("claude", "claude-opus-4-7", "Claude Opus 4.7", "Anthropic flagship reasoning"),
    ("claude", "claude-sonnet-4-6", "Claude Sonnet 4.6", "Anthropic balanced flagship"),
    ("codex", "gpt-5.5", "GPT-5.5", "OpenAI flagship"),
    ("codex", "gpt-5.5-pro", "GPT-5.5 Pro", "OpenAI maximum precision"),
    ("gemini", "gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview", "Google frontier"),
    ("gemini", "gemini-3-flash-preview", "Gemini 3 Flash Preview", "Google fast frontier"),
    ("grok", "grok-4.20-0309-reasoning", "Grok 4.20 Reasoning", "xAI frontier"),
    ("grok", "grok-4-1-fast-reasoning", "Grok 4.1 Fast", "xAI fast agentic"),
]


@dataclass(frozen=True)
class ModelPreset:
    provider: str
    provider_label: str
    api_name: str
    title: str
    description: str = ""
    status: str = ""
    is_sota: bool = False
    source_path: str = ""


def provider_label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider)


def list_model_presets(api_info_dir: Path | None = None) -> list[ModelPreset]:
    """Load active/preview text model presets from local CMDS API docs."""
    if api_info_dir is None:
        api_info_dir = app_config.api_info_dir()
    presets: list[ModelPreset] = []
    if api_info_dir and api_info_dir.exists():
        for path in sorted(api_info_dir.glob("*.md")):
            meta = _frontmatter(path)
            preset = _preset_from_meta(meta, path)
            if preset:
                presets.append(preset)

    if not presets:
        return [
            ModelPreset(
                provider=provider,
                provider_label=provider_label(provider),
                api_name=api_name,
                title=title,
                description=description,
                status="fallback",
                is_sota=True,
            )
            for provider, api_name, title, description in FALLBACK_PRESETS
        ]

    if any(preset.is_sota for preset in presets):
        presets = [preset for preset in presets if preset.is_sota]
    presets.sort(key=_sort_key)
    return presets


def presets_json(api_info_dir: Path | None = None) -> str:
    return json.dumps(
        [asdict(preset) for preset in list_model_presets(api_info_dir)],
        ensure_ascii=False,
        indent=2,
    )


def _frontmatter(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if not raw.startswith("---\n"):
        return {}
    end = raw.find("\n---\n", 4)
    if end < 0:
        return {}
    meta: dict[str, Any] = {}
    current_key = ""
    for line in raw[4:end].splitlines():
        stripped = line.lstrip()
        if not line.strip() or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_key:
            meta.setdefault(current_key, []).append(stripped[2:].strip().strip('"'))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        value = value.strip()
        if not value:
            meta[current_key] = []
        else:
            meta[current_key] = _parse_scalar(value)
    return meta


def _preset_from_meta(meta: dict[str, Any], path: Path) -> ModelPreset | None:
    api_name = str(meta.get("api_name") or "").strip()
    if not api_name:
        return None
    provider = _provider_id(str(meta.get("provider") or ""))
    if provider not in PROVIDER_LABELS:
        return None
    status = str(meta.get("status") or "").lower()
    if status and status not in {"active", "preview"}:
        return None
    if not _supports_text(meta):
        return None
    title = _clean_wikilink(str(meta.get("model_name") or api_name))
    return ModelPreset(
        provider=provider,
        provider_label=provider_label(provider),
        api_name=api_name,
        title=title,
        description=str(meta.get("description") or ""),
        status=status,
        is_sota=bool(meta.get("is_sota")),
        source_path=str(path),
    )


def _provider_id(raw: str) -> str:
    provider = _clean_wikilink(raw).lower()
    return PROVIDER_IDS.get(provider, provider)


def _supports_text(meta: dict[str, Any]) -> bool:
    outputs = meta.get("output_modalities") or []
    if isinstance(outputs, str):
        outputs = [outputs]
    if outputs and "text" not in [str(v).lower() for v in outputs]:
        return False
    raw_tags = meta.get("tags", [])
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    tags = [str(v).lower() for v in raw_tags]
    return not tags or "chat" in tags or "text-generation" in tags or "reasoning" in tags


def _sort_key(preset: ModelPreset) -> tuple[int, int, int, str]:
    provider_order = {"claude": 0, "codex": 1, "gemini": 2, "grok": 3}
    status_order = {"active": 0, "preview": 1}
    return (
        provider_order.get(preset.provider, 99),
        0 if preset.is_sota else 1,
        status_order.get(preset.status, 9),
        preset.api_name,
    )


def _parse_scalar(raw: str) -> Any:
    value = raw.strip().strip('"').strip("'")
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if (
        value.startswith("[")
        and value.endswith("]")
        and not (value.startswith("[[") and value.endswith("]]"))
    ):
        return [v.strip().strip('"').strip("'") for v in value[1:-1].split(",") if v.strip()]
    return value


def _clean_wikilink(raw: str) -> str:
    text = raw.strip().strip('"').strip("'")
    match = re.fullmatch(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", text)
    return match.group(1) if match else text
