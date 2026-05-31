"""Index Obsidian vaults so Plaud keywords can resolve to real notes.

Walks every `.md` under each configured vault root, parses YAML frontmatter
(aliases, tags, type, description), and stores one row per note. The
indexer is incremental — only re-reads files whose mtime has changed since
last scan.

Output schema (created/upgraded by Storage):

    vault_notes(
        id INTEGER PRIMARY KEY,
        vault TEXT,                -- vault dirname (e.g. MyVault)
        path TEXT UNIQUE,          -- absolute path
        rel_path TEXT,             -- path relative to vault root
        title TEXT,                -- filename without extension
        aliases TEXT,              -- JSON list
        tags TEXT,                 -- JSON list
        description TEXT,
        type TEXT,
        mtime REAL,
        indexed_at INTEGER
    )

    keywords(
        id INTEGER PRIMARY KEY,
        term TEXT UNIQUE COLLATE NOCASE
    )

    file_keywords(
        file_id TEXT,
        keyword_id INTEGER,
        source TEXT NOT NULL DEFAULT 'plaud',   -- plaud | manual | cmds
        PRIMARY KEY (file_id, keyword_id, source)
    )

    vault_links(
        file_id TEXT NOT NULL,
        vault_note_id INTEGER NOT NULL,
        match_kind TEXT NOT NULL,     -- title | alias | tag
        keyword TEXT NOT NULL,        -- the source keyword that produced the link
        confidence REAL NOT NULL DEFAULT 1.0,
        created_at INTEGER NOT NULL,
        PRIMARY KEY (file_id, vault_note_id, match_kind, keyword)
    )
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import app_config
from .storage import DEFAULT_DB, Storage

_NO_VAULT_MESSAGE = "set PLAUD_OBSIDIAN_VAULT to index vaults"


def default_vault_root() -> Path | None:
    """Parent directory that holds the configured vault(s)."""
    vault = app_config.obsidian_vault()
    return vault.parent if vault else None


def default_vaults() -> list[str]:
    """Vault dirnames to index. Derived from the configured vault's name."""
    vault = app_config.obsidian_vault()
    return [vault.name] if vault else []


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\s*\n?", re.DOTALL)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vault_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vault TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    rel_path TEXT,
    title TEXT NOT NULL,
    aliases TEXT,
    tags TEXT,
    description TEXT,
    type TEXT,
    mtime REAL,
    indexed_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS vault_notes_title_idx ON vault_notes(title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS vault_notes_vault_idx ON vault_notes(vault);

CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS file_keywords (
    file_id TEXT NOT NULL,
    keyword_id INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'plaud',
    PRIMARY KEY (file_id, keyword_id, source)
);
CREATE INDEX IF NOT EXISTS file_keywords_kid_idx ON file_keywords(keyword_id);

CREATE TABLE IF NOT EXISTS vault_links (
    file_id TEXT NOT NULL,
    vault_note_id INTEGER NOT NULL,
    match_kind TEXT NOT NULL,
    keyword TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (file_id, vault_note_id, match_kind, keyword)
);
CREATE INDEX IF NOT EXISTS vault_links_file_idx ON vault_links(file_id);
CREATE INDEX IF NOT EXISTS vault_links_vault_idx ON vault_links(vault_note_id);
"""


@dataclass
class VaultNote:
    vault: str
    path: Path
    rel_path: str
    title: str
    aliases: list[str]
    tags: list[str]
    description: str | None
    note_type: str | None
    mtime: float


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


def _parse_frontmatter(raw: str) -> dict:
    """Best-effort YAML parse without taking a yaml dependency.

    Supports the subset Obsidian users actually write: scalar, list (`-` items
    or `[a, b]`), and quoted strings. Nested mappings (an indented `key: value`
    under a key) are NOT supported — such a field is dropped rather than
    indexed as an empty list.
    """
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}
    body = m.group(1)
    out: dict = {}
    current_key: str | None = None
    for line in body.splitlines():
        if not line.strip():
            continue
        if line.startswith(" ") and current_key:
            # list item under current key
            stripped = line.strip()
            if stripped.startswith("- "):
                val = stripped[2:].strip().strip("\"'")
                # strip Obsidian [[wikilink]] wrappers — we keep the inner text
                m2 = re.match(r"^\[\[([^\]]+)\]\]$", val)
                if m2:
                    val = m2.group(1).split("|")[0]
                existing = out.get(current_key)
                if existing is None or existing == "":
                    out[current_key] = [val]
                elif isinstance(existing, list):
                    existing.append(val)
                else:
                    # was scalar string — promote to list
                    out[current_key] = [existing, val]
            elif out.get(current_key) == []:
                # Indented non-list line = nested mapping we don't parse. Drop
                # the auto-created empty key so the field isn't falsely [].
                out.pop(current_key, None)
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        current_key = key
        if not val:
            out.setdefault(key, [])
            continue
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            items = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
            out[key] = items
        else:
            val = val.strip("\"'")
            out[key] = val
    return out


def _read_note(vault: str, root: Path, path: Path) -> VaultNote | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    fm = _parse_frontmatter(raw)
    aliases = fm.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    aliases = [a for a in aliases if isinstance(a, str)]
    tags = [t.lstrip("#") for t in tags if isinstance(t, str)]
    return VaultNote(
        vault=vault,
        path=path,
        rel_path=str(path.relative_to(root)),
        title=path.stem,
        aliases=aliases,
        tags=tags,
        description=fm.get("description") if isinstance(fm.get("description"), str) else None,
        note_type=fm.get("type") if isinstance(fm.get("type"), str) else None,
        mtime=path.stat().st_mtime,
    )


def index_vault(
    vault_name: str,
    *,
    vault_root: Path | None = None,
    full: bool = False,
    conn: sqlite3.Connection | None = None,
) -> tuple[int, int]:
    """Index one vault. Returns (added/updated count, skipped count)."""
    if vault_root is None:
        vault_root = default_vault_root()
    if vault_root is None:
        print(_NO_VAULT_MESSAGE)
        return 0, 0
    owns_conn = conn is None
    if owns_conn:
        Storage()  # ensure base schema
        conn = sqlite3.connect(DEFAULT_DB)
    assert conn is not None
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    root = vault_root / vault_name
    if not root.exists():
        if owns_conn:
            conn.close()
        return 0, 0

    # Existing notes' mtime for incremental skip.
    existing: dict[str, float] = {
        row["path"]: row["mtime"] or 0.0
        for row in conn.execute(
            "SELECT path, mtime FROM vault_notes WHERE vault = ?", (vault_name,)
        )
    }

    now = int(time.time())
    touched = 0
    skipped = 0
    for path in root.rglob("*.md"):
        if any(part.startswith(".") for part in path.parts):
            continue
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            continue
        if not full and existing.get(str(path), 0.0) >= mtime:
            skipped += 1
            continue
        note = _read_note(vault_name, root, path)
        if not note:
            continue
        conn.execute(
            """
            INSERT INTO vault_notes
                (vault, path, rel_path, title, aliases, tags, description, type, mtime, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                vault = excluded.vault,
                rel_path = excluded.rel_path,
                title = excluded.title,
                aliases = excluded.aliases,
                tags = excluded.tags,
                description = excluded.description,
                type = excluded.type,
                mtime = excluded.mtime,
                indexed_at = excluded.indexed_at
            """,
            (
                note.vault,
                str(note.path),
                note.rel_path,
                note.title,
                json.dumps(note.aliases, ensure_ascii=False),
                json.dumps(note.tags, ensure_ascii=False),
                note.description,
                note.note_type,
                note.mtime,
                now,
            ),
        )
        touched += 1

    conn.commit()
    if owns_conn:
        conn.close()
    return touched, skipped


def index_all(
    vaults: Iterable[str] | None = None, *, full: bool = False
) -> dict[str, tuple[int, int]]:
    vault_list = list(vaults) if vaults is not None else default_vaults()
    if not vault_list:
        print(_NO_VAULT_MESSAGE)
        return {}
    Storage()
    conn = sqlite3.connect(DEFAULT_DB)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    out: dict[str, tuple[int, int]] = {}
    for vault in vault_list:
        out[vault] = index_vault(vault, full=full, conn=conn)
    conn.close()
    return out


# ---------- Keyword <-> vault note resolution ---------------------------


def _store_keywords_for_file(
    conn: sqlite3.Connection, file_id: str, terms: list[str], source: str
) -> list[tuple[int, str]]:
    """Upsert keywords + file_keywords. Returns list of (keyword_id, term)."""
    out: list[tuple[int, str]] = []
    for raw in terms:
        term = raw.strip()
        if not term:
            continue
        cur = conn.execute(
            "INSERT INTO keywords(term) VALUES (?) "
            "ON CONFLICT(term) DO UPDATE SET term = excluded.term "
            "RETURNING id",
            (term,),
        )
        row = cur.fetchone()
        kid = int(row[0])
        conn.execute(
            "INSERT OR IGNORE INTO file_keywords(file_id, keyword_id, source) VALUES (?, ?, ?)",
            (file_id, kid, source),
        )
        out.append((kid, term))
    return out


def sync_plaud_keywords(conn: sqlite3.Connection | None = None) -> int:
    """Read `file_content.keywords` JSON for every file and populate
    `keywords` + `file_keywords`. Idempotent.
    """
    owns_conn = conn is None
    if owns_conn:
        Storage()
        conn = sqlite3.connect(DEFAULT_DB)
    assert conn is not None
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    rows = conn.execute(
        "SELECT file_id, keywords FROM file_content WHERE keywords IS NOT NULL"
    ).fetchall()
    n = 0
    for row in rows:
        try:
            terms = json.loads(row["keywords"] or "[]")
        except Exception:
            continue
        if not terms:
            continue
        _store_keywords_for_file(conn, row["file_id"], list(terms), source="plaud")
        n += 1
    conn.commit()
    if owns_conn:
        conn.close()
    return n


def resolve_links(
    file_id: str | None = None, *, conn: sqlite3.Connection | None = None
) -> dict[str, int]:
    """For each keyword on each file, find matching vault_notes
    (by title, alias, or tag) and write rows into vault_links.

    Returns counts per match_kind.
    """
    owns_conn = conn is None
    if owns_conn:
        Storage()
        conn = sqlite3.connect(DEFAULT_DB)
    assert conn is not None
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    where_sql = "" if file_id is None else " WHERE fk.file_id = ?"
    params: tuple = () if file_id is None else (file_id,)

    pairs = conn.execute(
        f"""
        SELECT DISTINCT fk.file_id AS file_id, k.term AS term
          FROM file_keywords fk
          JOIN keywords k ON k.id = fk.keyword_id
          {where_sql}
        """,
        params,
    ).fetchall()

    counts = {"title": 0, "alias": 0, "tag": 0}
    now = int(time.time())
    for pair in pairs:
        fid = pair["file_id"]
        term = pair["term"]
        # Match 1: exact title (case insensitive)
        for v in conn.execute(
            "SELECT id FROM vault_notes WHERE title = ? COLLATE NOCASE",
            (term,),
        ):
            conn.execute(
                "INSERT OR IGNORE INTO vault_links "
                "(file_id, vault_note_id, match_kind, keyword, confidence, created_at) "
                "VALUES (?, ?, 'title', ?, 1.0, ?)",
                (fid, v["id"], term, now),
            )
            counts["title"] += 1
        # Match 2: alias contains term (JSON LIKE). Escape LIKE metacharacters
        # (\ % _) so a keyword containing them can't create false-positive links.
        esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like_term = f'%"{esc}"%'
        for v in conn.execute(
            "SELECT id FROM vault_notes WHERE aliases LIKE ? ESCAPE '\\'",
            (like_term,),
        ):
            conn.execute(
                "INSERT OR IGNORE INTO vault_links "
                "(file_id, vault_note_id, match_kind, keyword, confidence, created_at) "
                "VALUES (?, ?, 'alias', ?, 0.9, ?)",
                (fid, v["id"], term, now),
            )
            counts["alias"] += 1
        # Match 3: tag contains term
        for v in conn.execute(
            "SELECT id FROM vault_notes WHERE tags LIKE ? ESCAPE '\\'",
            (like_term,),
        ):
            conn.execute(
                "INSERT OR IGNORE INTO vault_links "
                "(file_id, vault_note_id, match_kind, keyword, confidence, created_at) "
                "VALUES (?, ?, 'tag', ?, 0.7, ?)",
                (fid, v["id"], term, now),
            )
            counts["tag"] += 1

    conn.commit()
    if owns_conn:
        conn.close()
    return counts
