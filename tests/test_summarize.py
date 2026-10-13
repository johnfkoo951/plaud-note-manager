import subprocess
from pathlib import Path

import pytest

import core.paths as paths_mod
import core.templates as templates_mod
from core import summarize
from core.templates import TemplateKindError


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Templates and summaries under tmp; run_model records instead of calling."""
    tdir = tmp_path / "templates"
    tdir.mkdir()
    monkeypatch.setattr(paths_mod, "TEMPLATES_DIR", tdir)
    monkeypatch.setattr(templates_mod, "TEMPLATES_DIR", tdir)
    monkeypatch.setattr(paths_mod, "SUMMARIES_DIR", tmp_path / "summaries")
    monkeypatch.setattr(paths_mod, "_override", lambda kind: None)
    calls: list = []
    monkeypatch.setattr(summarize, "run_model", lambda *a, **k: calls.append(a) or "요약")
    return {"tmp": tmp_path, "templates": tdir, "calls": calls}


def _template(tdir: Path, name: str, kind: str) -> None:
    (tdir / f"{name}.md").write_text(
        f"---\nname: {name}\nkind: {kind}\n---\n\n{{transcript}}\n", encoding="utf-8"
    )


@pytest.mark.parametrize("kind", ["integrated", "internal"])
def test_summarize_refuses_a_non_summary_template_before_the_model(isolated, kind) -> None:
    _template(isolated["templates"], "t", kind)
    with pytest.raises(TemplateKindError):
        summarize.summarize(file_id="f1", model="claude", template_name="t", transcript="x")
    assert isolated["calls"] == []
    assert not (isolated["tmp"] / "summaries").exists()


def test_summarize_runs_a_summary_template(isolated) -> None:
    _template(isolated["templates"], "t", "summary")
    out = summarize.summarize(file_id="f1", model="claude", template_name="t", transcript="x")
    assert len(isolated["calls"]) == 1
    assert out.read_text(encoding="utf-8").endswith("요약")


# -------- _api_key_env mapping --------


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude", "ANTHROPIC_API_KEY"),
        ("codex", "OPENAI_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("grok", "XAI_API_KEY"),
        ("unknown", ""),
    ],
)
def test_api_key_env_mapping(model: str, expected: str) -> None:
    assert summarize._api_key_env(model) == expected


# -------- model_unavailable_message branches --------


def _patch_backend(monkeypatch, backend: str) -> None:
    monkeypatch.setattr(summarize.app_config, "backend_for", lambda _model: backend)


def test_unavailable_message_cli_known_model(monkeypatch) -> None:
    _patch_backend(monkeypatch, "cli")
    msg = summarize.model_unavailable_message("claude")
    assert "`claude` CLI not installed" in msg
    assert "Switch claude to api" in msg


def test_unavailable_message_cli_grok_points_to_install(monkeypatch) -> None:
    # Grok now HAS a CLI backend (Grok Build, SuperGrok OAuth); when the binary
    # is missing the message offers install-or-switch like the other CLIs.
    _patch_backend(monkeypatch, "cli")
    monkeypatch.setattr(summarize, "_resolve_binary", lambda model, name: None)
    msg = summarize.model_unavailable_message("grok")
    assert "`grok` CLI not installed" in msg
    assert "Switch grok to api" in msg


def test_unavailable_message_cli_unknown_model(monkeypatch) -> None:
    _patch_backend(monkeypatch, "cli")
    msg = summarize.model_unavailable_message("nope")
    assert msg == "unknown CLI model: nope"


def test_unavailable_message_api_known_model(monkeypatch) -> None:
    _patch_backend(monkeypatch, "api")
    msg = summarize.model_unavailable_message("claude")
    assert msg == "ANTHROPIC_API_KEY not set for claude API backend."


def test_unavailable_message_api_unknown_model(monkeypatch) -> None:
    _patch_backend(monkeypatch, "api")
    msg = summarize.model_unavailable_message("nope")
    assert msg == "unknown API model: nope"


def test_unavailable_message_bogus_backend(monkeypatch) -> None:
    _patch_backend(monkeypatch, "weirdbackend")
    msg = summarize.model_unavailable_message("claude")
    assert msg == "unknown backend for claude: weirdbackend"


# -------- _zshrc_value --------


def _write_zshrc(monkeypatch, tmp_path: Path, body: str) -> None:
    (tmp_path / ".zshrc").write_text(body, encoding="utf-8")
    monkeypatch.setattr(summarize.Path, "home", staticmethod(lambda: tmp_path))


def test_zshrc_value_double_quoted_with_spaces(monkeypatch, tmp_path) -> None:
    # POST-FIX: double-quoted values may contain spaces.
    _write_zshrc(monkeypatch, tmp_path, 'export FOO="a b c"\n')
    assert summarize._zshrc_value("FOO") == "a b c"


def test_zshrc_value_single_quoted(monkeypatch, tmp_path) -> None:
    _write_zshrc(monkeypatch, tmp_path, "export BAR='secret value'\n")
    assert summarize._zshrc_value("BAR") == "secret value"


def test_zshrc_value_bare(monkeypatch, tmp_path) -> None:
    _write_zshrc(monkeypatch, tmp_path, "export BAZ=plain-token-123\n")
    assert summarize._zshrc_value("BAZ") == "plain-token-123"


def test_zshrc_value_absent_var(monkeypatch, tmp_path) -> None:
    _write_zshrc(monkeypatch, tmp_path, 'export OTHER="value"\n')
    assert summarize._zshrc_value("MISSING") is None


def test_zshrc_value_no_zshrc_file(monkeypatch, tmp_path) -> None:
    # No .zshrc written at all -> file does not exist -> None.
    monkeypatch.setattr(summarize.Path, "home", staticmethod(lambda: tmp_path))
    assert summarize._zshrc_value("ANYTHING") is None


# -------- _cli_reason: why a vendor CLI actually failed --------


class _Proc:
    """Stand-in for subprocess.CompletedProcess."""

    def __init__(self, stderr: str = "", stdout: str = "") -> None:
        self.stderr = stderr
        self.stdout = stdout


CODEX_BANNER = """Reading prompt from stdin...
OpenAI Codex v0.153.4
--------
workdir: /somewhere/project
model: gpt-6-astra
provider: openai
approval: never
sandbox: workspace-write [workdir, /tmp, $TMPDIR] (network access enabled)
reasoning effort: ultra
reasoning summaries: none
session id: 01a0a89d-0820-7133-8483-b9278fb1bf19
--------
user

