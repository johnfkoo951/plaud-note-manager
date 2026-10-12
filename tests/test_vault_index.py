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
import sqlite3
import time
from pathlib import Path

import pytest

import core.vault_index as vault_index
from core.vault_index import (
    VaultNote,
    _parse_frontmatter,
    _read_note,
    configured_vaults,
    ensure_schema,
    index_all,
    index_vault,
    related_notes,
    related_wikilinks,
    resolve_links,
)
from core.vault_index import refresh_if_stale as _real_refresh  # conftest stubs the module's


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


# --------------------------------------------------------------------------
# related_notes — one vault at a time, files that exist, a stable order
# --------------------------------------------------------------------------


@pytest.fixture()
def vaults(tmp_path, monkeypatch):
    """An index DB on tmp and two real vault folders; main is configured."""
    conn = _setup_db(tmp_path, monkeypatch)
    main = tmp_path / "vaults" / "Main"
    wiki = tmp_path / "vaults" / "CMDS_LLM_Wiki"
    for root in (main, wiki):
        root.mkdir(parents=True)
    monkeypatch.setattr(vault_index.app_config, "obsidian_vault", lambda: main)
    yield {"conn": conn, "main": main, "wiki": wiki, "tmp": tmp_path}
    conn.close()


def _indexed(conn, root: Path, rel: str, *, aliases=(), tags=(), note_type=None, exists=True):
    """Index `root/rel` (creating the file unless `exists` is False)."""
    path = root / rel
    if exists:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    conn.execute(
        "INSERT INTO vault_notes (vault, path, rel_path, title, aliases, tags, description,"
        " type, mtime, indexed_at) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 0, 0)",
        (
            root.name,
            str(path),
            rel,
            Path(rel).stem,
            json.dumps(list(aliases)),
            json.dumps(list(tags)),
            note_type,
        ),
    )
    conn.commit()
    return path


def test_related_notes_only_returns_notes_from_the_requested_vault(vaults) -> None:
    conn = vaults["conn"]
    _indexed(conn, vaults["main"], "Tools/Obsidian.md")
    _indexed(conn, vaults["wiki"], "20. Wiki/Obsidian.md")
    in_wiki = related_notes(["Obsidian"], vaults=["CMDS_LLM_Wiki"], conn=conn)
    in_main = related_notes(["Obsidian"], vaults=["Main"], conn=conn)
    assert [(n["vault"], n["rel_path"]) for n in in_wiki] == [
        ("CMDS_LLM_Wiki", "20. Wiki/Obsidian.md")
    ]
    assert [(n["vault"], n["rel_path"]) for n in in_main] == [("Main", "Tools/Obsidian.md")]
    assert in_main[0]["path"] == str(vaults["main"] / "Tools/Obsidian.md")


def test_related_notes_defaults_to_the_configured_main_vault(vaults, monkeypatch) -> None:
    conn = vaults["conn"]
    _indexed(conn, vaults["tmp"] / "vaults" / "GOBI", "GobiOnly.md")
    _indexed(conn, vaults["main"], "Obsidian.md")
    assert [n["title"] for n in related_notes(["GobiOnly", "Obsidian"], conn=conn)] == ["Obsidian"]
    monkeypatch.setattr(vault_index.app_config, "obsidian_vault", lambda: None)
    assert related_notes(["Obsidian"], conn=conn) == []


def test_related_wikilinks_never_returns_another_vaults_title(vaults) -> None:
    _indexed(vaults["conn"], vaults["wiki"], "Wiki Only.md")
    assert related_wikilinks(["Wiki Only"], conn=vaults["conn"]) == []
    assert related_wikilinks(["Wiki Only"], vault="CMDS_LLM_Wiki", conn=vaults["conn"]) == [
        "Wiki Only"
    ]


def test_related_notes_skips_rows_whose_file_is_gone(vaults) -> None:
    conn = vaults["conn"]
    _indexed(conn, vaults["main"], "a/Dup.md", exists=False)  # shorter, but deleted
    _indexed(conn, vaults["main"], "bb/Dup.md")
    assert [n["rel_path"] for n in related_notes(["Dup"], conn=conn)] == ["bb/Dup.md"]
    _indexed(conn, vaults["main"], "Gone.md", exists=False)
    assert related_notes(["Gone"], conn=conn) == []


def test_tag_matches_stay_out_of_related_by_default(vaults) -> None:
    conn = vaults["conn"]
    _indexed(conn, vaults["main"], "Other.md", tags=["Tagged"])
    assert related_notes(["Tagged"], conn=conn) == []
    loose = related_notes(["Tagged"], conn=conn, min_confidence=0.7)
    assert [(n["title"], n["kind"]) for n in loose] == [("Other", "tag")]


