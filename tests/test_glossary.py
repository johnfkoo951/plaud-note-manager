from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from core import glossary as gl
from core.models import PlaudFile
from core.storage import Storage

NOW = int(time.time())

NOTE = """---
type: terminology
date modified: 2026-01-01T00:00
---
# 용어집

## 인물

- **홍길동** — 인물 · 호칭: 길동, 길동이 · 둘째 아들

## 용어·자곤

## 검토 대기 (자동 수집)
<!-- plaud-glossary:auto -->
"""


@pytest.fixture()
def note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "vault" / gl.DEFAULT_GLOSSARY_RELPATH
    path.parent.mkdir(parents=True)
    path.write_text(NOTE, encoding="utf-8")
    from core import app_config

    monkeypatch.setattr(app_config, "obsidian_vault", lambda: tmp_path / "vault")
    monkeypatch.setattr(gl, "_configured", lambda key: "")
    return path


@pytest.fixture()
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Storage:
    import core.paths as paths_mod

    monkeypatch.setattr(paths_mod, "INTEGRATED_DIR", tmp_path / "integrated")
    s = Storage(tmp_path / "plaud.db")
    s.upsert_file(PlaudFile(id="f1", filename="가족 대화"), now=NOW)
    final = tmp_path / "integrated" / "f1" / "claude__integrated.transcript.md"
    final.parent.mkdir(parents=True)
    final.write_text(
        "---\nkind: transcript\n---\n\n[00:00:01] 김철수: 길동이 내일 바람빛 가?\n",
        encoding="utf-8",
    )
    return s


def stub_model(monkeypatch: pytest.MonkeyPatch, reply: str) -> list[str]:
    import core.summarize as summarize

    prompts: list[str] = []

    def fake_run(model: str, prompt: str, **_kw) -> str:
        prompts.append(prompt)
        return reply

    monkeypatch.setattr(summarize, "run_model", fake_run)
    monkeypatch.setattr(summarize, "model_available", lambda _m: True)
    return prompts


REPLY = json.dumps(
    [
        {"term": "바람빛교회", "kind": "place", "aliases": ["바람빛"], "note": "다니는 교회"},
        {"term": "홍길동", "kind": "person", "aliases": ["길동이"]},  # already in the note
        {"term": "바람빛교회", "kind": "place"},  # repeated in the batch
        {"term": "용", "kind": "person"},  # too short to be a term
        {"term": "", "kind": "term"},
    ],
    ensure_ascii=False,
)


def test_paths_resolve_vault_relative_absolute_and_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core import app_config

    monkeypatch.setattr(app_config, "obsidian_vault", lambda: tmp_path)
    assert gl.resolve_note("", "a/b.md") == tmp_path / "a/b.md"
    assert gl.resolve_note("x/y.md", "a/b.md") == tmp_path / "x/y.md"
    assert gl.resolve_note("/abs/z.md", "a/b.md") == Path("/abs/z.md")
    monkeypatch.setattr(app_config, "obsidian_vault", lambda: None)
    assert gl.resolve_note("x/y.md", "a/b.md") is None
    assert gl.resolve_note("/abs/z.md", "a/b.md") == Path("/abs/z.md")


def test_prompt_text_drops_front_matter_and_labels_pending_entries(note: Path) -> None:
    note.write_text(NOTE + "- **새말** — 용어\n", encoding="utf-8")
    text = gl.load_for_prompt()
    assert "type: terminology" not in text
    assert "plaud-glossary:auto" not in text
    assert text.index("**홍길동**") < text.index("미확인 후보") < text.index("**새말**")


def test_roster_context_includes_the_glossary(note: Path) -> None:
    from core.dual_speakers import load_transcription_context

    assert "**홍길동**" in load_transcription_context()


def test_new_candidates_skip_known_repeated_and_short_terms() -> None:
    fresh = gl.new_candidates(gl.parse_candidates(REPLY), NOTE)
    assert [c["term"] for c in fresh] == ["바람빛교회"]


def test_append_lands_in_the_review_section_and_touches_date_modified() -> None:
    text = NOTE.replace("<!-- plaud-glossary:auto -->\n", "<!-- plaud-glossary:auto -->\n\n## 끝\n")
    out = gl.append_entries(text, ["- **A** — 용어"], now_iso="2026-10-26T13:00")
    assert "<!-- plaud-glossary:auto -->\n- **A** — 용어\n\n## 끝" in out
    assert "date modified: 2026-10-26T13:00" in out
    assert out.count("## 인물") == 1


def test_append_creates_the_section_when_missing() -> None:
    out = gl.append_entries("# 노트\n", ["- **A** — 용어"])
    assert out.endswith(f"{gl.AUTO_HEADING}\n{gl.AUTO_MARKER}\n- **A** — 용어\n")


def test_harvest_appends_new_terms_once_per_recording(
    note: Path, storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = stub_model(monkeypatch, REPLY)
    report = gl.harvest(storage, "f1", model="codex")
    assert report.status == "added"
    assert [a["term"] for a in report.added] == ["바람빛교회"]
    text = note.read_text(encoding="utf-8")
    assert "- **바람빛교회** — 장소·기관 · 호칭: 바람빛 · 다니는 교회 · 출처: 가족 대화" in text
    assert "길동이 내일 바람빛 가?" in prompts[0]  # read the final transcript
    assert "**홍길동**" in prompts[0]  # and the current glossary

    again = gl.harvest(storage, "f1", model="codex")
    assert again.status == "skipped"
    assert len(prompts) == 1
    assert gl.harvest(storage, "f1", model="codex", force=True).status == "nothing-new"


def test_harvest_dry_run_writes_nothing(
    note: Path, storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_model(monkeypatch, REPLY)
    report = gl.harvest(storage, "f1", model="codex", dry_run=True)
    assert report.status == "added"
    assert note.read_text(encoding="utf-8") == NOTE
    assert not gl.STATE_FILE.exists()


def test_harvest_without_a_note_does_not_call_the_model(
    storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = stub_model(monkeypatch, REPLY)
    assert gl.harvest(storage, "f1", model="codex").status == "no-glossary"
    assert prompts == []


def test_harvest_failure_is_reported_not_raised(
    note: Path, storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.summarize as summarize

    monkeypatch.setattr(summarize, "model_available", lambda _m: True)

    def boom(*_a, **_k):
        raise summarize.ModelFailed("quota")

    monkeypatch.setattr(summarize, "run_model", boom)
    report = gl.harvest(storage, "f1", model="codex")
    assert report.status == "unavailable"
    assert not gl.STATE_FILE.exists()  # a later run retries
