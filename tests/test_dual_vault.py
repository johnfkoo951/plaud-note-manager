from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from core import dual_vault as dv
from core.models import FileContent, PlaudFile, SummaryBlock
from core.storage import Storage

NOW = int(time.time())


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core import app_config
    import core.paths as paths_mod

    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(app_config, "obsidian_vault", lambda: vault)
    monkeypatch.setattr(app_config, "author", lambda: "구요한")
    monkeypatch.setattr(paths_mod, "INTEGRATED_DIR", tmp_path / "integrated")

    storage = Storage(tmp_path / "plaud.db")
    storage.upsert_file(
        PlaudFile(id="f1", filename="결혼식 계획 논의", start_time=1754800000000), now=NOW
    )
    storage.save_content(
        FileContent(
            file_id="f1",
            title="결혼식 계획 논의",
            summaries=[SummaryBlock(kind="auto_sum_note", body_md="plaud 요약")],
            keywords=["결혼식"],
        ),
        now=NOW,
    )
    # Integrated artifacts + confirmed speaker map.
    paths = paths_mod.integrated_paths("f1", model="gpt-5.5", template="integrated")
    paths["summary"].write_text("통합 요약 본문", encoding="utf-8")
    paths["transcript"].write_text("[00:00:01] 구요한: 안녕하세요", encoding="utf-8")
    storage.upsert_dual(
        "f1",
        status="vault-ready",
        speaker_map=json.dumps({"speaker_0": "구요한", "speaker_1": "홍길동"}),
        now=NOW,
    )
    storage.set_reuse("f1", "newsletter", note="사례", now=NOW)
    return storage, vault


def test_lands_in_plaud_lane_with_conventions(env) -> None:
    storage, vault = env
    result = dv.send_dual_to_vault(storage, "f1", model="gpt-5.5")
    assert result.status == "ok"
    target = Path(result.path)
    assert target.parent == vault / dv.TRANSCRIPT_LANE
    assert target.name.endswith(".transcript.md")
    assert target.name.split("_")[0].isdigit() and len(target.name.split("_")[0]) == 8

    text = target.read_text(encoding="utf-8")
    assert "type: transcript" in text
    assert "dual: true" in text
    assert '"[[구요한]]"' in text and '"[[홍길동]]"' in text
    assert "reuse-channels:" in text and "newsletter:flagged" in text
    assert "## 개요" in text and "통합 요약 본문" in text
    assert "## 최종 전사본" in text and "[00:00:01] 구요한: 안녕하세요" in text
    assert "web.plaud.ai/file/f1" in text
    assert result.obsidian_url.startswith("obsidian://open?vault=")

    # note_references links the vault file back to the recording.
    refs = storage.list_note_references("f1")
    assert any(r["kind"] == "dual-transcript" for r in refs)


def test_duplicate_send_gets_numbered_filename(env) -> None:
    storage, vault = env
    first = dv.send_dual_to_vault(storage, "f1", model="gpt-5.5")
    second = dv.send_dual_to_vault(storage, "f1", model="gpt-5.5")
    assert second.status == "ok"
    assert first.path != second.path
    assert "(2)" in Path(second.path).name


def test_missing_transcript_is_no_content(env) -> None:
    storage, vault = env
    result = dv.send_dual_to_vault(storage, "f1", model="other-model")
    assert result.status == "no_content"


def test_missing_vault(env, monkeypatch: pytest.MonkeyPatch) -> None:
    storage, _ = env
    from core import app_config

    monkeypatch.setattr(app_config, "obsidian_vault", lambda: None)
    assert dv.send_dual_to_vault(storage, "f1", model="gpt-5.5").status == "no_vault"
