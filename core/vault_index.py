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

    vault_index_runs(              -- Python only; throttles refresh_if_stale
        vault TEXT PRIMARY KEY,
        root TEXT NOT NULL,          -- the vault folder it was walked from
        last_run INTEGER NOT NULL
    )
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from . import app_config
from .storage import DEFAULT_DB, Storage

_NO_VAULT_MESSAGE = "set PLAUD_OBSIDIAN_VAULT to index vaults"
# refresh_if_stale walks a vault again only after this long.
REFRESH_MAX_AGE_S = 3600


def default_vault_root() -> Path | None:
    """Parent directory that holds the configured vault(s)."""
    vault = app_config.obsidian_vault()
    return vault.parent if vault else None


def configured_vaults() -> list[tuple[str, Path]]:
    """`(dirname, folder)` of each configured vault that exists: the main
    vault, then the wiki. Each is indexed from its own folder, so a wiki set
    outside the main vault's parent is found too; a wiki that is the main
    vault itself is listed once."""
    out: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for root in _configured_roots():
        if not root.is_dir() or root.resolve() in seen:
            continue
        seen.add(root.resolve())
        out.append((root.name, root))
    return out


def _configured_roots() -> list[Path]:
    """The configured main and wiki folders, whether or not they exist now."""
    from .vault_send import wiki_vault  # here: vault_send imports this module

    return [root for root in (app_config.obsidian_vault(), wiki_vault()) if root is not None]


def default_vaults() -> list[str]:
    """Dirnames of the configured vaults (see `configured_vaults`)."""
    return [name for name, _ in configured_vaults()]


def _connect() -> sqlite3.Connection:
    """The index DB with the vault tables (they need no Storage schema)."""
    DEFAULT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DEFAULT_DB)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


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

CREATE TABLE IF NOT EXISTS vault_index_runs (
    vault TEXT PRIMARY KEY,
    root TEXT NOT NULL,
    last_run INTEGER NOT NULL
);
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
        conn = _connect()
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
        # Relative to the root: a vault kept under a dot-folder is still walked.
        if any(part.startswith(".") for part in path.relative_to(root).parts):
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
    """Index the configured vaults, each from its own folder — or the named
    dirnames under the main vault's parent."""
    if vaults is None:
        pairs = [(name, root.parent) for name, root in configured_vaults()]
    else:
        parent = default_vault_root()
        pairs = [(name, parent) for name in vaults]
    if not pairs:
        print(_NO_VAULT_MESSAGE)
        return {}
    conn = _connect()
    out: dict[str, tuple[int, int]] = {}
    try:
        for name, parent in pairs:
            out[name] = index_vault(name, vault_root=parent, full=full, conn=conn)
    finally:
        conn.close()
    return out


def refresh_if_stale(
    *, max_age_s: int = REFRESH_MAX_AGE_S, now: Callable[[], float] = time.time
) -> dict[str, tuple[int, int]]:
    """Index each configured vault again when its last walk is older than
    `max_age_s` or was made from another folder. The walk is incremental
    (mtime), so only changed notes are read. Called where related links are
    computed; callers catch its errors so indexing never fails a send."""
    pairs = configured_vaults()
    if not pairs:
        return {}
    conn = _connect()
    out: dict[str, tuple[int, int]] = {}
    try:
        for name, root in pairs:
            at = int(now())
            run = conn.execute(
                "SELECT root, last_run FROM vault_index_runs WHERE vault = ?", (name,)
            ).fetchone()
            if run and run["root"] == str(root) and at - int(run["last_run"]) < max_age_s:
                continue
            # Recorded before the walk: a walk that fails waits an hour too,
            # instead of being retried by every send. Its rows are kept.
            conn.execute(
                "INSERT INTO vault_index_runs (vault, root, last_run) VALUES (?, ?, ?) "
                "ON CONFLICT(vault) DO UPDATE SET root = excluded.root, "
                "last_run = excluded.last_run",
                (name, str(root), at),
            )
            conn.commit()
            out[name] = index_vault(name, vault_root=root.parent, conn=conn)
    finally:
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


# How a term may match a note, strongest first: (kind, confidence, condition).
_MATCHES = (
    ("title", 1.0, "title = ? COLLATE NOCASE"),
    ("alias", 0.9, "aliases LIKE ? ESCAPE '\\'"),
    ("tag", 0.7, "tags LIKE ? ESCAPE '\\'"),
)


