from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from core import dual_vault as dv
from core.models import FileContent, PlaudFile, SummaryBlock, TranscriptSegment
from core.storage import Storage
from core.vault_index import related_notes as _real_related_notes  # before fixtures stub it

NOW = int(time.time())


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core import app_config, vault_index
    import core.paths as paths_mod

    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(app_config, "obsidian_vault", lambda: vault)
    monkeypatch.setattr(app_config, "author", lambda: "구요한")
    monkeypatch.setattr(paths_mod, "INTEGRATED_DIR", tmp_path / "integrated")
    # note_context looks up related notes, which opens the real data/plaud.db.
    monkeypatch.setattr(vault_index, "related_notes", lambda *a, **k: [])

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


def test_transcript_note_never_wikilinks_wiki_pages(env, monkeypatch, tmp_path, index_note) -> None:
    from core import vault_index

    storage, vault = env
    monkeypatch.setattr(vault_index, "related_notes", _real_related_notes)
    wiki = tmp_path / "CMDS_LLM_Wiki"
    wiki.mkdir()
    monkeypatch.setenv("PLAUD_WIKI_VAULT", str(wiki))
    keyword = json.loads(storage.get_content_row("f1")["keywords"])[0]
    index_note(vault, f"Life/{keyword}.md")
    index_note(wiki, f"20. Wiki/{keyword}.md", note_type="concept")
    result = dv.send_dual_to_vault(storage, "f1", model="gpt-5.5")
    text = Path(result.path).read_text(encoding="utf-8")
    assert f'related:\n  - "[[{keyword}]]"\nwikiVaultRelated:\n' in text
    assert f"[LLM Wiki: {keyword}](obsidian://advanced-uri?vault=CMDS_LLM_Wiki" in text
    assert text.count(f"[[{keyword}]]") == 2  # front matter + body, both the main-vault note


def _unmapped(storage: Storage, plaud_speakers: list[str]) -> None:
    """State after `dual --continue-unmapped`: no confirmed map, so the CMDS
    transcript still carries the diarizer's own labels."""
    storage.save_content(
        FileContent(
            file_id="f1",
            title="결혼식 계획 논의",
            transcript=[
                TranscriptSegment(start_time=i, end_time=i + 1, speaker=s, content="…")
                for i, s in enumerate(plaud_speakers)
            ],
        ),
        now=NOW,
    )
    storage.save_cmds_transcript(
        file_id="f1",
        model="scribe",
        language="ko",
        text="…",
        segments_json=json.dumps([{"speaker": "speaker_0"}, {"speaker": "speaker_1"}]),
        now=NOW,
    )
    storage.upsert_dual("f1", status="vault-ready", speaker_map=json.dumps({}), now=NOW)


def test_unmapped_dual_note_keeps_snapshot_names_and_drops_placeholders(env) -> None:
    storage, _ = env
    _unmapped(storage, ["홍길동", "Speaker 2"])
    result = dv.send_dual_to_vault(storage, "f1", model="gpt-5.5")
    assert result.status == "ok"
    text = Path(result.path).read_text(encoding="utf-8")
    # Plaud already knew this name; the author (구요한) is not a speaker here.
    assert '"[[홍길동]]"' in text
    assert "> 참석자: [[홍길동]]\n" in text
    for label in ("[[speaker_0]]", "[[speaker_1]]", "[[Speaker 2]]"):
        assert label not in text


def test_unmapped_dual_note_with_only_placeholders_lists_no_speakers(env) -> None:
    storage, _ = env
    _unmapped(storage, ["Speaker 1"])
    result = dv.send_dual_to_vault(storage, "f1", model="gpt-5.5")
    assert result.status == "ok"
    text = Path(result.path).read_text(encoding="utf-8")
    assert "\nspeakers:" not in text
    assert "참석자" not in text


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
