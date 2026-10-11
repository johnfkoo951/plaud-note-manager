"""`plaud templates` / `template-show` / `template-save` and the template gates
in front of `cmds-summarize`, `cmds-integrate` and `slot-add`."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import cli.main as cli
import core.paths as paths_mod
import core.slots as slots_mod
import core.summarize as summarize_mod
import core.templates as templates_mod

runner = CliRunner()

SHOW_BODY = "\n[[notes]] {transcript}\n\tindented with a tab\n" + "y" * 120 + "\n"
LONG = "Fused Plaud and CMDS transcripts into one final record " + "x" * 60


@pytest.fixture()
def tdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "templates"
    d.mkdir()
    monkeypatch.setattr(paths_mod, "TEMPLATES_DIR", d)
    monkeypatch.setattr(templates_mod, "TEMPLATES_DIR", d)
    # Nothing a refused command might have written may land in the real data/.
    data = tmp_path / "data"
    monkeypatch.setattr(paths_mod, "DATA_DIR", data)
    monkeypatch.setattr(paths_mod, "TRANSCRIPTS_DIR", data / "transcripts")
    monkeypatch.setattr(paths_mod, "SUMMARIES_DIR", data / "summaries")
    monkeypatch.setattr(paths_mod, "INTEGRATED_DIR", data / "integrated")
    monkeypatch.setattr(paths_mod, "_override", lambda kind: None)
    monkeypatch.setattr(slots_mod, "SLOTS_FILE", data / "slots.json")

    def write(name: str, front: str, body: str = "\n{transcript}\n") -> None:
        (d / f"{name}.md").write_text(f"---\nname: {name}\n{front}\n---\n{body}", encoding="utf-8")

    write("default", f"description: {LONG}\nkind: integrated", "\n{cmds_transcript}\n")
    write("note-general", "description: [bold]x[/bold]\nkind: summary", SHOW_BODY)
    write("old", "description: legacy\nkind: summary\ndeprecated: true\nreplaced_by: note-general")
    write("classify", "description: folder arbiter\nkind: internal")
    return d


@pytest.fixture()
def summarize_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Shipped templates; data paths, Storage and the model all local to the test."""
    from core.models import FileContent, PlaudFile, TranscriptSegment
    from core.storage import Storage

    data = tmp_path / "data"
    monkeypatch.setattr(paths_mod, "DATA_DIR", data)
    monkeypatch.setattr(paths_mod, "TRANSCRIPTS_DIR", data / "transcripts")
    monkeypatch.setattr(paths_mod, "SUMMARIES_DIR", data / "summaries")
    monkeypatch.setattr(paths_mod, "INTEGRATED_DIR", data / "integrated")
    monkeypatch.setattr(paths_mod, "_override", lambda kind: None)
    monkeypatch.setattr(slots_mod, "SLOTS_FILE", data / "slots.json")
    storage = Storage(tmp_path / "plaud.db")
    storage.upsert_file(PlaudFile(id="f1", filename="회의"), now=1)
    storage.save_content(
        FileContent(
            file_id="f1",
            title="회의",
            transcript=[
                TranscriptSegment(
                    start_time=0, end_time=1, speaker="구요한", content="SENTINEL-전사"
                )
            ],
        ),
        now=1,
    )
    monkeypatch.setattr(cli, "Storage", lambda: storage)
    prompts: list[str] = []
    monkeypatch.setattr(summarize_mod, "model_available", lambda m: True)
    monkeypatch.setattr(
        summarize_mod, "run_model", lambda model, prompt, **k: prompts.append(prompt) or "결과"
    )
    return {"data": data, "prompts": prompts}