def _first_match(
    conn: sqlite3.Connection,
    term: str,
    vault: str,
    *,
    min_confidence: float,
    exclude_types: Sequence[str],
    root: Path | None = None,
) -> dict[str, str] | None:
    """The best existing note of `vault` for `term`: strongest match kind,
    then the shortest rel_path, then alphabetical. With `root` (the vault's
    configured folder), a row counts only if it is the file at
    `root/rel_path` — what a link to `vault` + `rel_path` opens — so rows
    left from an earlier folder never become links."""
    if root is not None and not root.is_dir():
        return None  # configured but missing now: no link could open, old rows or not
    esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    current = root.resolve() if root is not None else None
    for kind, confidence, condition in _MATCHES:
        if confidence < min_confidence:
            continue
        rows = conn.execute(
            "SELECT title, rel_path, vault, path, type FROM vault_notes "
            f"WHERE vault = ? AND {condition} ORDER BY length(rel_path), rel_path",
            (vault, term if kind == "title" else f'%"{esc}"%'),
        )
        for row in rows:
            path = Path(row["path"])
            if row["type"] in exclude_types or not path.is_file():
                continue  # an excluded kind, or a note deleted since it was indexed
            if current is not None and path.resolve() != (current / row["rel_path"]).resolve():
                continue  # indexed under another folder of this vault's name
            return {
                "title": str(row["title"]),
                "rel_path": str(row["rel_path"] or ""),
                "vault": str(row["vault"]),
                "path": str(row["path"]),
                "kind": kind,
            }
    return None


def related_notes(
    terms: list[str],
    *,
    vaults: Sequence[str] | None = None,
    conn: sqlite3.Connection | None = None,
    min_confidence: float = 0.9,
    limit: int = 8,
    exclude_types: Sequence[str] = (),
) -> list[dict[str, str]]:
    """Resolve free-text terms (Plaud keywords, LLM key topics, speaker names)
    to existing notes of `vaults` (dirnames, tried in order; default: the
    configured main vault only), so a link never points into another vault
    or at a deleted file. For a configured vault, only rows of its current
    folder count (see `_first_match`).

    Returns ``[{"title", "rel_path", "vault", "path", "kind"}]`` in term
    order, each note once. Match order: exact title (1.0) → alias (0.9) →
    tag (0.7); only matches at or above `min_confidence` are returned, so tag
    matches stay out by default on purpose (PLAN-v0.8 §5: too noisy to link).
    """
    if vaults is None:
        main = app_config.obsidian_vault()
        vaults = [main.name] if main else []
    vault_list = [v for v in vaults if v]
    clean = []
    for term in terms:
        t = str(term or "").strip()
        if len(t) >= 2 and t not in clean:
            clean.append(t)
    if not clean or not vault_list:
        return []
    owns_conn = conn is None
    if owns_conn:
        if not DEFAULT_DB.exists():
            return []
        conn = sqlite3.connect(DEFAULT_DB)
    assert conn is not None
    conn.row_factory = sqlite3.Row
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vault_notes'"
        ).fetchone()
        if not has_table:
            return []
        # Every configured folder, missing ones included: a configured vault
        # never falls back to rows of an older folder.
        roots = {root.name: root for root in _configured_roots()}
        found: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for term in clean:
            hit = None
            for vault in vault_list:
                hit = _first_match(
                    conn,
                    term,
                    vault,
                    min_confidence=min_confidence,
                    exclude_types=exclude_types,
                    root=roots.get(vault),
                )
                if hit:
                    break
            if hit and (hit["vault"], hit["title"]) not in seen:
                seen.add((hit["vault"], hit["title"]))
                found.append(hit)
            if len(found) >= limit:
                break
        return found
    finally:
        if owns_conn:
            conn.close()


def related_wikilinks(
    terms: list[str],
    *,
    vault: str | None = None,
    conn: sqlite3.Connection | None = None,
    min_confidence: float = 0.9,
    limit: int = 8,
) -> list[str]:
    """Titles for `[[…]]` links: notes of one vault only (default: the main
    vault), since a wikilink cannot cross vaults. See related_notes."""
    return [
        n["title"]
        for n in related_notes(
            terms,
            vaults=[vault] if vault else None,
            conn=conn,
            min_confidence=min_confidence,
            limit=limit,
        )
    ]
