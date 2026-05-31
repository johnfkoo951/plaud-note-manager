"""Tests for core.vault_index.

Two halves:

* Pure-function tests for the hand-rolled YAML frontmatter parser
  (`_parse_frontmatter`) and the `_read_note` wrapper that builds a
  `VaultNote` from a `.md` file on disk. These need no database.
* Database tests for `resolve_links`, which matches Plaud keywords against
  indexed `vault_notes` by title / alias / tag. These build a temporary
  SQLite DB and exercise the LIKE-escaping fix on the alias/tag path.
"""

import json
import time

import core.vault_index as vault_index
from core.vault_index import (
    VaultNote,
    _parse_frontmatter,
    _read_note,
    ensure_schema,
    resolve_links,
)


# --------------------------------------------------------------------------
# _parse_frontmatter — pure function, no DB
# --------------------------------------------------------------------------


def test_parse_frontmatter_empty_when_no_block() -> None:
    assert _parse_frontmatter("just body text, no frontmatter") == {}
    assert _parse_frontmatter("") == {}


def test_parse_frontmatter_empty_block_returns_empty_dict() -> None:
    raw = "---\n---\nbody\n"
    assert _parse_frontmatter(raw) == {}


def test_parse_frontmatter_scalar_values() -> None:
    raw = '---\ntype: meeting\ndescription: "A quoted desc"\n---\nbody\n'
    fm = _parse_frontmatter(raw)

    assert fm["type"] == "meeting"
    # surrounding quotes are stripped
    assert fm["description"] == "A quoted desc"


def test_parse_frontmatter_inline_list() -> None:
    raw = "---\ntags: [a, b, c]\n---\n"
    fm = _parse_frontmatter(raw)

    assert fm["tags"] == ["a", "b", "c"]


def test_parse_frontmatter_inline_list_strips_quotes_and_blanks() -> None:
    raw = "---\naliases: ['Foo', \"Bar\", ]\n---\n"
    fm = _parse_frontmatter(raw)

    # empty trailing element is dropped, quotes removed
    assert fm["aliases"] == ["Foo", "Bar"]


def test_parse_frontmatter_block_list() -> None:
    raw = "---\naliases:\n  - Foo\n  - Bar\n---\n"
    fm = _parse_frontmatter(raw)

    assert fm["aliases"] == ["Foo", "Bar"]


def test_parse_frontmatter_block_list_unwraps_wikilink_alias() -> None:
    raw = "---\naliases:\n  - [[Foo]]\n  - [[Bar|Baz]]\n  - Plain\n---\n"
    fm = _parse_frontmatter(raw)

    # [[Foo]] -> Foo, [[Bar|Baz]] -> Bar (display segment after | is dropped)
    assert fm["aliases"] == ["Foo", "Bar", "Plain"]


def test_parse_frontmatter_scalar_promoted_to_list_by_block_items() -> None:
    # A scalar value followed by indented list items must be promoted to a list
    # that includes the original scalar as the first element.
    raw = "---\naliases: First\n  - Second\n  - Third\n---\n"
    fm = _parse_frontmatter(raw)

    assert fm["aliases"] == ["First", "Second", "Third"]


def test_parse_frontmatter_empty_key_becomes_empty_list() -> None:
    raw = "---\ntags:\n---\n"
    fm = _parse_frontmatter(raw)

    assert fm["tags"] == []


# --------------------------------------------------------------------------
# _read_note — frontmatter -> VaultNote, with filtering
# --------------------------------------------------------------------------


def test_read_note_builds_vault_note_with_lists(tmp_path) -> None:
    note_path = tmp_path / "My Note.md"
    note_path.write_text(
        "---\n"
        "aliases:\n  - Alpha\n  - Beta\n"
        "tags:\n  - '#Project'\n  - idea\n"
        "type: meeting\n"
        "description: A note about things\n"
        "---\nbody\n",
        encoding="utf-8",
    )

    note = _read_note("MyVault", tmp_path, note_path)

    assert isinstance(note, VaultNote)
    assert note.vault == "MyVault"
    assert note.title == "My Note"  # filename stem
    assert note.rel_path == "My Note.md"
    assert note.aliases == ["Alpha", "Beta"]
    # '#'-tag stripping: leading '#' removed
    assert note.tags == ["Project", "idea"]
    assert note.note_type == "meeting"
    assert note.description == "A note about things"


