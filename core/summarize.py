"""Multi-model summarization via local CLIs *or* direct API calls.

Backend choice is per-model and stored in `data/config.json`:

  - "cli": shell out to `claude` / `codex` / `gemini` (uses each CLI's auth —
    OAuth subscription, API key, whatever the CLI is logged in with).
  - "api": direct HTTP with the provider's REST API and an env-var key
    (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`).

Both modes accept the same `prompt` string and return the model's text reply,
so the caller (`summarize`, `cmds-integrate`) doesn't care which path was taken.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import httpx

from . import app_config
from .paths import summary_path
from .templates import load_template

# CLI invocation per model.
MODEL_COMMANDS: dict[str, list[str]] = {
    "claude": ["claude", "--print"],
    "codex": ["codex", "exec", "--skip-git-repo-check"],
    "gemini": ["gemini", "--prompt-interactive=false"],
}


class ModelNotInstalled(RuntimeError):
    pass


class ModelFailed(RuntimeError):
    pass


# -------- public entry --------


def model_available(model: str) -> bool:
    backend = app_config.backend_for(model)
    if backend == "cli":
        cmd = MODEL_COMMANDS.get(model)
        return bool(cmd and shutil.which(cmd[0]))
    if backend == "api":
        env_key = _api_key_env(model)
        return bool(env_key and (os.environ.get(env_key) or _zshrc_value(env_key)))
    return False


def model_unavailable_message(model: str) -> str:
    backend = app_config.backend_for(model)
    if backend == "cli":
        cmd = MODEL_COMMANDS.get(model)
        if not cmd:
            if _api_key_env(model):
                return f"{model} has no CLI backend — set {model} to api."
            return f"unknown CLI model: {model}"
        return f"`{cmd[0]}` CLI not installed. Switch {model} to api or install the CLI."
    if backend == "api":
        env_key = _api_key_env(model)
        if not env_key:
            return f"unknown API model: {model}"
        return f"{env_key} not set for {model} API backend."
    return f"unknown backend for {model}: {backend}"


def run_model(
    model: str,
    prompt: str,
    *,
    model_id: str | None = None,
    timeout: int = 600,
) -> str:
    backend = app_config.backend_for(model)
    if backend == "cli":
        return _run_cli(model, prompt, timeout=timeout)
    if backend == "api":
        return _run_api(model, prompt, model_id=model_id, timeout=timeout)
    raise ModelNotInstalled(f"unknown backend: {backend}")


# -------- CLI mode --------


def _run_cli(model: str, prompt: str, *, timeout: int) -> str:
    cmd = MODEL_COMMANDS.get(model)
    if not cmd:
        if _api_key_env(model):
            raise ModelNotInstalled(f"{model} has no CLI backend — set {model} to api.")
        raise ModelNotInstalled(f"unknown model: {model}")
    if not shutil.which(cmd[0]):
        raise ModelNotInstalled(f"`{cmd[0]}` CLI not on PATH. Install it or switch backend to api.")
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise ModelFailed(
            f"{model} CLI failed (exit {proc.returncode}): {(proc.stderr or '')[:400]}"
        )
    return proc.stdout.strip()


# -------- API mode --------


def _api_key_env(model: str) -> str:
    return {
        "claude": "ANTHROPIC_API_KEY",
        "codex": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "grok": "XAI_API_KEY",
    }.get(model, "")


def _run_api(model: str, prompt: str, *, model_id: str | None, timeout: int) -> str:
    if model == "claude":
        return _claude_api(prompt, model_id=model_id, timeout=timeout)
    if model == "codex":
        return _openai_api(prompt, model_id=model_id, timeout=timeout)
    if model == "gemini":
        return _gemini_api(prompt, model_id=model_id, timeout=timeout)
    if model == "grok":
        return _xai_api(prompt, model_id=model_id, timeout=timeout)
    raise ModelNotInstalled(f"unknown api model: {model}")


def _claude_api(prompt: str, *, model_id: str | None, timeout: int) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY") or _zshrc_value("ANTHROPIC_API_KEY")
    if not key:
        raise ModelNotInstalled("ANTHROPIC_API_KEY not set")
    model_id = model_id or app_config.model_id_for("claude") or "claude-opus-4-7"
    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model_id,
            "max_tokens": 16000,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=timeout,
    )
    if r.status_code != 200:
        raise ModelFailed(f"Anthropic API {r.status_code}: {r.text[:400]}")
    data = r.json()
    parts = data.get("content") or []
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")


def _openai_api(prompt: str, *, model_id: str | None, timeout: int) -> str:
    key = os.environ.get("OPENAI_API_KEY") or _zshrc_value("OPENAI_API_KEY")
    if not key:
        raise ModelNotInstalled("OPENAI_API_KEY not set")
    model_id = model_id or app_config.model_id_for("codex") or "gpt-5.5"
    r = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "content-type": "application/json",
        },
        json={
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=timeout,
    )
    if r.status_code != 200:
        raise ModelFailed(f"OpenAI API {r.status_code}: {r.text[:400]}")
    data = r.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")


def _gemini_api(prompt: str, *, model_id: str | None, timeout: int) -> str:
    key = os.environ.get("GEMINI_API_KEY") or _zshrc_value("GEMINI_API_KEY")
    if not key:
        raise ModelNotInstalled("GEMINI_API_KEY not set")
    model_id = model_id or app_config.model_id_for("gemini") or "gemini-3.1-pro-preview"
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent",
        params={"key": key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=timeout,
    )
    if r.status_code != 200:
        raise ModelFailed(f"Gemini API {r.status_code}: {r.text[:400]}")
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        return ""
    parts = cands[0].get("content", {}).get("parts", []) or []
    return "".join(p.get("text", "") for p in parts)


def _xai_api(prompt: str, *, model_id: str | None, timeout: int) -> str:
    key = os.environ.get("XAI_API_KEY") or _zshrc_value("XAI_API_KEY")
    if not key:
        raise ModelNotInstalled("XAI_API_KEY not set")
    model_id = model_id or app_config.model_id_for("grok") or "grok-4.20-0309-reasoning"
    r = httpx.post(
        "https://api.x.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "content-type": "application/json",
        },
        json={
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16000,
        },
        timeout=timeout,
    )
    if r.status_code != 200:
        raise ModelFailed(f"xAI API {r.status_code}: {r.text[:400]}")
    data = r.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")


def _zshrc_value(name: str) -> str | None:
    """Fallback: scrape `export NAME=...` from ~/.zshrc when env not loaded."""
    import re

    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists():
        return None
    text = zshrc.read_text(encoding="utf-8", errors="ignore")
    # Handle double-quoted (may contain spaces), single-quoted, or bare values.
    m = re.search(
        rf"export\s+{re.escape(name)}\s*=\s*"
        r"(?:\"([^\"]*)\"|'([^']*)'|([^\s#]+))",
        text,
    )
    if not m:
        return None
    return m.group(1) or m.group(2) or m.group(3)


# -------- summarize entry point --------


def summarize(
    *,
    file_id: str,
    model: str,
    template_name: str,
    transcript: str,
    title: str = "",
    keywords: str = "",
    speakers: str = "",
    model_id: str = "",
) -> Path:
    template = load_template(template_name)
    prompt = template.render(
        transcript=transcript,
        title=title,
        keywords=keywords,
        speakers=speakers,
    )
    response = run_model(model, prompt, model_id=model_id or None)
    output_model = model_id or model
    out_path = summary_path(file_id, model=output_model, template=template_name)
    front = (
        f"---\nfile_id: {file_id}\nprovider: {model}\nmodel: {output_model}\n"
        f"template: {template_name}\n---\n\n"
    )
    out_path.write_text(front + response, encoding="utf-8")
    return out_path
