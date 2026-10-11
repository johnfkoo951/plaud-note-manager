"""Multi-model summarization via local CLIs *or* direct API calls.

Backend choice is per-model and stored in `data/config.json`:

  - "cli": shell out to `claude` / `codex` / `gemini` / `grok` (uses each CLI's
    auth — OAuth subscription, API key, whatever the CLI is logged in with).
  - "api": direct HTTP with the provider's REST API and an env-var key
    (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`).

Both modes accept the same `prompt` string and return the model's text reply,
so the caller (`summarize`, `cmds-integrate`) doesn't care which path was taken.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import app_config
from .paths import summary_path
from .templates import KIND_SUMMARY, load_template, require_kind

# CLI invocation per model.
MODEL_COMMANDS: dict[str, list[str]] = {
    "claude": ["claude", "--print"],
    "codex": ["codex", "exec", "--skip-git-repo-check"],
    # Gemini CLI headless: `-p` takes the prompt (docs/cli/tutorials/automation);
    # stdin is treated as *context*, so the prompt must go as the last argv.
    "gemini": ["gemini", "-p"],
    # Grok Build (SuperGrok subscription OAuth — no API cost). `-p` takes the
    # prompt as the LAST argv element; stdin piping is not supported.
    "grok": ["grok", "--disable-web-search", "--no-memory", "--no-subagents", "-p"],
}

# Models whose CLI takes the prompt as an argument instead of stdin.
PROMPT_AS_ARG = {"grok", "gemini"}

# macOS ARG_MAX is 1 MiB shared with the environment; leave generous headroom.
PROMPT_ARG_LIMIT = 700_000

# GUI apps inherit a minimal PATH; known install locations checked as fallback.
CLI_FALLBACK_PATHS: dict[str, list[Path]] = {
    "grok": [Path.home() / ".grok" / "bin" / "grok"],
    "claude": [Path.home() / ".local" / "bin" / "claude"],
    "codex": [Path.home() / ".local" / "bin" / "codex"],
    "gemini": [Path.home() / ".local" / "bin" / "gemini"],
}

# API-key env vars hidden from each vendor CLI so it authenticates with the
# OAuth / subscription session instead of billing the key. (Claude Code,
# Codex and Gemini CLI all prefer an env key when one is present.)
CLI_STRIP_ENV: dict[str, tuple[str, ...]] = {
    "claude": ("ANTHROPIC_API_KEY",),
    "codex": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "grok": ("XAI_API_KEY",),
}


class ModelCallError(RuntimeError):
    """A model call gave no usable reply. Its text is safe to show and store:
    every raise site builds it from fixed phrases, model names and numbers,
    never from what the provider printed or returned."""


class ModelNotInstalled(ModelCallError):
    pass


class ModelFailed(ModelCallError):
    pass


class PromptTooLarge(ModelFailed):
    """This file's prompt can't be passed to the CLI — not a provider failure."""


# Why a provider call failed, in words of our own. A vendor CLI echoes the
# prompt into its diagnostics and an API error body can quote the request or
# the key, in forms (prefixed, escaped, cut) no scrub could be shown to catch.
# So that text is only read, to pick one of these; it is never kept.
REASON_QUOTA = "usage limit or quota reached"
REASON_AUTH = "not logged in or not authorized"
REASON_MODEL = "model not available to this account"
REASON_TOO_LONG = "input too long for the model"
REASON_OVERLOADED = "provider overloaded or unavailable"
REASON_REJECTED = "request rejected"
REASON_UNKNOWN = "no known cause in its output"
REASON_SILENT = "it printed nothing"

# Markers matched case-insensitively; the first kind that matches wins.
FAILURE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        REASON_QUOTA,
        (
            "usage limit",
            "quota",
            "rate limit",
            "rate_limit",
            "too many requests",
            "resource_exhausted",
            "credit balance",
        ),
    ),
    (
        REASON_AUTH,
        (
            "not logged in",
            "unauthor",
            "unauthenticated",
            "forbidden",
            "permission_denied",
            "permission denied",
            "authentication",
            "api key",
            "api_key",
            "login",
            "log in",
            "expired",
        ),
    ),
    (
        REASON_MODEL,
        ("ineligibletier", "model_not_found", "model not found", "not_found_error"),
    ),
    (
        REASON_TOO_LONG,
        ("context length", "context_length", "context window", "too many tokens", "too long"),
    ),
    (
        REASON_OVERLOADED,
        ("overloaded", "service unavailable", "internal server error", "server_error"),
    ),
)

# An HTTP status that names the cause by itself.
_STATUS_REASONS = {
    401: REASON_AUTH,
    403: REASON_AUTH,
    404: REASON_MODEL,
    413: REASON_TOO_LONG,
    429: REASON_QUOTA,
}


def _failure_kind(text: str) -> str:
    """The REASON_* a provider's own text points to, or ""."""
    low = text.lower()
    for reason, markers in FAILURE_MARKERS:
        if any(marker in low for marker in markers):
            return reason
    return ""


