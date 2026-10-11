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
    deprecation_notice,
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


def test_deprecation_notice(tdir: Path) -> None:
    _write(tdir, "old", "name: old\ndeprecated: true\nreplaced_by: new")
    _write(tdir, "bare", "name: bare\ndeprecated: yes")
    _write(tdir, "live", "name: live")
    assert deprecation_notice(load_template("old")) == "template 'old' is deprecated — use 'new'"
    assert deprecation_notice(load_template("bare")) == "template 'bare' is deprecated"
    assert deprecation_notice(load_template("live")) == ""


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


# ----- parity with the app's reader -----------------------------------------
# The parse cases of app/Tests/PlaudNoteAppTests/TemplateMetaTests.swift,
# replayed through this reader. Both sides must agree on every one.


def _read(tdir: Path, raw: str, name: str = "sample") -> dict:
    (tdir / f"{name}.md").write_bytes(raw.encode())
    t = load_template(name)
    return {
        "name": t.name,
        "description": t.description,
        "kind": t.kind,
        "deprecated": t.deprecated,
        "replaced_by": t.replaced_by,
        "body": t.body,
    }


@pytest.mark.parametrize(
    ("front", "expected"),
    [
        ('kind: "integrated"', {"kind": KIND_INTEGRATED}),
        ("kind: integrate", {"kind": KIND_INTEGRATED}),
        ("kind: 'Integrated'  # merged output", {"kind": KIND_INTEGRATED}),
        ("kind: summarize", {"kind": KIND_SUMMARY}),
        ("kind: summary", {"kind": KIND_SUMMARY}),
        ("kind: internal", {"kind": KIND_INTERNAL}),
        ("kind: transcript", {"kind": KIND_SUMMARY}),
        ("kind:", {"kind": KIND_SUMMARY}),
        ("name: x\ndescription: d", {"kind": KIND_SUMMARY, "deprecated": False, "replaced_by": ""}),
        (
            "deprecated: yes\nreplaced_by: note-meeting",
            {"deprecated": True, "replaced_by": "note-meeting"},
        ),
        ("deprecated: TRUE", {"deprecated": True}),
        ("deprecated: 1", {"deprecated": True}),
        ("deprecated: no", {"deprecated": False}),
        ("deprecated: maybe", {"deprecated": False}),
        (
            "description: first\nkind: internal\ndescription: second\nkind: summary",
            {"description": "first", "kind": KIND_INTERNAL},
        ),
        ("description: Weekly sync # owner note", {"description": "Weekly sync"}),
        ('description: "Use # for headings"', {"description": "Use # for headings"}),
        ("description: 'single quoted'", {"description": "single quoted"}),
        ("description: C# notes", {"description": "C# notes"}),
        ('description: "unbalanced', {"description": '"unbalanced'}),
        ('description: "a" and "b"', {"description": '"a" and "b"'}),
        (
            "tags:\n  description: nested\n# description: commented\nkind: internal",
            {"description": "", "kind": KIND_INTERNAL},
        ),
    ],
)
def test_app_reader_cases(tdir: Path, front: str, expected: dict) -> None:
    got = _read(tdir, f"---\n{front}\n---\n\nBody\n")
    assert {k: got[k] for k in expected} == expected
    assert got["body"] == "\nBody\n"


@pytest.mark.parametrize(
    "front",
    [
        'kind: " integrated "',
        "kind: ' internal '",
        'kind: " integrate "',
        "kind: ' Integrated'",
        'kind: "internal "',
    ],
)
def test_padded_quoted_kind_is_not_trimmed_again(tdir: Path, front: str) -> None:
    # The app switches on the cleaned value as it is (TemplateMeta.kind(from:)),
    # so quoted padding is an unknown kind there. Trimming it again here would
    # make the backend refuse what the app offers as a summary template.
    assert _read(tdir, f"---\n{front}\n---\n\nBody\n")["kind"] == KIND_SUMMARY


