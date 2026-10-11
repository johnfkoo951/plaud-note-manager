"""The built-in slot list and how an existing slots.json is treated."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.slots as slots_mod
from core.slots import DEFAULT_SLOTS, Slot, load_slots
from core.templates import KIND_SUMMARY, load_template


def test_default_slot_uses_a_summary_template() -> None:
    # Must stay equal to the app's Database.fallbackSlots, which shows this
    # slot before a slots.json exists; slot-add writes the Python list.
    assert DEFAULT_SLOTS == [
        Slot(name="Default (Claude)", model="claude", template="note-general", model_id="")
    ]
    t = load_template("note-general")
    assert (t.kind, t.deprecated) == (KIND_SUMMARY, False)


def test_missing_slots_file_yields_defaults_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "slots.json"
    monkeypatch.setattr(slots_mod, "SLOTS_FILE", path)
    assert load_slots() == DEFAULT_SLOTS
    assert not path.exists()


def test_existing_slots_file_is_not_migrated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "slots.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "Claude (Anthropic)",
                    "model": "claude",
                    "template": "default",
                    "model_id": "",
                }
            ]
        ),
        encoding="utf-8",
    )
    before = path.read_bytes()
    monkeypatch.setattr(slots_mod, "SLOTS_FILE", path)
    assert [s.template for s in load_slots()] == ["default"]
    assert path.read_bytes() == before
