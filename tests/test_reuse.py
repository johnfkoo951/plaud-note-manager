from __future__ import annotations

import time
from pathlib import Path

import pytest

from core import reuse
from core.models import PlaudFile
from core.storage import Storage

NOW = int(time.time())


@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "plaud.db")
    s.upsert_file(PlaudFile(id="f1", filename="rec.m4a"), now=NOW)
    return s


def test_mark_upsert_and_summary(storage: Storage) -> None:
    storage.set_reuse("f1", "newsletter", note="게임 사례로", now=NOW)
    storage.set_reuse("f1", "lecture", status="drafted", now=NOW)
    summary = reuse.reuse_summary(storage, "f1")
    assert summary["state"] == "drafted"  # furthest progression wins
    assert {t["channel"] for t in summary["targets"]} == {"newsletter", "lecture"}

    # Re-mark upgrades status but a missing note must not erase the old one.
    storage.set_reuse("f1", "newsletter", status="published", now=NOW + 1)
    rows = storage.list_reuse("f1")
    nl = next(r for r in rows if r["channel"] == "newsletter")
    assert nl["status"] == "published"
    assert nl["note"] == "게임 사례로"


def test_query_and_clear(storage: Storage) -> None:
    storage.set_reuse("f1", "newsletter", now=NOW)
    storage.set_reuse("f1", "shorts", status="drafted", now=NOW)
    assert len(storage.query_reuse(channel="newsletter")) == 1
    assert len(storage.query_reuse(status="drafted")) == 1
    assert storage.query_reuse(channel="newsletter", status="drafted") == []
    assert storage.clear_reuse("f1", "shorts") == 1
    assert len(storage.list_reuse("f1")) == 1


def test_trashed_files_excluded_from_query(storage: Storage) -> None:
    storage.upsert_file(PlaudFile(id="f2", filename="gone.m4a"), now=NOW, is_trash=1)
    storage.set_reuse("f2", "newsletter", now=NOW)
    assert all(r["file_id"] != "f2" for r in storage.query_reuse())


def test_validation_and_frontmatter_pairs(storage: Storage) -> None:
    with pytest.raises(ValueError):
        reuse.validate_channel("blog")
    with pytest.raises(ValueError):
        reuse.validate_status("done")
    storage.set_reuse("f1", "consulting", status="flagged", now=NOW)
    assert reuse.reuse_channels_for_frontmatter(storage, "f1") == ["consulting:flagged"]
