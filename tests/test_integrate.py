import json
from pathlib import Path

import pytest

import core.integrate as integrate_mod
import core.paths as paths_mod
import core.templates as templates_mod
from core.dual_pipeline import DUAL_TEMPLATE
from core.integrate import integrate, split_integrated
from core.templates import TemplateKindError

INPUTS = {"cmds_transcript": "a", "plaud_transcript": "b", "plaud_summaries": "c"}
FUSED = "===FINAL_TRANSCRIPT_BEGIN===\nt\n===FINAL_TRANSCRIPT_END===\n===SUMMARY_BEGIN===\ns\n===SUMMARY_END==="


@pytest.fixture()
def calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list:
    """Integrated output under tmp; integrate's own run_model import records."""
    monkeypatch.setattr(paths_mod, "INTEGRATED_DIR", tmp_path / "integrated")
    monkeypatch.setattr(paths_mod, "_override", lambda kind: None)
    seen: list = []
    monkeypatch.setattr(integrate_mod, "run_model", lambda *a, **k: seen.append(a) or FUSED)
    return seen


def test_integrate_refuses_a_summary_template_before_the_model(
    calls, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "notes.md").write_text(
        "---\nname: notes\nkind: summary\n---\n\n{transcript}\n", encoding="utf-8"
    )
    monkeypatch.setattr(paths_mod, "TEMPLATES_DIR", tdir)
    monkeypatch.setattr(templates_mod, "TEMPLATES_DIR", tdir)
    with pytest.raises(TemplateKindError, match="cmds-summarize"):
        integrate(file_id="f1", model="claude", template_name="notes", **INPUTS)
    assert calls == []
    assert not (tmp_path / "integrated").exists()


def test_integrate_accepts_the_dual_template(calls, tmp_path: Path) -> None:
    # The shipped template the dual pipeline runs must declare itself integrated;
    # turning the check on without that line would stop every dual run.
    paths = integrate(file_id="f1", model="claude", template_name=DUAL_TEMPLATE, **INPUTS)
    assert len(calls) == 1
    assert paths["summary"].read_text(encoding="utf-8").endswith("s")