def test_read_note_empty_frontmatter_yields_sensible_defaults(tmp_path) -> None:
    note_path = tmp_path / "Bare.md"
    note_path.write_text("no frontmatter here\n", encoding="utf-8")

    note = _read_note("V", tmp_path, note_path)

    assert isinstance(note, VaultNote)
    assert note.title == "Bare"
    assert note.aliases == []
    assert note.tags == []
    assert note.description is None
    assert note.note_type is None


def test_read_note_scalar_alias_and_tag_promoted_to_list(tmp_path) -> None:
    note_path = tmp_path / "Scalar.md"
    note_path.write_text(
        "---\naliases: SoleAlias\ntags: SoleTag\n---\n",
        encoding="utf-8",
    )

    note = _read_note("V", tmp_path, note_path)

    assert note.aliases == ["SoleAlias"]
    assert note.tags == ["SoleTag"]


def test_read_note_filters_non_string_description_and_type(tmp_path) -> None:
    # When `description`/`type` parse as lists (not scalars), they must be
    # filtered out to None rather than stored as a list.
    note_path = tmp_path / "Weird.md"
    note_path.write_text(
        "---\ndescription: [a, b]\ntype: [x, y]\n---\n",
        encoding="utf-8",
    )

    note = _read_note("V", tmp_path, note_path)

    assert note.description is None
    assert note.note_type is None


# --------------------------------------------------------------------------
# resolve_links — DB-backed keyword -> vault_note matching
# --------------------------------------------------------------------------