def test_same_title_resolution_is_deterministic(vaults) -> None:
    conn = vaults["conn"]
    _indexed(conn, vaults["main"], "zz/deep/path/Same.md")
    _indexed(conn, vaults["main"], "b/Same.md")
    _indexed(conn, vaults["main"], "a/Same.md")
    assert [n["rel_path"] for n in related_notes(["Same"], conn=conn)] == ["a/Same.md"]


def test_related_notes_can_leave_out_raw_sources(vaults) -> None:
    conn = vaults["conn"]
    _indexed(conn, vaults["wiki"], "10. Raw Sources/Talk.md", note_type="raw-source")
    _indexed(conn, vaults["wiki"], "20. Wiki/Concept.md", note_type="concept")
    found = related_notes(
        ["Talk", "Concept"], vaults=["CMDS_LLM_Wiki"], conn=conn, exclude_types=("raw-source",)
    )
    assert [n["title"] for n in found] == ["Concept"]


def test_related_notes_lists_a_note_once(vaults) -> None:
    conn = vaults["conn"]
    _indexed(conn, vaults["main"], "Obsidian.md", aliases=["옵시디언"])
    found = related_notes(["Obsidian", "옵시디언"], conn=conn)
    assert [(n["title"], n["kind"]) for n in found] == [("Obsidian", "title")]


# --------------------------------------------------------------------------
# Indexing each configured vault from its own root, refreshed at most hourly
# --------------------------------------------------------------------------


