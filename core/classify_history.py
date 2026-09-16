"""Local snapshots for reversible classification; no Plaud network calls."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .storage import Storage


def capture_state(storage: Storage, file_id: str) -> dict:
    with storage._connect() as conn:
        metadata = conn.execute(
            "SELECT * FROM note_metadata WHERE file_id = ?", (file_id,)
        ).fetchone()
        return {
            "folder_ids": [
                r[0]
                for r in conn.execute(
                    "SELECT folder_id FROM file_folders WHERE file_id = ? ORDER BY folder_id",
                    (file_id,),
                )
            ],
            "metadata": dict(metadata) if metadata else None,
            "tags": [
                dict(r)
                for r in conn.execute(
                    "SELECT tag, source, created_at FROM note_tags WHERE file_id = ? ORDER BY tag",
                    (file_id,),
                )
            ],
        }


def restore_state(storage: Storage, file_id: str, state: dict) -> None:
    """Restore exactly the saved local state, including absent metadata/NULLs."""
    with storage._connect() as conn:
        metadata = state["metadata"]
        # Column names come from the schema, never interpolated from the JSON
        # manifest. This also preserves future columns absent in old snapshots.
        columns = [
            r[1]
            for r in conn.execute("PRAGMA table_info(note_metadata)")
            if r[1] != "file_id" and metadata is not None and r[1] in metadata
        ]
        if metadata is None:
            conn.execute("DELETE FROM note_metadata WHERE file_id = ?", (file_id,))
        elif columns:
            names = ", ".join(["file_id", *columns])
            marks = ", ".join("?" for _ in range(len(columns) + 1))
            updates = ", ".join(f"{column} = excluded.{column}" for column in columns)
            conn.execute(
                f"INSERT INTO note_metadata ({names}) VALUES ({marks}) "
                f"ON CONFLICT(file_id) DO UPDATE SET {updates}",
                [file_id, *[metadata[column] for column in columns]],
            )
        conn.execute("DELETE FROM file_folders WHERE file_id = ?", (file_id,))
        conn.executemany(
            "INSERT INTO file_folders (file_id, folder_id) VALUES (?, ?)",
            [(file_id, folder_id) for folder_id in state["folder_ids"]],
        )
        conn.execute("DELETE FROM note_tags WHERE file_id = ?", (file_id,))
        conn.executemany(
            "INSERT INTO note_tags (file_id, tag, source, created_at) VALUES (?, ?, ?, ?)",
            [(file_id, tag["tag"], tag["source"], tag["created_at"]) for tag in state["tags"]],
        )


def write_manifest(path: Path, manifest: dict) -> None:
    """Keep the undo record intact if a process stops during a write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