def _setup_db(tmp_path, monkeypatch):
    """Create an isolated SQLite DB with the vault_index schema and point the
    module's DEFAULT_DB at it. Returns an open connection (caller closes)."""
    import sqlite3

    db_path = tmp_path / "plaud.db"
    monkeypatch.setattr(vault_index, "DEFAULT_DB", db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _insert_note(conn, *, vault="V", path, title, aliases=None, tags=None) -> int:
    cur = conn.execute(
        """
        INSERT INTO vault_notes
            (vault, path, rel_path, title, aliases, tags, description, type, mtime, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
        """,
        (
            vault,
            path,
            path,
            title,
            json.dumps(aliases or [], ensure_ascii=False),
            json.dumps(tags or [], ensure_ascii=False),
            0.0,
            int(time.time()),
        ),
    )
    return int(cur.lastrowid)


def _insert_keyword_for_file(conn, file_id: str, term: str, source: str = "plaud") -> int:
    cur = conn.execute(
        "INSERT INTO keywords(term) VALUES (?) "
        "ON CONFLICT(term) DO UPDATE SET term = excluded.term RETURNING id",
        (term,),
    )
    kid = int(cur.fetchone()[0])
    conn.execute(
        "INSERT OR IGNORE INTO file_keywords(file_id, keyword_id, source) VALUES (?, ?, ?)",
        (file_id, kid, source),
    )
    return kid


def test_resolve_links_title_alias_tag_counts(tmp_path, monkeypatch) -> None:
    conn = _setup_db(tmp_path, monkeypatch)
    try:
        # One note matched by title, one by alias, one by tag.
        title_id = _insert_note(conn, path="/v/Obsidian.md", title="Obsidian")
        alias_id = _insert_note(conn, path="/v/Tool.md", title="Tool", aliases=["Obsidian"])
        tag_id = _insert_note(conn, path="/v/Notes.md", title="Notes", tags=["Obsidian"])
        conn.commit()

        _insert_keyword_for_file(conn, "file-1", "Obsidian")
        conn.commit()

        counts = resolve_links("file-1", conn=conn)

        assert counts == {"title": 1, "alias": 1, "tag": 1}

        rows = conn.execute(
            "SELECT vault_note_id, match_kind, confidence FROM vault_links "
            "WHERE file_id = 'file-1' ORDER BY match_kind"
        ).fetchall()
        by_kind = {r["match_kind"]: (r["vault_note_id"], r["confidence"]) for r in rows}

        # match_kind -> confidence mapping: title=1.0, alias=0.9, tag=0.7
        assert by_kind["title"] == (title_id, 1.0)
        assert by_kind["alias"] == (alias_id, 0.9)
        assert by_kind["tag"] == (tag_id, 0.7)
    finally:
        conn.close()


def test_resolve_links_title_match_is_case_insensitive(tmp_path, monkeypatch) -> None:
    conn = _setup_db(tmp_path, monkeypatch)
    try:
        note_id = _insert_note(conn, path="/v/Obsidian.md", title="Obsidian")
        conn.commit()
        _insert_keyword_for_file(conn, "file-1", "obsidian")
        conn.commit()

        counts = resolve_links("file-1", conn=conn)

        assert counts["title"] == 1
        row = conn.execute(
            "SELECT vault_note_id, confidence FROM vault_links "
            "WHERE file_id = 'file-1' AND match_kind = 'title'"
        ).fetchone()
        assert row["vault_note_id"] == note_id
        assert row["confidence"] == 1.0
    finally:
        conn.close()


def test_resolve_links_like_metachar_percent_does_not_spuriously_match(
    tmp_path, monkeypatch
) -> None:
    # POST-FIX regression: alias/tag matching escapes LIKE metacharacters and
    # uses ESCAPE '\'. A keyword containing '%' must be matched LITERALLY, so a
    # note whose alias is plain unrelated text must NOT be linked.
    conn = _setup_db(tmp_path, monkeypatch)
    try:
        # Unrelated note; its alias does not contain a literal '%'.
        unrelated_id = _insert_note(conn, path="/v/Report.md", title="Report", aliases=["abc"])
        # Note that literally contains the metachar keyword as an alias.
        literal_id = _insert_note(conn, path="/v/Pct.md", title="Pct", aliases=["a%c"])
        conn.commit()

        # Without escaping, '%' is a SQL wildcard and would match "abc"
        # (a + anything + c). With ESCAPE '\' it only matches the literal "a%c".
        _insert_keyword_for_file(conn, "file-1", "a%c")
        conn.commit()

        counts = resolve_links("file-1", conn=conn)

        assert counts["alias"] == 1  # only the literal match, not the wildcard one
        matched = conn.execute(
            "SELECT vault_note_id FROM vault_links "
            "WHERE file_id = 'file-1' AND match_kind = 'alias'"
        ).fetchall()
        ids = {r["vault_note_id"] for r in matched}
        assert literal_id in ids
        assert unrelated_id not in ids
    finally:
        conn.close()


def test_resolve_links_like_metachar_underscore_does_not_spuriously_match(
    tmp_path, monkeypatch
) -> None:
    # Same regression for '_' (single-char wildcard) on the tag path.
    conn = _setup_db(tmp_path, monkeypatch)
    try:
        # 'aXc' would match the wildcard 'a_c' if '_' were not escaped.
        unrelated_id = _insert_note(conn, path="/v/U.md", title="U", tags=["aXc"])
        literal_id = _insert_note(conn, path="/v/L.md", title="L", tags=["a_c"])
        conn.commit()

        _insert_keyword_for_file(conn, "file-1", "a_c")
        conn.commit()

        counts = resolve_links("file-1", conn=conn)

        assert counts["tag"] == 1
        matched = conn.execute(
            "SELECT vault_note_id FROM vault_links WHERE file_id = 'file-1' AND match_kind = 'tag'"
        ).fetchall()
        ids = {r["vault_note_id"] for r in matched}
        assert literal_id in ids
        assert unrelated_id not in ids
    finally:
        conn.close()