당신은 녹음 전사의 화자 식별 전문가입니다.
ERROR: You've hit your usage limit. Visit https://example.test/usage to purchase more credits."""


def test_cli_reason_finds_the_cause_a_long_banner_used_to_truncate_away() -> None:
    # The banner alone is over 400 characters; the cause is the last line.
    assert len(CODEX_BANNER) > 400
    assert summarize._cli_reason(_Proc(stderr=CODEX_BANNER)) == summarize.REASON_QUOTA


@pytest.mark.parametrize(
    "text, reason",
    [
        ("ERROR: You've hit your usage limit. Try again at 5:36 PM.", "REASON_QUOTA"),
        ("Error: 429 Too Many Requests", "REASON_QUOTA"),
        ("Your credit balance is too low", "REASON_QUOTA"),
        ("Invalid API key · Please run /login", "REASON_AUTH"),
        ("Not logged in", "REASON_AUTH"),
        ("IneligibleTierError: this model is not available on your tier", "REASON_MODEL"),
        ("Error: prompt is too long: 250000 tokens > 200000 maximum", "REASON_TOO_LONG"),
        ("API Error: 529 Overloaded", "REASON_OVERLOADED"),
    ],
)
def test_cli_reason_names_the_cause_in_fixed_words(text: str, reason: str) -> None:
    assert summarize._cli_reason(_Proc(stderr=f"banner\n{text}")) == getattr(summarize, reason)


def test_cli_reason_never_returns_the_clis_own_text() -> None:
    assert summarize._cli_reason(_Proc(stderr="line one\nline four")) == summarize.REASON_UNKNOWN
    assert summarize._cli_reason(_Proc(stdout="error: boom")) == summarize.REASON_UNKNOWN


def test_cli_reason_reads_stdout_when_stderr_is_empty() -> None:
    assert summarize._cli_reason(_Proc(stdout="ERROR: quota exceeded")) == summarize.REASON_QUOTA


def test_cli_reason_handles_a_silent_failure() -> None:
    assert summarize._cli_reason(_Proc()) == summarize.REASON_SILENT


def test_cli_reason_ignores_causes_named_only_by_the_echoed_prompt() -> None:
    prompt = "회의 내용: 이번 달 quota 를 다 썼다"
    assert summarize._cli_reason(_Proc(stderr=prompt), prompt=prompt) == summarize.REASON_UNKNOWN


# -------- why a call failed: runner rules and the shared reason --------


