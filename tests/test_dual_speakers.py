from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from core import dual_speakers as ds
from core.models import FileContent, PlaudFile, TranscriptSegment
from core.storage import Storage

NOW = int(time.time())

CONTEXT_MD = """---
date created: 2026-03-29
---
# Transcription Context (Compact)

## 1. 핵심 인물 (CMDSPACE)

```
구요한 — 커맨드스페이스 대표
홍길동 — 교사 (오류: 홍길둥)
```

## 2. LG 교육 담당자

```
이경채 — LG 인화원
```

## 3. 재즈 뮤지션

```
KoN — 바이올린
```
"""


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ctx = tmp_path / ds.CONTEXT_RELPATH
    ctx.parent.mkdir(parents=True)
    ctx.write_text(CONTEXT_MD, encoding="utf-8")
    from core import app_config

    monkeypatch.setattr(app_config, "obsidian_vault", lambda: tmp_path)
    return tmp_path


def test_context_always_includes_roster_and_ranks_by_hint(vault: Path) -> None:
    out = ds.load_transcription_context(hint="LG 인화원 임원교육")
    assert "구요한" in out  # §1 always in
    assert "이경채" in out  # hint-matched block
    assert "KoN" not in out  # unmatched block dropped when hints match


def test_context_without_hint_still_carries_some_blocks(vault: Path) -> None:
    out = ds.load_transcription_context(hint="")
    assert "구요한" in out
    # general fallback keeps a couple of extra blocks
    assert "이경채" in out


def test_context_missing_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import app_config

    monkeypatch.setattr(app_config, "obsidian_vault", lambda: None)
    assert ds.load_transcription_context() == ""


def test_parse_proposal_tolerates_fences_and_junk() -> None:
    raw = 'Here you go:\n```json\n[{"speaker":"speaker_0","name":"구요한","confidence":0.9,"evidence":"[0:12] 자기소개"}]\n```'
    parsed = ds.parse_proposal(raw)
    assert parsed == [
        {"speaker": "speaker_0", "name": "구요한", "confidence": 0.9, "evidence": "[0:12] 자기소개"}
    ]
    assert ds.parse_proposal("no json here") == []


def test_propose_aligns_to_actual_speaker_set(
    tmp_path: Path, vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = Storage(tmp_path / "plaud.db")
    storage.upsert_file(PlaudFile(id="f1", filename="LG 회의"), now=NOW)
    storage.save_content(
        FileContent(
            file_id="f1",
            title="LG 회의",
            transcript=[
                TranscriptSegment(
                    start_time=0, end_time=900, speaker="구요한", content="안녕하세요"
                )
            ],
        ),
        now=NOW,
    )
    storage.save_cmds_transcript(
        file_id="f1",
        model="scribe_v1",
        language="ko",
        text="…",
        segments_json=json.dumps(
            [
                {"start_ms": 0, "speaker": "speaker_0", "content": "안녕하세요 구요한입니다"},
                {"start_ms": 1000, "speaker": "speaker_1", "content": "네"},
            ]
        ),
        now=NOW,
    )

    from core import summarize

    monkeypatch.setattr(summarize, "model_available", lambda m: True)
    # Model answers only speaker_0 and hallucinates speaker_9.
    monkeypatch.setattr(
        summarize,
        "run_model",
        lambda m, prompt, model_id=None, **kw: json.dumps(
            [
                {
                    "speaker": "speaker_0",
                    "name": "구요한",
                    "confidence": 0.9,
                    "evidence": "자기소개",
                },
                {"speaker": "speaker_9", "name": "유령", "confidence": 0.9, "evidence": ""},
            ]
        ),
    )
    proposal = ds.propose_speaker_names(storage, "f1", model="codex")
    assert [p["speaker"] for p in proposal] == ["speaker_0", "speaker_1"]
    assert proposal[0]["name"] == "구요한"
    assert proposal[1]["name"] == ""  # gap filled, ghost dropped
