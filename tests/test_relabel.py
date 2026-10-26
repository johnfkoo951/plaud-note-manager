from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from core import relabel as rl
from core.models import PlaudFile
from core.storage import Storage

NOW = int(time.time())

FINAL = """---
file_id: f1
provider: claude
model: claude-x
template: integrated
kind: transcript
---

[00:00:00] speaker_0: 길동아, speaker_1 말 들었어?
[00:00:09] speaker_1: 응. 홍길동 얘기 맞지?
[00:00:11] speaker_0: 그래."""

SUMMARY = """---
kind: summary
---

## 한 줄 요약
speaker_0과 speaker_1이 홍길동의 학교 이야기를 나눴다.

## 인용할 만한 발언
- speaker_1: "괜찮아."
- **홍길동**: "다음 주에 보자."
"""


@pytest.fixture()
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Storage:
    import core.paths as paths_mod

    monkeypatch.setattr(paths_mod, "INTEGRATED_DIR", tmp_path / "integrated")
    s = Storage(tmp_path / "plaud.db")
    s.upsert_file(PlaudFile(id="f1", filename="rec.m4a"), now=NOW)
    s.save_cmds_transcript(
        file_id="f1",
        model="scribe_v1",
        language="ko",
        text="…",
        segments_json=json.dumps(
            [
                {"start_ms": 0, "end_ms": 900, "speaker": "speaker_0", "content": "a"},
                {"start_ms": 1000, "end_ms": 1900, "speaker": "speaker_1", "content": "b"},
                {"start_ms": 2000, "end_ms": 2900, "speaker": "speaker_0", "content": "c"},
            ]
        ),
        now=NOW,
    )
    base = tmp_path / "integrated" / "f1"
    base.mkdir(parents=True)
    (base / "claude-x__integrated.transcript.md").write_text(FINAL, encoding="utf-8")
    (base / "claude-x__integrated.summary.md").write_text(SUMMARY, encoding="utf-8")
    return s


def test_placeholder_detection() -> None:
    for label in ("speaker_0", "Speaker 3", "spk_1", "화자 2", "화자2"):
        assert rl.is_placeholder(label), label
    for label in ("홍길동", "speaker", "Speaker A", ""):
        assert not rl.is_placeholder(label), label


def test_transcript_labels_change_but_not_the_words_spoken() -> None:
    out, n = rl.relabel_text(FINAL, {"speaker_0": "홍길동", "speaker_1": "김철수"})
    lines = out.splitlines()
    assert "[00:00:00] 홍길동: 길동아, 김철수 말 들었어?" in lines  # placeholder in prose follows
    assert "[00:00:09] 김철수: 응. 홍길동 얘기 맞지?" in lines
    assert out.startswith("---\nfile_id: f1\n")  # front matter untouched
    assert n == 4


def test_mapping_applies_simultaneously_so_swaps_work() -> None:
    text = "[00:00:00] 홍길동: 하나\n[00:00:01] 김철수: 둘"
    out, _ = rl.relabel_text(text, {"홍길동": "김철수", "김철수": "홍길동"})
    assert out == "[00:00:00] 김철수: 하나\n[00:00:01] 홍길동: 둘"


def test_real_name_changes_only_where_it_attributes_a_line() -> None:
    out, _ = rl.relabel_text(SUMMARY, {"홍길동": "김철수", "speaker_0": "이영희"})
    assert "이영희과 speaker_1이 홍길동의 학교 이야기를 나눴다." in out  # prose name kept
    assert '- **김철수**: "다음 주에 보자."' in out


def test_blank_and_identity_pairs_are_ignored() -> None:
    assert rl.clean_mapping({"speaker_0": "", "speaker_1": "speaker_1", " a ": " b "}) == {"a": "b"}
    assert rl.relabel_text(FINAL, {"speaker_0": ""}) == (FINAL, 0)


def test_relabel_recording_rewrites_cmds_and_every_final(storage: Storage) -> None:
    report = rl.relabel_recording(storage, "f1", {"speaker_0": "홍길동", "speaker_1": "김철수"})
    assert report.cmds_segments == 3
    assert len(report.files) == 2
    segs = json.loads(storage.get_cmds_transcript("f1")["segments"])
    assert [s["speaker"] for s in segs] == ["홍길동", "김철수", "홍길동"]
    final = Path(report.files[0]["path"]).parent / "claude-x__integrated.transcript.md"
    assert "[00:00:11] 홍길동: 그래." in final.read_text(encoding="utf-8")
    assert {r["name"] for r in storage.list_speakers()} >= {"홍길동", "김철수"}


def test_renaming_again_composes_the_stored_map(storage: Storage) -> None:
    storage.upsert_dual("f1", status="vault-ready", now=NOW)
    rl.relabel_recording(storage, "f1", {"speaker_0": "홍길동", "speaker_1": "김철수"})
    rl.relabel_recording(storage, "f1", {"홍길동": "김철수", "김철수": "홍길동"})
    stored = json.loads(storage.get_dual("f1")["speaker_map"])
    assert stored == {"speaker_0": "김철수", "speaker_1": "홍길동"}
    segs = json.loads(storage.get_cmds_transcript("f1")["segments"])
    assert [s["speaker"] for s in segs] == ["김철수", "홍길동", "김철수"]


def test_relabel_keeps_an_existing_pipeline_error(storage: Storage) -> None:
    storage.upsert_dual("f1", status="vault-ready", error="quota", now=NOW)
    rl.relabel_recording(storage, "f1", {"speaker_0": "홍길동"})
    assert storage.get_dual("f1")["error"] == "quota"
