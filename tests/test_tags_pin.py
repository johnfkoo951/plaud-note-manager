"""Tag counts, pinned-tag config, and classify-undo manifest behavior."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import cli.main as cli_main
from core import app_config
from core.models import PlaudFile
from core.storage import Storage


def _seed_tags(tmp_path: Path) -> Storage:
    storage = Storage(db_path=tmp_path / "t.db")
    storage.upsert_file(PlaudFile(id="a", filename="A"), now=1)
    storage.upsert_file(PlaudFile(id="b", filename="B"), now=1)
    storage.upsert_file(PlaudFile(id="t", filename="trashed"), now=1, is_trash=1)
    storage.add_note_tags("a", ["meeting", "lg"], source="manual", now=1)
    storage.add_note_tags("b", ["meeting"], source="manual", now=1)
    storage.add_note_tags("t", ["meeting"], source="manual", now=1)  # trashed
    return storage


def test_tag_counts_excludes_trash_and_sorts_by_frequency(tmp_path: Path) -> None:
    storage = _seed_tags(tmp_path)
    counts = dict(storage.tag_counts())
    assert counts["meeting"] == 2  # 'a' + 'b', not the trashed 't'
    assert counts["lg"] == 1
    # busiest first
    assert storage.tag_counts()[0][0] == "meeting"


def test_file_ids_with_tag(tmp_path: Path) -> None:
    storage = _seed_tags(tmp_path)
    assert storage.file_ids_with_tag("lg") == {"a"}
    assert storage.file_ids_with_tag("meeting") == {"a", "b", "t"}


def test_pinned_tags_roundtrip_and_toggle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_config, "CONFIG_FILE", tmp_path / "config.json")
    assert app_config.pinned_tags() == []
    assert app_config.toggle_pinned_tag("lg") is True
    assert app_config.pinned_tags() == ["lg"]
    assert app_config.toggle_pinned_tag("lg") is False
    assert app_config.pinned_tags() == []
    app_config.set_pinned_tags(["a", "a", "b"])  # de-dupes, preserves order
    assert app_config.pinned_tags() == ["a", "b"]


def test_manual_add_upgrades_auto_tag_so_regeneration_keeps_it(tmp_path) -> None:
    from core.storage import Storage

    storage = Storage(tmp_path / "t.db")
    storage.replace_generated_note_tags("f1", ["alpha", "beta"], source="auto", now=1)
    storage.add_note_tags("f1", ["alpha"], source="manual", now=2)
    storage.replace_generated_note_tags("f1", ["gamma"], source="auto", now=3)

    tags = {r["tag"] for r in storage.list_note_tags("f1")}
    assert tags == {"alpha", "gamma"}


@pytest.mark.parametrize(
    ("command", "tag", "method"),
    [
        ("tag-add", "--topic", "add"),
        ("tag-remove", "-topic", "remove"),
    ],
)
def test_tag_commands_accept_leading_hyphen_after_option_terminator(
    monkeypatch: pytest.MonkeyPatch, command: str, tag: str, method: str
) -> None:
    recorded: dict[str, object] = {}

    class FakeStorage:
        def add_note_tags(self, file_id, tags, *, source, now):
            recorded.update(file_id=file_id, tags=tags, method="add")
            return tags

        def remove_note_tags(self, file_id, tags):
            recorded.update(file_id=file_id, tags=tags, method="remove")
            return tags

    monkeypatch.setattr(cli_main, "Storage", FakeStorage)

    result = CliRunner().invoke(cli_main.app, [command, "file-1", "--", tag])

    assert result.exit_code == 0, result.output
    assert recorded == {"file_id": "file-1", "tags": [tag], "method": method}
