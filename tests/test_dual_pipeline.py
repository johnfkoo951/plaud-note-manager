from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from core import dual_pipeline as dp
from core.models import FileContent, PlaudFile, SummaryBlock, TranscriptSegment
from core.storage import Storage

NOW = int(time.time())
SEGS = [
    {"start_ms": 0, "end_ms": 900, "speaker": "speaker_0", "content": "안녕하세요 구요한입니다"},
    {"start_ms": 1000, "end_ms": 1900, "speaker": "speaker_1", "content": "네 반갑습니다"},
]


@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "plaud.db")
    s.upsert_file(PlaudFile(id="f1", filename="rec.m4a"), now=NOW)
    s.save_content(
        FileContent(
            file_id="f1",
            title="회의",
            transcript=[
                TranscriptSegment(start_time=0, end_time=900, speaker="Speaker 1", content="안녕")
            ],
            summaries=[SummaryBlock(kind="auto_sum_note", body_md="요약")],
        ),
        now=NOW,
    )
    return s


@pytest.fixture()
def pipeline_stubs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Stub every external effect: ElevenLabs, LLM integrate, metadata.

    Integrated artifacts are redirected under tmp_path so tests never touch
    the repo's real data/integrated/.
    """
    import core.paths as paths_mod

    monkeypatch.setattr(paths_mod, "INTEGRATED_DIR", tmp_path / "integrated")
    calls: dict[str, int] = {"transcribe": 0, "integrate": 0, "metadata": 0}

    def fake_transcribe(storage: Storage, file_id: str) -> None:
        calls["transcribe"] += 1
        storage.save_cmds_transcript(
            file_id=file_id,
            model="scribe_v1",
            language="ko",
            text="…",
            segments_json=json.dumps(SEGS, ensure_ascii=False),
            now=int(time.time()),
        )

    def fake_integrate(storage: Storage, file_id: str, *, model: str, model_id: str) -> None:
        calls["integrate"] += 1
        from core.paths import integrated_paths

        paths = integrated_paths(file_id, model=model_id or model, template=dp.DUAL_TEMPLATE)
        paths["summary"].write_text("summary", encoding="utf-8")
        paths["transcript"].write_text("transcript", encoding="utf-8")

    def fake_metadata(storage: Storage, file_id: str, *, model: str = "", **kw):
        calls["metadata"] += 1
        return {}

    monkeypatch.setattr(dp, "_run_transcribe", fake_transcribe)
    monkeypatch.setattr(dp, "_run_integrate", fake_integrate)
    import core.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod, "generate_note_metadata", fake_metadata)
    return calls


def test_pauses_at_relabel_pending_with_proposal(storage: Storage, pipeline_stubs) -> None:
    report = dp.advance(storage, "f1", model="codex")
    assert report.status == "relabel-pending"
    assert pipeline_stubs["transcribe"] == 1
    assert {p["speaker"] for p in report.proposal} == {"speaker_0", "speaker_1"}
    assert pipeline_stubs["integrate"] == 0  # paused before integration

    row = storage.get_dual("f1")
    assert row["status"] == "relabel-pending"
    assert json.loads(row["speaker_proposal"])


def test_map_resumes_through_vault_ready(storage: Storage, pipeline_stubs) -> None:
    dp.advance(storage, "f1", model="codex")  # pause
    report = dp.advance(
        storage,
        "f1",
        model="codex",
        speaker_map={"speaker_0": "구요한", "speaker_1": "홍길동"},
    )
    assert report.status == "vault-ready"
    assert "integrated" in report.steps
    # Real names applied to the CMDS transcript.
    segs = json.loads(storage.get_cmds_transcript("f1")["segments"])
    assert {s["speaker"] for s in segs} == {"구요한", "홍길동"}
    # Confirmed names learned into the saved-speakers roster.
    assert {r["name"] for r in storage.list_speakers()} == {"구요한", "홍길동"}
    # Re-running is a no-op (idempotent).
    again = dp.advance(storage, "f1", model="codex")
    assert again.status == "vault-ready"
    assert pipeline_stubs["transcribe"] == 1
    assert pipeline_stubs["integrate"] == 1


def test_failure_parks_at_stable_status_and_retries(
    storage: Storage, pipeline_stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(storage, file_id, *, model, model_id):
        raise RuntimeError("model down")

    monkeypatch.setattr(dp, "_run_integrate", boom)
    dp.advance(storage, "f1", model="codex")
    report = dp.advance(storage, "f1", model="codex", speaker_map={"speaker_0": "구요한"})
    assert report.error == "model down"
    assert storage.get_dual("f1")["error"] == "model down"

    # Fix the model; the retry picks up from integration.
    def ok(storage, file_id, *, model, model_id):
        from core.paths import integrated_paths

        paths = integrated_paths(file_id, model=model_id or model, template=dp.DUAL_TEMPLATE)
        paths["summary"].write_text("s", encoding="utf-8")
        paths["transcript"].write_text("t", encoding="utf-8")

    monkeypatch.setattr(dp, "_run_integrate", ok)
    report2 = dp.advance(storage, "f1", model="codex")
    assert report2.status == "vault-ready"
    assert storage.get_dual("f1")["error"] is None


def test_auto_approve_uses_confident_proposals(
    storage: Storage, pipeline_stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        dp,
        "_proposal",
        lambda storage, file_id: [
            {"speaker": "speaker_0", "name": "구요한", "confidence": 0.9, "evidence": "자기소개"},
            {"speaker": "speaker_1", "name": "박준", "confidence": 0.4, "evidence": "추정"},
        ],
    )
    report = dp.advance(storage, "f1", model="codex", auto_approve=True)
    assert report.status == "vault-ready"
    segs = json.loads(storage.get_cmds_transcript("f1")["segments"])
    speakers = {s["speaker"] for s in segs}
    assert "구요한" in speakers  # 0.9 applied
    assert "speaker_1" in speakers  # 0.4 below threshold, untouched