def failure_reason(exc: BaseException, *, limit: int = 400) -> str:
    """Why a call failed, as one stable line of at most `limit` characters.

    The same text goes to the CLI's error line, the DB and the integrate
    sidecar. A ModelCallError's text is ours (see its docstring). A transport
    or subprocess error is named by class only: its text quotes the request
    URL (the Gemini key rides in it) or the argv (the whole prompt, for the
    PROMPT_AS_ARG CLIs). Anything else is a local error, named with its text.
    """
    name = type(exc).__name__
    if isinstance(exc, ModelCallError):
        text = str(exc)
    elif isinstance(exc, httpx.HTTPError):
        text = f"network error: {name}"
    elif isinstance(exc, subprocess.SubprocessError):
        text = name
    else:
        text = f"{name}: {exc}" if str(exc) else name
    text = " ".join(text.split()) or name
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def is_provider_failure(exc: BaseException) -> bool:
    """True when the provider itself failed, so the next call would fail too.

    Quota, login, timeout, an empty reply or a missing CLI all qualify; a
    prompt too large for one file's argv is that file's problem.
    """
    return isinstance(exc, (ModelFailed, ModelNotInstalled)) and not isinstance(exc, PromptTooLarge)


@dataclass
class ProviderBreaker:
    """Stops further model calls within one run once the provider has failed.

    It lives for a single command; nothing is remembered across runs. The
    first reason wins, because it is the one that says what went wrong.
    """

    reason: str = ""

    @property
    def tripped(self) -> bool:
        return bool(self.reason)

    def trip(self, reason: str) -> None:
        if not self.reason:
            self.reason = reason or "provider call failed"


# -------- public entry --------


def _node_version_key(path: Path) -> tuple[int, ...]:
    """Sort key for an `~/.nvm/versions/node/vX.Y.Z` directory."""
    try:
        return tuple(int(part) for part in path.name.lstrip("v").split("."))
    except ValueError:
        return (0,)


def _nvm_candidates(name: str) -> list[Path]:
    """Node-installed CLIs, newest nvm version first.

    A GUI-launched app inherits a minimal PATH with no nvm shims, so
    `shutil.which` misses a CLI the user installed with npm and the provider
    reports as "CLI missing" even though it is right there in the terminal.
    """
    nvm = Path.home() / ".nvm/versions/node"
    if not nvm.is_dir():
        return []
    versions = sorted(nvm.glob("v*"), key=_node_version_key, reverse=True)
    return [v / "bin" / name for v in versions]