@pytest.fixture()
def fake_cli(monkeypatch: pytest.MonkeyPatch):
    """`_run_cli` against a fake subprocess; `result` sets what it returns."""
    state: dict = {"result": None, "raise": None}

    def run(argv, *_a, **_k):
        if state["raise"] is not None:
            raise state["raise"]
        return state["result"]

    monkeypatch.setattr(summarize.subprocess, "run", run)
    monkeypatch.setattr(summarize, "_resolve_binary", lambda model, name: f"/fake/{name}")
    monkeypatch.setattr(summarize.app_config, "backend_for", lambda m: "cli")
    return state


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_run_cli_empty_stdout_is_a_failure_with_the_stderr_reason(fake_cli) -> None:
    fake_cli["result"] = _Completed(0, "", "ERROR: You've hit your usage limit.")
    with pytest.raises(summarize.ModelFailed, match="returned no output.*usage limit"):
        summarize.run_model("codex", "prompt")


def test_run_cli_timeout_is_reported_without_the_prompt(fake_cli) -> None:
    fake_cli["raise"] = subprocess.TimeoutExpired(
        cmd=["/fake/grok", "-p", "SECRET TRANSCRIPT"], timeout=10
    )
    with pytest.raises(summarize.ModelFailed) as exc:
        summarize.run_model("grok", "SECRET TRANSCRIPT", timeout=10)
    assert "timed out after 10s" in str(exc.value)
    assert "SECRET TRANSCRIPT" not in summarize.failure_reason(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_an_echoed_prompt_never_becomes_the_reason(fake_cli) -> None:
    # Codex echoes the prompt into stderr; with no error-looking line the tail
    # of stderr used to be the prompt itself.
    prompt = "당신은 녹음 전사의 화자 식별 전문가입니다.\n비밀 전사 내용 한 줄"
    banner = "OpenAI Codex v0.153.4\n--------\nuser\n"
    fake_cli["result"] = _Completed(1, "", banner + prompt)
    with pytest.raises(summarize.ModelFailed) as exc:
        summarize.run_model("codex", prompt)
    reason = summarize.failure_reason(exc.value)
    assert "비밀 전사" not in reason and "화자 식별" not in reason


SENTINEL = "SYNTHETIC_PRIVATE_TRANSCRIPT_41"
KEY = "SYNTHETIC_KEY_42"

# (stdout, stderr) of a failing CLI that quotes the prompt in some form.
CLI_ECHOES = {
    "prefixed": ("", f"ERROR: rejected input: {SENTINEL}"),
    "multiline": ("", f"ERROR: bad request\n  near: {SENTINEL}\nERROR: {SENTINEL} (cut)"),
    "escaped": ("", '{"error": "invalid: \\"' + SENTINEL + '\\""}'),
    "partial": ("", f"error: input rejected near '{SENTINEL[:12]}'"),
    "stdout": (f"error while reading {SENTINEL}", ""),
}


@pytest.mark.parametrize("echo", CLI_ECHOES)
def test_no_cli_diagnostic_text_reaches_the_reason(fake_cli, echo: str) -> None:
    stdout, stderr = CLI_ECHOES[echo]
    fake_cli["result"] = _Completed(1, stdout, stderr)
    with pytest.raises(summarize.ModelFailed) as exc:
        summarize.run_model("codex", f"prompt line\n{SENTINEL}")
    reason = summarize.failure_reason(exc.value)
    assert "SYNTHETIC" not in reason
    assert reason == f"codex CLI failed (exit 1): {summarize.REASON_UNKNOWN}"


def test_a_known_cause_is_kept_without_the_echo_around_it(fake_cli) -> None:
    fake_cli["result"] = _Completed(1, "", f"ERROR: You've hit your usage limit. Input: {SENTINEL}")
    with pytest.raises(summarize.ModelFailed) as exc:
        summarize.run_model("codex", SENTINEL)
    assert str(exc.value) == f"codex CLI failed (exit 1): {summarize.REASON_QUOTA}"


API_PROVIDERS = [
    ("claude", "Anthropic", "ANTHROPIC_API_KEY"),
    ("codex", "OpenAI", "OPENAI_API_KEY"),
    ("gemini", "Gemini", "GEMINI_API_KEY"),
    ("grok", "xAI", "XAI_API_KEY"),
]


def _api_reply(monkeypatch, model: str, env: str, status: int, body: str) -> None:
    import types

    monkeypatch.setattr(summarize.app_config, "backend_for", lambda m: "api")
    monkeypatch.setattr(summarize.app_config, "model_id_for", lambda m: "")
    monkeypatch.setenv(env, KEY)
    reply = types.SimpleNamespace(status_code=status, text=body)
    monkeypatch.setattr(summarize.httpx, "post", lambda *a, **k: reply)


@pytest.mark.parametrize("model, label, env", API_PROVIDERS)
def test_no_api_error_body_reaches_the_reason(monkeypatch, model, label, env) -> None:
    body = '{"error": {"message": "invalid request: ' + SENTINEL + "; key=" + KEY + '"}}'
    _api_reply(monkeypatch, model, env, 400, body)
    with pytest.raises(summarize.ModelFailed) as exc:
        summarize.run_model(model, f"prompt {SENTINEL}")
    reason = summarize.failure_reason(exc.value)
    assert SENTINEL not in reason and KEY not in reason
    assert reason == f"{label} API 400: {summarize.REASON_REJECTED}"


@pytest.mark.parametrize(
    "status, body, reason",
    [
        (401, "", "REASON_AUTH"),
        (403, "", "REASON_AUTH"),
        (404, "", "REASON_MODEL"),
        (429, "", "REASON_QUOTA"),
        (529, "", "REASON_OVERLOADED"),
        (
            400,
            '{"error": {"message": "API key not valid. Please pass a valid API key."}}',
            "REASON_AUTH",
        ),
        (400, '{"error": {"message": "prompt is too long: 300000 tokens"}}', "REASON_TOO_LONG"),
    ],
)
def test_an_api_failure_keeps_its_status_and_cause(monkeypatch, status, body, reason) -> None:
    _api_reply(monkeypatch, "gemini", "GEMINI_API_KEY", status, body + SENTINEL)
    with pytest.raises(summarize.ModelFailed) as exc:
        summarize.run_model("gemini", SENTINEL)
    assert str(exc.value) == f"Gemini API {status}: {getattr(summarize, reason)}"


def test_an_unexpected_backend_error_is_named_by_class_only(monkeypatch) -> None:
    monkeypatch.setattr(summarize.app_config, "backend_for", lambda m: "api")

    def broken(*_a, **_k):
        raise ValueError(f"cannot parse the reply to {SENTINEL}")

    monkeypatch.setattr(summarize, "_run_api", broken)
    with pytest.raises(summarize.ModelFailed) as exc:
        summarize.run_model("gemini", SENTINEL)
    assert str(exc.value) == "gemini API call failed: ValueError"
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_api_empty_reply_and_transport_errors_are_failures(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(summarize.app_config, "backend_for", lambda m: "api")
    monkeypatch.setattr(summarize, "_run_api", lambda *a, **k: "  ")
    with pytest.raises(summarize.ModelFailed, match="empty reply"):
        summarize.run_model("claude", "p")

    def unreachable(*_a, **_k):
        raise httpx.ConnectError("https://x.test/v1?key=SECRET-KEY")

    monkeypatch.setattr(summarize, "_run_api", unreachable)
    with pytest.raises(summarize.ModelFailed) as exc:
        summarize.run_model("gemini", "p")
    assert "ConnectError" in str(exc.value)
    assert "SECRET-KEY" not in summarize.failure_reason(exc.value)


def test_failure_reason_is_one_bounded_stable_line() -> None:
    long = summarize.ModelFailed("first line\n" + "x " * 600 + "\nlast")
    reason = summarize.failure_reason(long)
    assert "\n" not in reason and len(reason) <= 400
    assert reason == summarize.failure_reason(long)
    assert summarize.failure_reason(RuntimeError("boom")) == "RuntimeError: boom"


def test_failure_reason_never_quotes_a_transport_error() -> None:
    import httpx

    reason = summarize.failure_reason(httpx.ConnectError("https://x.test/?key=SECRET-KEY"))
    assert reason == "network error: ConnectError"
    # A subprocess error that reaches a call site without the runner quotes argv.
    leaked = subprocess.TimeoutExpired(cmd=["grok", "-p", "SECRET TRANSCRIPT"], timeout=1)
    assert summarize.failure_reason(leaked) == "TimeoutExpired"


def test_provider_failures_and_the_hierarchy() -> None:
    too_large = summarize.PromptTooLarge("prompt too large")
    assert isinstance(too_large, summarize.ModelFailed)
    assert isinstance(summarize.ModelFailed("x"), summarize.ModelCallError)
    assert isinstance(summarize.ModelNotInstalled("x"), summarize.ModelCallError)
    assert summarize.is_provider_failure(summarize.ModelFailed("quota"))
    assert summarize.is_provider_failure(summarize.ModelNotInstalled("gone"))
    assert not summarize.is_provider_failure(too_large)
    assert not summarize.is_provider_failure(RuntimeError("other"))


def test_breaker_keeps_the_first_reason() -> None:
    breaker = summarize.ProviderBreaker()
    assert not breaker.tripped
    breaker.trip("usage limit")
    breaker.trip("later")
    assert (breaker.tripped, breaker.reason) == (True, "usage limit")
