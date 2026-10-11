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


def test_cli_reason_keeps_the_error_that_a_long_banner_used_to_truncate_away() -> None:
    # The banner alone is over 400 characters, so taking the head of stderr cut
    # the actual cause off the end.
    assert len(CODEX_BANNER) > 400
    reason = summarize._cli_reason(_Proc(stderr=CODEX_BANNER))
    assert "usage limit" in reason
    assert "OpenAI Codex v0.153.4" not in reason


def test_cli_reason_falls_back_to_the_tail_when_nothing_looks_like_an_error() -> None:
    reason = summarize._cli_reason(_Proc(stderr="line one\nline two\nline three\nline four"))
    assert "line four" in reason
    assert "line one" not in reason


def test_cli_reason_reads_stdout_when_stderr_is_empty() -> None:
    assert "boom" in summarize._cli_reason(_Proc(stdout="boom"))


def test_cli_reason_deduplicates_a_line_written_to_both_streams() -> None:
    dup = "ERROR: quota exceeded\nERROR: quota exceeded\nERROR: quota exceeded"
    assert summarize._cli_reason(_Proc(stderr=dup)) == "ERROR: quota exceeded"


def test_cli_reason_handles_a_silent_failure() -> None:
    assert summarize._cli_reason(_Proc()) == "(no output)"


def test_cli_reason_is_bounded() -> None:
    assert len(summarize._cli_reason(_Proc(stderr="error: " + "x" * 5000))) <= 400