@pytest.fixture()
def nothing_downstream(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal must come before the model check and the database."""

    def boom(*_a, **_k):
        raise AssertionError("reached past the template check")

    monkeypatch.setattr(cli, "Storage", boom)
    monkeypatch.setattr(summarize_mod, "model_available", boom)


# ----- templates ----------------------------------------------------------


def test_templates_json_shape_and_kinds(tdir: Path) -> None:
    result = runner.invoke(cli.app, ["templates", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [d["name"] for d in data] == ["classify", "default", "note-general", "old"]
    for d in data:
        assert set(d) == {"name", "description", "kind", "deprecated", "replaced_by", "path"}
    assert {d["name"]: d["kind"] for d in data} == {
        "classify": "internal",
        "default": "integrated",
        "note-general": "summary",
        "old": "summary",
    }
    old = next(d for d in data if d["name"] == "old")
    assert (old["deprecated"], old["replaced_by"]) == (True, "note-general")


def test_templates_text_escapes_markup_marks_deprecation_and_does_not_wrap(tdir: Path) -> None:
    result = runner.invoke(cli.app, ["templates"])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert any("note-general" in ln and "[bold]x[/bold]" in ln for ln in lines)
    old = next(ln for ln in lines if "old" in ln and "legacy" in ln)
    assert old.index("DEPRECATED -> note-general") < old.index("legacy")
    assert any(LONG in ln for ln in lines)  # one line, not wrapped at 80 columns
    assert any("default" in ln and "integrated" in ln for ln in lines)


def test_templates_marks_the_shipped_legacy_templates() -> None:
    result = runner.invoke(cli.app, ["templates"])
    assert result.exit_code == 0, result.output
    line = next(ln for ln in result.stdout.splitlines() if "cmds-meeting" in ln)
    description = templates_mod.load_template("cmds-meeting").description
    # `-> note-meeting` only comes from the marker, which sits before the description.
    assert line.index("DEPRECATED -> note-meeting") < line.index(description)
    assert line.count("DEPRECATED") == 1

    data = json.loads(runner.invoke(cli.app, ["templates", "--json"]).stdout)
    assert {d["name"]: d["replaced_by"] for d in data if d["deprecated"]} == {
        "cmds-coaching": "note-consulting",
        "cmds-lecture": "note-lecture",
        "cmds-meeting": "note-meeting",
    }


# ----- template-show ------------------------------------------------------


def test_template_show_prints_the_stored_body_verbatim(tdir: Path) -> None:
    result = runner.invoke(cli.app, ["template-show", "note-general"])
    assert result.exit_code == 0, result.output
    assert result.stdout == SHOW_BODY


def test_template_show_json_carries_the_exact_body(tdir: Path) -> None:
    result = runner.invoke(cli.app, ["template-show", "note-general", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["body"] == SHOW_BODY
    assert (data["name"], data["kind"], data["description"]) == (
        "note-general",
        "summary",
        "[bold]x[/bold]",
    )


@pytest.mark.parametrize("extra", [[], ["--json"]])
def test_template_show_unknown_name_exits_1_without_a_traceback(tdir: Path, extra) -> None:
    result = runner.invoke(cli.app, ["template-show", "nope", *extra])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "template not found: nope" in result.stderr
    assert result.stdout == ""


# ----- template-save ------------------------------------------------------


def test_template_save_keeps_kind_and_description(tdir: Path, tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("new {cmds_transcript}\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["template-save", "default", "--body-file", str(body)])
    assert result.exit_code == 0, result.output
    t = templates_mod.load_template("default")
    assert (t.kind, t.description, t.body) == ("integrated", LONG, "\nnew {cmds_transcript}\n")

    result = runner.invoke(
        cli.app, ["template-save", "default", "--body-file", str(body), "--kind", "summarize"]
    )
    assert result.exit_code == 0, result.output
    assert templates_mod.load_template("default").kind == "summary"


def test_template_save_rejects_an_unknown_kind(tdir: Path, tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("replacement\n", encoding="utf-8")
    before = (tdir / "default.md").read_bytes()
    result = runner.invoke(
        cli.app, ["template-save", "default", "--body-file", str(body), "--kind", "banana"]
    )
    assert result.exit_code == 1
    assert "summary, integrated, internal" in result.output
    assert (tdir / "default.md").read_bytes() == before


def test_template_save_refuses_a_description_it_cannot_store_faithfully(
    tdir: Path, tmp_path: Path
) -> None:
    body = tmp_path / "body.md"
    body.write_text("replacement\n", encoding="utf-8")
    before = (tdir / "default.md").read_bytes()
    result = runner.invoke(
        cli.app,
        ["template-save", "default", "--body-file", str(body), "--description", 'Bob\'s "Plan" #2'],
    )
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)  # a message, not a traceback
    assert "both" in result.output
    assert (tdir / "default.md").read_bytes() == before


# ----- pipelines refuse before doing anything -------------------------------


def test_cmds_summarize_refuses_an_integrated_template_first(tdir, nothing_downstream) -> None:
    result = runner.invoke(cli.app, ["cmds-summarize", "f1", "--template", "default"])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "cmds-integrate" in result.stderr
    assert not paths_mod.TRANSCRIPTS_DIR.exists()  # no context.txt written


def test_cmds_integrate_refuses_a_summary_template_first(tdir, nothing_downstream) -> None:
    result = runner.invoke(cli.app, ["cmds-integrate", "f1", "--template", "note-general"])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "cmds-summarize" in result.stderr


def test_cmds_summarize_warns_on_a_deprecated_template_and_still_runs(summarize_env) -> None:
    result = runner.invoke(cli.app, ["cmds-summarize", "f1", "--template", "cmds-meeting"])
    assert result.exit_code == 0, result.output
    assert "template 'cmds-meeting' is deprecated — use 'note-meeting'" in result.stdout
    assert len(summarize_env["prompts"]) == 1
    assert "SENTINEL-전사" in summarize_env["prompts"][0]
    assert (summarize_env["data"] / "summaries/f1/claude__cmds-meeting.md").is_file()


def test_cmds_summarize_defaults_to_note_general(summarize_env) -> None:
    # The old default, `default`, is an integrate prompt with no {transcript}:
    # run through summarize it sent the model no transcript at all.
    result = runner.invoke(cli.app, ["cmds-summarize", "f1"])
    assert result.exit_code == 0, result.output
    assert len(summarize_env["prompts"]) == 1
    assert "SENTINEL-전사" in summarize_env["prompts"][0]
    assert (summarize_env["data"] / "summaries/f1/claude__note-general.md").is_file()
    assert "deprecated" not in result.stdout

    option = inspect.signature(cli.cmds_summarize).parameters["template"].default
    assert option.default == "note-general"
    assert "cmds-integrate" in option.help


@pytest.mark.parametrize("command", ["cmds-summarize", "cmds-integrate"])
def test_pipelines_report_a_missing_template_first(tdir, nothing_downstream, command) -> None:
    result = runner.invoke(cli.app, [command, "f1", "--template", "nope"])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "template not found: nope" in result.stderr


# ----- slot-add -----------------------------------------------------------


@pytest.mark.parametrize(("template", "message"), [("classify", "internal"), ("nope", "not found")])
def test_slot_add_rejects_internal_and_missing_templates(tdir: Path, template, message) -> None:
    result = runner.invoke(cli.app, ["slot-add", "s", "claude", template])
    assert result.exit_code == 1
    assert message in result.stderr
    assert not slots_mod.SLOTS_FILE.exists()


def test_slot_add_warns_on_a_deprecated_template_and_saves(summarize_env) -> None:
    result = runner.invoke(cli.app, ["slot-add", "legacy", "claude", "cmds-lecture"])
    assert result.exit_code == 0, result.output
    assert "template 'cmds-lecture' is deprecated — use 'note-lecture'" in result.stdout
    saved = json.loads(slots_mod.SLOTS_FILE.read_text(encoding="utf-8"))
    assert saved[-1]["template"] == "cmds-lecture"


def test_slot_add_accepts_summary_and_integrated_templates(tdir: Path) -> None:
    for template in ("note-general", "default"):
        result = runner.invoke(cli.app, ["slot-add", template, "claude", template])
        assert result.exit_code == 0, result.output
    saved = json.loads(slots_mod.SLOTS_FILE.read_text(encoding="utf-8"))
    assert [s["template"] for s in saved][-2:] == ["note-general", "default"]
