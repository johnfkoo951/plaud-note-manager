"""SQLite-backed metadata + content cache for Plaud files."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import PROJECT_ROOT
from .models import FileContent, FileStatus, Folder, PlaudFile
from .tags import normalize_tags

DEFAULT_DB = PROJECT_ROOT / "data" / "plaud.db"

# Bump when SCHEMA_TABLES/_migrate change; gates the migration fast-path.
SCHEMA_VERSION = 1

SCHEMA_TABLES = """
CREATE TABLE IF NOT EXISTS files (
    id          TEXT PRIMARY KEY,
    filename    TEXT,
    filesize    INTEGER,
    duration    REAL,
    edit_time   INTEGER,
    start_time  INTEGER,
    is_trash    INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'new',
    local_path  TEXT,
    synced_at   INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS folders (
    id      TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    icon    TEXT,
    color   TEXT,
    synced_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS file_folders (
    file_id   TEXT NOT NULL,
    folder_id TEXT NOT NULL,
    PRIMARY KEY (file_id, folder_id)
);
CREATE TABLE IF NOT EXISTS file_content (
    file_id      TEXT PRIMARY KEY,
    title        TEXT,
    transcript   TEXT,
    outline      TEXT,
    summary_md   TEXT,
    summary_extra TEXT,
    keywords     TEXT,
    fetched_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cmds_transcripts (
    file_id     TEXT NOT NULL,
    model       TEXT NOT NULL,
    language    TEXT,
    text        TEXT,
    segments    TEXT,
    fetched_at  INTEGER NOT NULL,
    PRIMARY KEY (file_id, model)
);
CREATE TABLE IF NOT EXISTS speakers (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    is_self   INTEGER NOT NULL DEFAULT 0,
    notes     TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS note_metadata (
    file_id         TEXT PRIMARY KEY,
    title           TEXT,
    description     TEXT,
    note_type       TEXT,
    status          TEXT NOT NULL DEFAULT 'unread',
    usage_status    TEXT NOT NULL DEFAULT 'unused',
    category        TEXT,
    folder_id       TEXT,
    folder_name     TEXT,
    vault_path      TEXT,
    draft_path      TEXT,
    final_note_path TEXT,
    metadata_json   TEXT,
    generated_at    INTEGER,
    updated_at      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS note_tags (
    file_id    TEXT NOT NULL,
    tag        TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'manual',
    created_at INTEGER NOT NULL,
    PRIMARY KEY (file_id, tag)
);
CREATE TABLE IF NOT EXISTS note_references (
    file_id    TEXT NOT NULL,
    path       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    title      TEXT,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (file_id, path)
);
"""

SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS files_status_idx ON files(status);
CREATE INDEX IF NOT EXISTS files_edit_time_idx ON files(edit_time DESC);
CREATE INDEX IF NOT EXISTS files_trash_idx ON files(is_trash);
CREATE INDEX IF NOT EXISTS note_tags_tag_idx ON note_tags(tag);
CREATE INDEX IF NOT EXISTS note_refs_file_idx ON note_references(file_id);
CREATE INDEX IF NOT EXISTS file_folders_folder_idx ON file_folders(folder_id);
"""


class Storage:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        with self._connect() as conn:
            conn.executescript(SCHEMA_TABLES)
            self._migrate(conn)
            conn.executescript(SCHEMA_INDEXES)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        # Version-gate the (idempotent) migrations so an up-to-date DB skips the
        # PRAGMA table_info diffing on every open.
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        cols = {row[1] for row in conn.execute("PRAGMA table_info(files)")}
        if "is_trash" not in cols:
            conn.execute("ALTER TABLE files ADD COLUMN is_trash INTEGER NOT NULL DEFAULT 0")
        if "start_time" not in cols:
            conn.execute("ALTER TABLE files ADD COLUMN start_time INTEGER")
        note_cols = {row[1] for row in conn.execute("PRAGMA table_info(note_metadata)")}
        if note_cols:
            if "usage_status" not in note_cols:
                conn.execute(
                    "ALTER TABLE note_metadata ADD COLUMN usage_status TEXT "
                    "NOT NULL DEFAULT 'unused'"
                )
            if "category" not in note_cols:
                conn.execute("ALTER TABLE note_metadata ADD COLUMN category TEXT")
            if "folder_id" not in note_cols:
                conn.execute("ALTER TABLE note_metadata ADD COLUMN folder_id TEXT")
            if "folder_name" not in note_cols:
                conn.execute("ALTER TABLE note_metadata ADD COLUMN folder_name TEXT")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        # WAL mode lets Swift readers/writers and Python writers share the
        # database concurrently without locking each other out.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        # Wait (instead of failing immediately) when another writer holds the lock.
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---------- files ----------

    def upsert_file(self, file: PlaudFile, *, now: int, is_trash: int = 0) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO files (id, filename, filesize, duration, edit_time,
                                   start_time, is_trash, status, synced_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    filename   = excluded.filename,
                    filesize   = excluded.filesize,
                    duration   = excluded.duration,
                    edit_time  = excluded.edit_time,
                    start_time = excluded.start_time,
                    is_trash   = excluded.is_trash,
                    synced_at  = excluded.synced_at,
                    updated_at = excluded.updated_at
                """,
                (
                    file.id,
                    file.filename or file.fullname,
                    file.filesize,
                    file.duration,
                    file.edit_time,
                    file.start_time,
                    is_trash,
                    now,
                    now,
                ),
            )

    def mark_downloaded(self, file_id: str, local_path: Path, *, now: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE files SET status = ?, local_path = ?, updated_at = ?
                    WHERE id = ?""",
                (FileStatus.DOWNLOADED.value, str(local_path), now, file_id),
            )

    def files_by_status(self, status: FileStatus) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM files WHERE status = ? ORDER BY edit_time DESC",
                    (status.value,),
                )
            )

    def get_file_row(self, file_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()

    def counts(self) -> dict[str, Any]:
        """Library summary counts for the status dashboard."""
        with self._connect() as conn:

            def one(sql: str) -> int:
                return int(conn.execute(sql).fetchone()[0])

            usage = {
                row[0]: int(row[1])
                for row in conn.execute(
                    "SELECT usage_status, COUNT(*) FROM note_metadata GROUP BY usage_status"
                )
            }
            return {
                "total": one("SELECT COUNT(*) FROM files WHERE is_trash = 0"),
                "trash": one("SELECT COUNT(*) FROM files WHERE is_trash = 1"),
                "unfiled": one(
                    "SELECT COUNT(*) FROM files WHERE is_trash = 0 "
                    "AND id NOT IN (SELECT file_id FROM file_folders)"
                ),
                "folders": one("SELECT COUNT(*) FROM folders"),
                "cached": one("SELECT COUNT(*) FROM file_content"),
                "usage_status": usage,
            }

    # ---------- folders ----------

    def replace_folders(self, folders: list[Folder], *, now: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM folders")
            conn.executemany(
                "INSERT INTO folders (id, name, icon, color, synced_at) VALUES (?, ?, ?, ?, ?)",
                [(f.id, f.name, f.icon, f.color, now) for f in folders],
            )
            # Drop file->folder links whose folder no longer exists, so downstream
            # classification/link guards aren't poisoned by orphaned rows.
            conn.execute("DELETE FROM file_folders WHERE folder_id NOT IN (SELECT id FROM folders)")

    def set_file_folders(self, file_id: str, folder_ids: list[str]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM file_folders WHERE file_id = ?", (file_id,))
            conn.executemany(
                "INSERT INTO file_folders (file_id, folder_id) VALUES (?, ?)",
                [(file_id, fid) for fid in folder_ids],
            )

    def files_with_multiple_folders(self) -> list[tuple[str, list[str]]]:
        """Files whose local mapping still holds >1 folder (breaks Plaud web)."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT file_id, GROUP_CONCAT(folder_id, char(31)) AS ids
                  FROM file_folders
                 GROUP BY file_id
                HAVING COUNT(*) > 1
                """
            ).fetchall()
        return [(r["file_id"], r["ids"].split(chr(31))) for r in rows]

    def set_file_name(self, file_id: str, name: str, *, now: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE files SET filename = ?, updated_at = ? WHERE id = ?",
                (name, now, file_id),
            )

    def list_folders(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(conn.execute("SELECT * FROM folders ORDER BY name COLLATE NOCASE"))

    def folder_by_name(self, name: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM folders WHERE lower(name) = lower(?) LIMIT 1",
                (name,),
            ).fetchone()

    # ---------- content ----------

    def save_content(self, content: FileContent, *, now: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO file_content
                    (file_id, title, transcript, outline, summary_md,
                     summary_extra, keywords, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    title = excluded.title,
                    transcript = excluded.transcript,
                    outline = excluded.outline,
                    summary_md = excluded.summary_md,
                    summary_extra = excluded.summary_extra,
                    keywords = excluded.keywords,
                    fetched_at = excluded.fetched_at
                """,
                (
                    content.file_id,
                    content.title,
                    json.dumps([s.model_dump() for s in content.transcript], ensure_ascii=False),
                    json.dumps([o.model_dump() for o in content.outline], ensure_ascii=False),
                    content.summary_md,
                    json.dumps(content.summary_extra_md, ensure_ascii=False),
                    json.dumps(content.keywords, ensure_ascii=False),
                    now,
                ),
            )
        self.set_file_folders(content.file_id, content.folder_ids)

    def get_content_row(self, file_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM file_content WHERE file_id = ?", (file_id,)
            ).fetchone()

    def files_without_content(self) -> list[sqlite3.Row]:
        """Files that don't yet have transcript/summary cached."""
        with self._connect() as conn:
            return list(
                conn.execute("""
                SELECT id FROM files
                 WHERE is_trash = 0
                   AND id NOT IN (SELECT file_id FROM file_content)
                 ORDER BY edit_time DESC
            """)
            )

    # ---------- note metadata / tags ----------

    def upsert_note_metadata(
        self,
        *,
        file_id: str,
        now: int,
        title: str | None = None,
        description: str | None = None,
        note_type: str | None = None,
        status: str | None = None,
        usage_status: str | None = None,
        category: str | None = None,
        folder_id: str | None = None,
        folder_name: str | None = None,
        vault_path: Path | str | None = None,
        draft_path: Path | str | None = None,
        final_note_path: Path | str | None = None,
        metadata: dict | None = None,
        generated_at: int | None = None,
    ) -> None:
        """Insert or update note metadata.

        For every COALESCE'd column, passing None means "leave unchanged" on an
        existing row (the new value only applies when non-None). Use
        update_usage_status / update_note_folder for explicit overwrites.
        """
        metadata_json = (
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True)
            if metadata is not None
            else None
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO note_metadata
                    (file_id, title, description, note_type, status, usage_status,
                     category, folder_id, folder_name, vault_path, draft_path,
                     final_note_path, metadata_json, generated_at, updated_at)
                VALUES (?, ?, ?, ?, COALESCE(?, 'unread'), COALESCE(?, 'unused'),
                        ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    title = COALESCE(excluded.title, title),
                    description = COALESCE(excluded.description, description),
                    note_type = COALESCE(excluded.note_type, note_type),
                    status = COALESCE(excluded.status, status),
                    usage_status = COALESCE(excluded.usage_status, usage_status),
                    category = COALESCE(excluded.category, category),
                    folder_id = COALESCE(excluded.folder_id, folder_id),
                    folder_name = COALESCE(excluded.folder_name, folder_name),
                    vault_path = COALESCE(excluded.vault_path, vault_path),
                    draft_path = COALESCE(excluded.draft_path, draft_path),
                    final_note_path = COALESCE(excluded.final_note_path, final_note_path),
                    metadata_json = COALESCE(excluded.metadata_json, metadata_json),
                    generated_at = COALESCE(excluded.generated_at, generated_at),
                    updated_at = excluded.updated_at
                """,
                (
                    file_id,
                    title,
                    description,
                    note_type,
                    status,
                    usage_status,
                    category,
                    folder_id,
                    folder_name,
                    str(vault_path) if vault_path else None,
                    str(draft_path) if draft_path else None,
                    str(final_note_path) if final_note_path else None,
                    metadata_json,
                    generated_at,
                    now,
                ),
            )

    def update_note_folder(
        self,
        file_id: str,
        *,
        folder_id: str | None,
        folder_name: str | None,
        now: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE note_metadata
                   SET folder_id = COALESCE(?, folder_id),
                       folder_name = COALESCE(?, folder_name),
                       updated_at = ?
                 WHERE file_id = ?
                """,
                (folder_id, folder_name, now, file_id),
            )

    def update_usage_status(self, file_id: str, usage_status: str, *, now: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO note_metadata (file_id, status, usage_status, updated_at)
                VALUES (?, 'unread', ?, ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    usage_status = excluded.usage_status,
                    updated_at = excluded.updated_at
                """,
                (file_id, usage_status, now),
            )

    def files_for_classification(
        self,
        *,
        include_filed: bool = False,
        limit: int | None = None,
    ) -> list[sqlite3.Row]:
        where = "f.is_trash = 0"
        if not include_filed:
            where += " AND f.id NOT IN (SELECT file_id FROM file_folders)"
        sql = f"""
            SELECT f.*, fc.title, fc.keywords, fc.summary_md, fc.summary_extra
              FROM files f
              LEFT JOIN file_content fc ON fc.file_id = f.id
             WHERE {where}
             ORDER BY COALESCE(f.start_time, f.edit_time * 1000) DESC
        """
        params: list[int] = []
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._connect() as conn:
            return list(conn.execute(sql, params))

    def get_note_metadata(self, file_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM note_metadata WHERE file_id = ?", (file_id,)
            ).fetchone()

    def list_note_tags(self, file_id: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT tag, source, created_at
                      FROM note_tags
                     WHERE file_id = ?
                     ORDER BY CASE source
                                WHEN 'manual' THEN 0
                                WHEN 'ai' THEN 1
                                WHEN 'auto' THEN 2
                                ELSE 3
                              END,
                              tag COLLATE NOCASE
                    """,
                    (file_id,),
                )
            )

    def add_note_tags(
        self,
        file_id: str,
        raw_tags: list[str],
        *,
        source: str = "manual",
        now: int,
    ) -> list[str]:
        tags = normalize_tags(raw_tags)
        if not tags:
            return []
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO note_tags (file_id, tag, source, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(file_id, tag) DO NOTHING
                """,
                [(file_id, tag, source, now) for tag in tags],
            )
        return tags

    def replace_generated_note_tags(
        self,
        file_id: str,
        raw_tags: list[str],
        *,
        source: str,
        now: int,
    ) -> list[str]:
        tags = normalize_tags(raw_tags)
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM note_tags WHERE file_id = ? AND source IN ('ai', 'auto')",
                (file_id,),
            )
            if tags:
                conn.executemany(
                    """
                    INSERT INTO note_tags (file_id, tag, source, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(file_id, tag) DO NOTHING
                    """,
                    [(file_id, tag, source, now) for tag in tags],
                )
        return tags

    def remove_note_tags(self, file_id: str, raw_tags: list[str]) -> list[str]:
        tags = normalize_tags(raw_tags)
        if not tags:
            return []
        with self._connect() as conn:
            conn.executemany(
                "DELETE FROM note_tags WHERE file_id = ? AND tag = ?",
                [(file_id, tag) for tag in tags],
            )
        return tags

    def upsert_note_reference(
        self,
        *,
        file_id: str,
        path: Path | str,
        kind: str,
        title: str | None = None,
        now: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO note_references (file_id, path, kind, title, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(file_id, path) DO UPDATE SET
                    kind = excluded.kind,
                    title = COALESCE(excluded.title, title),
                    updated_at = excluded.updated_at
                """,
                (file_id, str(path), kind, title, now),
            )

    def list_note_references(self, file_id: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT path, kind, title, updated_at
                      FROM note_references
                     WHERE file_id = ?
                     ORDER BY updated_at DESC
                    """,
                    (file_id,),
                )
            )

    # ---------- speakers (saved list) ----------

    def add_speaker(self, *, name: str, is_self: bool = False, now: int) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO speakers (name, is_self, created_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET is_self = excluded.is_self
                   RETURNING id""",
                (name, 1 if is_self else 0, now),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def list_speakers(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(
                conn.execute(
                    "SELECT id, name, is_self, notes FROM speakers "
                    "ORDER BY is_self DESC, name COLLATE NOCASE"
                )
            )

    def delete_speaker(self, speaker_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))

    # ---------- cmds transcripts ----------

    def get_cmds_transcript(self, file_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                """SELECT * FROM cmds_transcripts WHERE file_id = ?
                    ORDER BY fetched_at DESC LIMIT 1""",
                (file_id,),
            ).fetchone()

    def update_cmds_segments(
        self, file_id: str, model: str, segments_json: str, *, now: int
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE cmds_transcripts
                      SET segments = ?, fetched_at = ?
                    WHERE file_id = ? AND model = ?""",
                (segments_json, now, file_id, model),
            )

    def save_cmds_transcript(
        self,
        *,
        file_id: str,
        model: str,
        language: str | None,
        text: str,
        segments_json: str,
        now: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cmds_transcripts
                    (file_id, model, language, text, segments, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_id, model) DO UPDATE SET
                    language = excluded.language,
                    text = excluded.text,
                    segments = excluded.segments,
                    fetched_at = excluded.fetched_at
                """,
                (file_id, model, language, text, segments_json, now),
            )

    def files_missing_folder_link(self) -> list[sqlite3.Row]:
        """Files whose folder assignment has never been recorded.

        We check based on `file_content` cache absence as a proxy — once content
        is fetched, folder ids are saved alongside it. For untouched files we
        still want to reflect folder membership in the sidebar, so this lets
        the basic `sync` opportunistically pull folder ids for un-cached files.
        """
        with self._connect() as conn:
            return list(
                conn.execute("""
                SELECT id FROM files
                 WHERE is_trash = 0
                   AND id NOT IN (SELECT file_id FROM file_folders)
                   AND id NOT IN (SELECT file_id FROM file_content)
                 ORDER BY edit_time DESC
                 LIMIT 30
            """)
            )