def _resolve_binary(model: str, name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    candidates = [
        *CLI_FALLBACK_PATHS.get(model, []),
        *_nvm_candidates(name),
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def model_available(model: str) -> bool:
    """Whether the CLI or the API key exists — not whether it works.

    Existence-only by design: auth, quota and tier failures surface from the
    call itself as a ModelCallError with the provider's reason.
    """
    backend = app_config.backend_for(model)
    if backend == "cli":
        cmd = MODEL_COMMANDS.get(model)
        return bool(cmd and _resolve_binary(model, cmd[0]))
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
    if backend not in ("cli", "api"):
        raise ModelNotInstalled(f"unknown backend: {backend}")
    try:
        if backend == "cli":
            return _run_cli(model, prompt, timeout=timeout)
        reply = _run_api(model, prompt, model_id=model_id, timeout=timeout)
    except ModelCallError:
        raise
    except httpx.HTTPError as exc:
        # The request URL can carry the key (Gemini); name the error only.
        raise ModelFailed(f"{model} API request failed: {type(exc).__name__}") from None
    except Exception as exc:
        # A parse or OS error inside the backend; its text may quote the call.
        raise ModelFailed(f"{model} {backend.upper()} call failed: {type(exc).__name__}") from None
    if not reply.strip():
        raise ModelFailed(f"{model} API returned an empty reply")
    return reply


# -------- CLI mode --------


def _cli_reason(proc: subprocess.CompletedProcess[str], *, prompt: str = "") -> str:
    """Why a vendor CLI failed, as a REASON_* phrase — never its own text.

    Vendor CLIs print a banner (version, workdir, model, sandbox) and echo the
    prompt before they fail, and the real cause — an exhausted quota, a revoked
    login — is the LAST thing they write. So lines are read from the end,
    skipping lines of the echoed `prompt` (a transcript can mention "quota"
    too), and the first one that names a known cause decides.
    """
    text = (proc.stderr or "").strip() or (proc.stdout or "").strip()
    if not text:
        return REASON_SILENT
    echoed = {ln.strip() for ln in prompt.splitlines()}
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line and line not in echoed:
            kind = _failure_kind(line)
            if kind:
                return kind
    return REASON_UNKNOWN


def _run_cli(model: str, prompt: str, *, timeout: int) -> str:
    cmd = MODEL_COMMANDS.get(model)
    if not cmd:
        if _api_key_env(model):
            raise ModelNotInstalled(f"{model} has no CLI backend — set {model} to api.")
        raise ModelNotInstalled(f"unknown model: {model}")
    binary = _resolve_binary(model, cmd[0])
    if not binary:
        raise ModelNotInstalled(f"`{cmd[0]}` CLI not on PATH. Install it or switch backend to api.")
    argv = [binary, *cmd[1:]]
    stdin_input: str | None = prompt
    env = None
    if model in PROMPT_AS_ARG:
        if len(prompt.encode("utf-8")) > PROMPT_ARG_LIMIT:
            raise PromptTooLarge(
                f"prompt too large for the {model} CLI ({len(prompt.encode('utf-8'))} bytes "
                f"> {PROMPT_ARG_LIMIT}); switch this slot to another backend for this file."
            )
        argv.append(prompt)
        stdin_input = None
    strip = CLI_STRIP_ENV.get(model)
    if strip:
        env = {k: v for k, v in os.environ.items() if k not in strip}
    try:
        proc = subprocess.run(
            argv,
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        # Its text quotes argv, which is the whole prompt for PROMPT_AS_ARG.
        raise ModelFailed(f"{model} CLI timed out after {timeout}s") from None
    if proc.returncode != 0:
        reason = _cli_reason(proc, prompt=prompt)
        raise ModelFailed(f"{model} CLI failed (exit {proc.returncode}): {reason}")
    reply = proc.stdout.strip()
    if not reply:
        # Exit 0 with nothing on stdout (a quota notice on stderr, typically)
        # is not an answer; caching it would stop the file being retried.
        raise ModelFailed(f"{model} CLI returned no output: {_cli_reason(proc, prompt=prompt)}")
    return reply


# -------- API mode --------


def _api_key_env(model: str) -> str:
    return {
        "claude": "ANTHROPIC_API_KEY",
        "codex": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "grok": "XAI_API_KEY",
    }.get(model, "")


def _api_failure(provider: str, response: httpx.Response) -> ModelFailed:
    """A non-200 reply, named by its status and a REASON_* phrase only.

    The body is read to find the cause but never kept: it can quote the
    request, which is the prompt, and the key.
    """
    status = response.status_code
    kind = _STATUS_REASONS.get(status) or (REASON_OVERLOADED if status >= 500 else "")
    if not kind:
        try:
            body = str(response.text)
        except Exception:  # an undecodable body names nothing
            body = ""
        kind = _failure_kind(body) or REASON_REJECTED
    return ModelFailed(f"{provider} API {status}: {kind}")


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
    model_id = model_id or app_config.model_id_for("claude") or "claude-fable-5"
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
        raise _api_failure("Anthropic", r)
    data = r.json()
    parts = data.get("content") or []
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")


def _openai_api(prompt: str, *, model_id: str | None, timeout: int) -> str:
    key = os.environ.get("OPENAI_API_KEY") or _zshrc_value("OPENAI_API_KEY")
    if not key:
        raise ModelNotInstalled("OPENAI_API_KEY not set")
    model_id = model_id or app_config.model_id_for("codex") or "gpt-5.6-sol"
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
        raise _api_failure("OpenAI", r)
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
        raise _api_failure("Gemini", r)
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
    model_id = model_id or app_config.model_id_for("grok") or "grok-4.6"
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
        raise _api_failure("xAI", r)
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
    require_kind(template, KIND_SUMMARY)  # before the model call and any write
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
