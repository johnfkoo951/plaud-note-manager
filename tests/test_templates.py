"""The template front-matter contract.

The app's TemplateMeta reader parses the same files with the same rules, so
these cases are the shared contract: first `---` block, column-0 `key: value`
lines, first occurrence wins, one pair of quotes or a trailing ` #comment`
stripped, and an unknown or missing `kind` loads as `summary`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import core.paths as paths_mod
import core.templates as templates_mod
from core.templates import (
    KIND_INTEGRATED,
    KIND_INTERNAL,
    KIND_SUMMARY,
    TemplateKindError,
    load_template,
    normalize_kind,
    parse_frontmatter,
    require_kind,
    save_template,
    validate_kind,
)

BODY = "\n본문 {transcript}\n"


@pytest.fixture()
def tdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "templates"
    d.mkdir()
    monkeypatch.setattr(paths_mod, "TEMPLATES_DIR", d)
    monkeypatch.setattr(templates_mod, "TEMPLATES_DIR", d)
    return d


def _write(d: Path, name: str, front: str, body: str = BODY) -> Path:
    path = d / f"{name}.md"
    path.write_bytes(f"---\n{front}\n---\n{body}".encode())
    return path


# ----- reading ------------------------------------------------------------


def test_missing_kind_loads_as_summary(tdir: Path) -> None:
    _write(tdir, "t", "name: t\ndescription: d")
    assert load_template("t").kind == KIND_SUMMARY


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("summary", KIND_SUMMARY),
        ("Summarize", KIND_SUMMARY),
        ("INTEGRATED", KIND_INTEGRATED),
        ("integrate", KIND_INTEGRATED),
        ("Internal", KIND_INTERNAL),
    ],
)
def test_kind_aliases_and_case(tdir: Path, raw: str, expected: str) -> None:
    _write(tdir, "t", f"name: t\nkind: {raw}")
    assert load_template("t").kind == expected


@pytest.mark.parametrize(
    "line",
    ['kind: "integrated"', "kind: 'Integrate'", "kind: integrated # fused", "kind:\tintegrated"],
)
def test_quoted_and_commented_kind_values(tdir: Path, line: str) -> None:
    _write(tdir, "t", f"name: t\n{line}")
    assert load_template("t").kind == KIND_INTEGRATED


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('description: "a # b"', "a # b"),
        ("description: 'quoted' # note", "quoted"),
        ("description: plain # note", "plain"),
        ("description: C# tips", "C# tips"),
        ('description: "unclosed', '"unclosed'),
        ("description: # only a comment", ""),
    ],
)
def test_description_quotes_and_comments(tdir: Path, line: str, expected: str) -> None:
    _write(tdir, "t", f"name: t\n{line}")
    assert load_template("t").description == expected


def test_first_occurrence_wins_and_non_column_zero_lines_are_ignored(tdir: Path) -> None:
    _write(tdir, "t", "#kind: integrated\n  kind: integrated\nkind: internal\nkind: integrated")
    assert load_template("t").kind == KIND_INTERNAL


def test_bad_kind_does_not_break_internal_loads(tdir: Path) -> None:
    _write(tdir, "t", "name: t\nkind: banana")
    t = load_template("t")  # loading never raises on a bad kind
    assert t.kind == KIND_SUMMARY
    require_kind(t, KIND_SUMMARY)
    with pytest.raises(TemplateKindError):
        require_kind(t, KIND_INTEGRATED)


def test_body_is_returned_exactly(tdir: Path) -> None:
    body = "\n[[notes]]\tcol   trailing  \n" + "x" * 120 + "\n"
    _write(tdir, "t", "name: t", body=body)
    assert load_template("t").body == body


def test_crlf_and_cr_read_as_lf_same_as_the_app(tdir: Path) -> None:
    # Python's universal newlines; the app's reader normalises the same way.
    raw = "---\r\nname: t\r\nkind: integrated\r\n---\r\nbody\r\nmore\rend"
    assert parse_frontmatter(raw) == (
        ["name: t", "kind: integrated"],
        {"name": "t", "kind": "integrated"},
        "body\nmore\nend",
    )
    (tdir / "t.md").write_bytes(raw.encode())
    t = load_template("t")
    assert (t.kind, t.body) == (KIND_INTEGRATED, "body\nmore\nend")


def test_file_without_front_matter_is_all_body(tdir: Path) -> None:
    (tdir / "t.md").write_text("just a prompt {transcript}\n", encoding="utf-8")
    t = load_template("t")
    assert (t.description, t.kind, t.body) == ("", KIND_SUMMARY, "just a prompt {transcript}\n")


def test_deprecated_and_replaced_by(tdir: Path) -> None:
    _write(tdir, "old", "name: old\ndeprecated: Yes\nreplaced_by: note-meeting")
    _write(tdir, "cur", "name: cur\ndeprecated: false")
    old, cur = load_template("old"), load_template("cur")
    assert (old.deprecated, old.replaced_by) == (True, "note-meeting")
    assert (cur.deprecated, cur.replaced_by) == (False, "")


def test_as_dict_shape(tdir: Path) -> None:
    path = _write(tdir, "t", "name: t\ndescription: d\nkind: integrated")
    assert load_template("t").as_dict() == {
        "name": "t",
        "description": "d",
        "kind": "integrated",
        "deprecated": False,
        "replaced_by": "",
        "path": str(path),
    }


# ----- kinds --------------------------------------------------------------


def test_normalize_kind_never_raises() -> None:
    assert normalize_kind(None) == KIND_SUMMARY
    assert normalize_kind("") == KIND_SUMMARY
    assert normalize_kind("banana") == KIND_SUMMARY


def test_validate_kind_is_strict() -> None:
    assert validate_kind("Integrate") == KIND_INTEGRATED
    with pytest.raises(ValueError, match="summary, integrated, internal"):
        validate_kind("banana")


def test_require_kind_says_what_to_run_instead(tdir: Path) -> None:
    _write(tdir, "fused", "name: fused\nkind: integrated")
    _write(tdir, "notes", "name: notes\nkind: summary")
    _write(tdir, "plumbing", "name: plumbing\nkind: internal")

    with pytest.raises(TemplateKindError) as exc:
        require_kind(load_template("fused"), KIND_SUMMARY)
    assert "cmds-integrate <file_id> --template fused" in str(exc.value)

    with pytest.raises(TemplateKindError) as exc:
        require_kind(load_template("notes"), KIND_INTEGRATED)
    assert "cmds-summarize" in str(exc.value)
    assert "add `kind: integrated` to the front matter of templates/notes.md" in str(exc.value)

    for expected in (KIND_SUMMARY, KIND_INTEGRATED):
        with pytest.raises(TemplateKindError) as exc:
            require_kind(load_template("plumbing"), expected)
        assert "own pipeline" in str(exc.value) and "plaud templates" in str(exc.value)

    assert issubclass(TemplateKindError, ValueError)


# ----- saving -------------------------------------------------------------


def _front_lines(path: Path) -> list[str]:
    return parse_frontmatter(path.read_bytes().decode())[0]


def test_save_preserves_unknown_keys_in_order(tdir: Path) -> None:
    front = "name: t\ndescription: d\nowner: someone\nkind: integrated\nreplaced_by: x\nnotes: keep"
    path = _write(tdir, "t", front)
    save_template("t", "new body\n")
    assert _front_lines(path) == front.split("\n")
    assert load_template("t").body == "\nnew body\n"


def test_save_without_description_keeps_it(tdir: Path) -> None:
    _write(tdir, "t", "name: t\ndescription: keep me")
    save_template("t", "b")
    assert load_template("t").description == "keep me"


def test_save_sets_description_in_place_and_flattens_newlines(tdir: Path) -> None:
    path = _write(tdir, "t", "name: t\ndescription: old\nkind: summary")
    save_template("t", "b", description="line one\nline two")
    assert _front_lines(path) == ["name: t", "description: line one line two", "kind: summary"]


def test_save_quotes_a_description_the_reader_would_otherwise_cut(tdir: Path) -> None:
    _write(tdir, "t", "name: t")
    save_template("t", "b", description="Meeting #2 notes")
    assert load_template("t").description == "Meeting #2 notes"
    save_template("t", "b", description='"quoted" start')
    assert load_template("t").description == '"quoted" start'


def test_save_sets_kind_in_place(tdir: Path) -> None:
    path = _write(tdir, "t", "name: t\nkind: summary\ndescription: d")
    save_template("t", "b", kind="integrated")
    assert _front_lines(path) == ["name: t", "kind: integrated", "description: d"]


def test_save_appends_kind_when_absent(tdir: Path) -> None:
    path = _write(tdir, "t", "name: t\ndescription: d")
    save_template("t", "b", kind="Integrate")
    assert _front_lines(path) == ["name: t", "description: d", "kind: integrated"]


def test_save_round_trip_is_byte_stable(tdir: Path) -> None:
    path = _write(tdir, "t", "name: t\ndescription: d\nkind: integrated\nowner: x")
    original = path.read_bytes()
    for _ in range(2):
        save_template("t", load_template("t").body)
        assert path.read_bytes() == original


def test_save_rejects_an_unknown_kind_and_leaves_the_file_unchanged(tdir: Path) -> None:
    path = _write(tdir, "t", "name: t\ndescription: d")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        save_template("t", "replacement", kind="banana")
    assert path.read_bytes() == before


def test_save_creates_front_matter_for_new_and_blockless_files(tdir: Path) -> None:
    fresh = save_template("fresh", "body {transcript}\n", description="hi", kind="summary")
    assert fresh.read_text(encoding="utf-8") == (
        "---\nname: fresh\ndescription: hi\nkind: summary\n---\n\nbody {transcript}\n"
    )
    (tdir / "plain.md").write_text("old body\n", encoding="utf-8")
    save_template("plain", "new body\n")
    assert (tdir / "plain.md").read_text(encoding="utf-8") == (
        "---\nname: plain\ndescription:\n---\n\nnew body\n"
    )


def test_save_sets_name_in_place(tdir: Path) -> None:
    path = _write(tdir, "renamed", "description: d\nname: stale")
    save_template("renamed", "b")
    assert _front_lines(path) == ["description: d", "name: renamed"]