def test_failure_writes_error_sidecar_and_reraises(
    calls, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.summarize import ModelFailed

    def quota(*_a, **_k):
        raise ModelFailed("claude CLI failed (exit 1): ERROR: usage limit\nreset at 5pm")

    monkeypatch.setattr(integrate_mod, "run_model", quota)
    with pytest.raises(ModelFailed, match="usage limit"):
        integrate(
            file_id="f1",
            model="claude",
            template_name=DUAL_TEMPLATE,
            model_id="claude-x",
            **INPUTS,
        )
    sidecar = paths_mod.integrated_error_path("f1", model="claude-x", template=DUAL_TEMPLATE)
    outputs = paths_mod.integrated_paths("f1", model="claude-x", template=DUAL_TEMPLATE)
    # Same stem as the slot's outputs, so the app finds it beside them.
    assert sidecar.name == outputs["all"].name.removesuffix(".md") + ".error.json"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["reason"] == "claude CLI failed (exit 1): ERROR: usage limit reset at 5pm"
    assert (data["provider"], data["model"], data["template"]) == (
        "claude",
        "claude-x",
        DUAL_TEMPLATE,
    )
    assert isinstance(data["at"], int)
    assert list(sidecar.parent.glob("*.md")) == []


SENTINEL = "SYNTHETIC_PRIVATE_TRANSCRIPT_41"
KEY = "SYNTHETIC_KEY_42"


@pytest.fixture()
def real_runner(calls, monkeypatch: pytest.MonkeyPatch):
    """integrate through the real run_model, against a fake CLI or HTTP reply."""
    import core.summarize as sm

    monkeypatch.setattr(integrate_mod, "run_model", sm.run_model)
    monkeypatch.setattr(sm, "_resolve_binary", lambda *a: "/fake/bin")
    monkeypatch.setattr(sm.app_config, "model_id_for", lambda m: "")
    return sm


def _sidecar_reason(file_id: str, model: str) -> str:
    path = paths_mod.integrated_error_path(file_id, model=model, template=DUAL_TEMPLATE)
    return json.loads(path.read_text(encoding="utf-8"))["reason"]


@pytest.mark.parametrize(
    "stderr",
    [
        f"ERROR: rejected input: {SENTINEL}",
        f"ERROR: bad request\n  near: {SENTINEL}\nERROR: {SENTINEL} (cut)",
        f"ERROR: You've hit your usage limit. Input was: {SENTINEL}",
    ],
)
def test_the_sidecar_never_keeps_an_echo_of_the_prompt(
    real_runner, monkeypatch: pytest.MonkeyPatch, stderr: str
) -> None:
    import subprocess

    sm = real_runner
    monkeypatch.setattr(sm.app_config, "backend_for", lambda m: "cli")
    fake = subprocess.CompletedProcess(["codex"], 1, "", stderr)
    monkeypatch.setattr(sm.subprocess, "run", lambda *a, **k: fake)
    with pytest.raises(sm.ModelFailed):
        integrate(
            file_id="f1",
            model="codex",
            template_name=DUAL_TEMPLATE,
            **{**INPUTS, "cmds_transcript": SENTINEL},
        )
    reason = _sidecar_reason("f1", "codex")
    assert "SYNTHETIC" not in reason
    assert reason.startswith("codex CLI failed (exit 1): ")
    if "usage limit" in stderr:
        assert reason.endswith(sm.REASON_QUOTA)  # the useful part survives


def test_the_sidecar_never_keeps_an_api_error_body(
    real_runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    import types

    sm = real_runner
    monkeypatch.setattr(sm.app_config, "backend_for", lambda m: "api")
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    body = '{"error": "invalid request: ' + SENTINEL + "; key=" + KEY + '"}'
    reply = types.SimpleNamespace(status_code=400, text=body)
    monkeypatch.setattr(sm.httpx, "post", lambda *a, **k: reply)
    with pytest.raises(sm.ModelFailed):
        integrate(
            file_id="f1",
            model="gemini",
            model_id="m-x",
            template_name=DUAL_TEMPLATE,
            **{**INPUTS, "cmds_transcript": SENTINEL},
        )
    reason = _sidecar_reason("f1", "m-x")
    assert SENTINEL not in reason and KEY not in reason
    assert reason == f"Gemini API 400: {sm.REASON_REJECTED}"


def test_success_removes_a_stale_error_sidecar(calls, tmp_path: Path) -> None:
    sidecar = paths_mod.integrated_error_path("f1", model="claude", template=DUAL_TEMPLATE)
    sidecar.write_text('{"reason": "old"}', encoding="utf-8")
    paths = integrate(file_id="f1", model="claude", template_name=DUAL_TEMPLATE, **INPUTS)
    assert not sidecar.exists()
    assert paths["summary"].is_file()


def test_split_integrated_explicit_markers() -> None:
    raw = """
prefix ignored
===FINAL_TRANSCRIPT_BEGIN===
[00:00] A: hello
===FINAL_TRANSCRIPT_END===

===SUMMARY_BEGIN===
## Summary
done
===SUMMARY_END===
suffix ignored
"""

    transcript, summary = split_integrated(raw)

    assert transcript == "[00:00] A: hello"
    assert summary == "## Summary\ndone"


def test_split_integrated_legacy_marker() -> None:
    raw = "## Summary\nlegacy\n\n===TRANSCRIPT===\n# Transcript\nbody"

    transcript, summary = split_integrated(raw)

    assert transcript == "# Transcript\nbody"
    assert summary == "## Summary\nlegacy"


def test_split_integrated_missing_markers_keeps_raw_as_summary() -> None:
    transcript, summary = split_integrated("plain model output")

    assert transcript == ""
    assert summary == "plain model output"