def test_configured_vaults_uses_the_wiki_vaults_own_root(tmp_path, monkeypatch) -> None:
    main = tmp_path / "a" / "Main"
    wiki = tmp_path / "b" / "CMDS_LLM_Wiki"
    for root in (main, wiki):
        root.mkdir(parents=True)
    monkeypatch.setattr(vault_index.app_config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(vault_index.app_config, "obsidian_vault", lambda: main)
    monkeypatch.setenv("PLAUD_WIKI_VAULT", str(wiki))
    assert configured_vaults() == [("Main", main), ("CMDS_LLM_Wiki", wiki.resolve())]
    monkeypatch.setenv("PLAUD_WIKI_VAULT", str(main))
    assert configured_vaults() == [("Main", main)]
    monkeypatch.setenv("PLAUD_WIKI_VAULT", str(tmp_path / "missing"))
    assert configured_vaults() == [("Main", main)]


def test_index_all_indexes_main_and_wiki_from_their_roots(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch).close()
    main = tmp_path / "a" / "Main"
    wiki = tmp_path / "b" / "CMDS_LLM_Wiki"
    for root, name in ((main, "m.md"), (wiki, "w.md")):
        root.mkdir(parents=True)
        (root / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        vault_index, "configured_vaults", lambda: [("Main", main), (wiki.name, wiki)]
    )
    assert index_all() == {"Main": (1, 0), "CMDS_LLM_Wiki": (1, 0)}
    with sqlite3.connect(tmp_path / "plaud.db") as db:
        rows = sorted(db.execute("SELECT vault, path FROM vault_notes").fetchall())
    assert rows == [("CMDS_LLM_Wiki", str(wiki / "w.md")), ("Main", str(main / "m.md"))]


def test_index_vault_hidden_check_is_relative_to_the_root(tmp_path, monkeypatch) -> None:
    conn = _setup_db(tmp_path, monkeypatch)
    try:
        root = tmp_path / ".vaults" / "Main"
        (root / ".obsidian").mkdir(parents=True)
        (root / "note.md").write_text("x", encoding="utf-8")
        (root / ".obsidian" / "x.md").write_text("x", encoding="utf-8")
        assert index_vault("Main", vault_root=root.parent, conn=conn) == (1, 0)
        paths = [r["path"] for r in conn.execute("SELECT path FROM vault_notes")]
        assert paths == [str(root / "note.md")]
    finally:
        conn.close()


def test_cli_vault_index_walks_each_vault_from_its_own_root(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    import cli.main as cli

    _setup_db(tmp_path, monkeypatch).close()
    main = tmp_path / "a" / "Main"
    wiki = tmp_path / "b" / "CMDS_LLM_Wiki"
    for root, name in ((main, "m.md"), (wiki, "w.md")):
        root.mkdir(parents=True)
        (root / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(vault_index.app_config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setenv("PLAUD_OBSIDIAN_VAULT", str(main))
    monkeypatch.setenv("PLAUD_WIKI_VAULT", str(wiki))
    every = CliRunner().invoke(cli.app, ["vault-index"])
    assert every.exit_code == 0, every.output
    assert str(main.resolve()) in every.output and str(wiki.resolve()) in every.output
    assert _note_count(tmp_path / "plaud.db") == 2

    (wiki / "w2.md").write_text("x", encoding="utf-8")
    only_wiki = CliRunner().invoke(cli.app, ["vault-index", "--vault", "wiki"])
    assert only_wiki.exit_code == 0 and "CMDS_LLM_Wiki" in only_wiki.output
    assert _note_count(tmp_path / "plaud.db") == 3

    monkeypatch.delenv("PLAUD_WIKI_VAULT")
    missing = CliRunner().invoke(cli.app, ["vault-index", "--vault", "wiki"])
    assert missing.exit_code == 1 and "vault not found" in missing.output


def _note_count(db: Path) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM vault_notes").fetchone()[0]


def test_refresh_if_stale_indexes_at_most_once_an_hour(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch).close()
    main = tmp_path / "Main"
    main.mkdir()
    (main / "one.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(vault_index, "configured_vaults", lambda: [("Main", main)])
    clock = [1_000_000.0]
    _real_refresh(now=lambda: clock[0])
    assert _note_count(tmp_path / "plaud.db") == 1
    (main / "two.md").write_text("x", encoding="utf-8")
    clock[0] += 3599
    _real_refresh(now=lambda: clock[0])
    assert _note_count(tmp_path / "plaud.db") == 1  # within the hour: not walked again
    clock[0] += 2
    _real_refresh(now=lambda: clock[0])
    assert _note_count(tmp_path / "plaud.db") == 2


def test_refresh_if_stale_reindexes_a_vault_whose_root_moved(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch).close()
    old, new = tmp_path / "old" / "Main", tmp_path / "new" / "Main"
    for root in (old, new):
        root.mkdir(parents=True)
    (new / "moved.md").write_text("x", encoding="utf-8")
    roots = [old]
    monkeypatch.setattr(vault_index, "configured_vaults", lambda: [("Main", roots[0])])
    _real_refresh(now=lambda: 1_000_000.0)
    roots[0] = new
    _real_refresh(now=lambda: 1_000_010.0)
    assert _note_count(tmp_path / "plaud.db") == 1


def test_rows_of_an_old_root_never_become_links(tmp_path, monkeypatch) -> None:
    # core-08 r1 #2: after the wiki moved, its old rows (files kept as a
    # backup) were linked as `vault=Wiki&filepath=Concept.md`, which the
    # current Wiki folder does not have.
    from core.frontmatter import wiki_vault_links

    _setup_db(tmp_path, monkeypatch).close()
    old, current = tmp_path / "old" / "Wiki", tmp_path / "current" / "Wiki"
    for root in (old, current):
        root.mkdir(parents=True)
    (old / "Concept.md").write_text("old", encoding="utf-8")
    (old / "Both.md").write_text("old", encoding="utf-8")
    roots = [old]
    monkeypatch.setattr(vault_index, "configured_vaults", lambda: [("Wiki", roots[0])])
    _real_refresh(now=lambda: 1_000_000.0)
    roots[0] = current
    (current / "NewTopic.md").write_text("new", encoding="utf-8")
    (current / "Both.md").write_text("new", encoding="utf-8")
    _real_refresh(now=lambda: 1_000_001.0)
    found = related_notes(["Concept", "NewTopic", "Both"], vaults=["Wiki"])
    assert [(n["title"], n["path"]) for n in found] == [
        ("NewTopic", str(current / "NewTopic.md")),
        ("Both", str(current / "Both.md")),  # the current row, not the old one
    ]
    assert all((current / n["rel_path"]).is_file() for n in found)
    assert "Concept" not in " ".join(wiki_vault_links(found))


def test_a_failed_refresh_also_waits_an_hour(tmp_path, monkeypatch) -> None:
    # core-08 r1 #3: a walk that raised left no record, so every send tried again.
    _setup_db(tmp_path, monkeypatch).close()
    root = tmp_path / "Main"
    root.mkdir()
    monkeypatch.setattr(vault_index, "configured_vaults", lambda: [("Main", root)])
    calls: list[str] = []

    def failing(name, **_k):
        calls.append(name)
        raise OSError("synthetic walk failure")

    monkeypatch.setattr(vault_index, "index_vault", failing)
    with pytest.raises(OSError):
        _real_refresh(now=lambda: 1_000_000.0)
    _real_refresh(now=lambda: 1_000_001.0)  # within the hour: not walked, no error
    _real_refresh(now=lambda: 1_003_599.0)
    assert calls == ["Main"]
    with pytest.raises(OSError):
        _real_refresh(now=lambda: 1_003_601.0)
    assert calls == ["Main", "Main"]