def test_app_reader_every_key_and_file_name_identity(tdir: Path) -> None:
    front = (
        "name: something-else\n"
        "description: Meeting minutes — decisions and action items.\n"
        "kind: integrated\ndeprecated: false\nreplaced_by: note-meeting"
    )
    assert _read(tdir, f"---\n{front}\n---\n\nBody\n", name="note-sample") == {
        "name": "note-sample",
        "description": "Meeting minutes — decisions and action items.",
        "kind": KIND_INTEGRATED,
        "deprecated": False,
        "replaced_by": "note-meeting",
        "body": "\nBody\n",
    }


@pytest.mark.parametrize(
    "raw", ["Summarise this:\n{transcript}\n", "---\ndescription: never closed\n\nBody\n"]
)
def test_app_reader_without_a_closed_block_keeps_the_whole_file(tdir: Path, raw: str) -> None:
    got = _read(tdir, raw)
    assert (got["description"], got["kind"], got["body"]) == ("", KIND_SUMMARY, raw)


def test_app_reader_crlf_and_cr_twins(tdir: Path) -> None:
    lf = (
        "---\n"
        'description: "Meeting minutes" # shipped\n'
        "kind: integrate\ndeprecated: yes\nreplaced_by: note-meeting\n"
        "---\n\n"
        "관련 노트: [[Weekly Review]]\n\t- tab-indented line {transcript}\n---\nPart 2\n"
    )
    expected = _read(tdir, lf)
    assert (expected["description"], expected["kind"]) == ("Meeting minutes", KIND_INTEGRATED)
    assert (expected["deprecated"], expected["replaced_by"]) == (True, "note-meeting")
    assert "\n---\nPart 2" in expected["body"]  # a later --- line belongs to the body
    assert _read(tdir, lf.replace("\n", "\r\n")) == expected
    assert _read(tdir, lf.replace("\n", "\r")) == expected
    assert _read(tdir, "a\r\nb\rc\n", name="plain")["body"] == "a\nb\nc\n"


# ----- kinds --------------------------------------------------------------


def test_normalize_kind_never_raises() -> None:
    assert normalize_kind(None) == KIND_SUMMARY
    assert normalize_kind("") == KIND_SUMMARY
    assert normalize_kind("banana") == KIND_SUMMARY
    # The read path does not trim: an already-cleaned value is matched as is.
    assert normalize_kind(" integrated ") == KIND_SUMMARY


def test_validate_kind_is_strict() -> None:
    assert validate_kind("Integrate") == KIND_INTEGRATED
    # Write-time input (e.g. `--kind " integrated "`) is trimmed before matching.
    assert validate_kind(" Integrated ") == KIND_INTEGRATED
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


@pytest.mark.parametrize(
    "description",
    [
        "Meeting #2 notes",
        '"quoted" start',
        "'single' lead",
        "Bob's plan #2",
        'say "hi" #2',
        " padded ",
        "#hashtag first",
        "tab\t#x",
        "C# tips",
        'a "b" c',
        "it's",
    ],
)
def test_save_writes_a_description_the_reader_returns_unchanged(
    tdir: Path, description: str
) -> None:
    _write(tdir, "t", "name: t")
    save_template("t", "b", description=description)
    assert load_template("t").description == description


@pytest.mark.parametrize("existing", [True, False])
def test_save_refuses_a_description_the_reader_cannot_recover(tdir: Path, existing: bool) -> None:
    # Needs quotes (the ` #` would be read as a comment) but contains both kinds,
    # so no form the shared reader decodes gives it back. Refuse, write nothing.
    path = tdir / "t.md"
    if existing:
        _write(tdir, "t", "name: t\ndescription: before")
    before = path.read_bytes() if existing else None
    with pytest.raises(ValueError, match="both"):
        save_template("t", "new body", description='Bob\'s "Plan" #2')
    assert (path.read_bytes() if path.exists() else None) == before


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
