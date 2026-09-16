"""Dual-transcribe pipeline: one click from "important recording" to vault.

State machine (PLAN-v0.7 §2), persisted in the `dual_pipeline` table:

    marked -> transcribing -> relabel-pending ⏸ -> integrating -> vault-ready ⏸ -> vault-sent

`advance()` is idempotent and resumable: it inspects what already exists
(content cache, CMDS transcript, speaker map, integrated output) and runs only
the missing stages, pausing at `relabel-pending` until a speaker map is
supplied or approved. Errors park the row at the last stable status with the
error recorded, so a re-run retries just the failed stage.

Human checkpoints:
- relabel-pending: the ONLY required one — confirm speaker real names.
- vault-ready:     optional — `to_vault=True` (or `plaud dual-send`) lands the
                   note in the vault's transcript inbox lane.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .storage import Storage

# Ordered stable statuses (for progress display).
STATUSES = [
    "marked",
    "transcribing",
    "relabel-pending",
    "integrating",
    "vault-ready",
    "vault-sent",
]

DUAL_TEMPLATE = "integrated"


@dataclass
class DualReport:
    file_id: str
    status: str = "marked"
    detail: str = ""
    steps: list[str] = field(default_factory=list)  # what this run actually did
    proposal: list[dict[str, Any]] = field(default_factory=list)  # relabel-pending payload
    vault_path: str = ""
    error: str = ""


def mark(storage: Storage, file_id: str) -> None:
    if storage.get_dual(file_id) is None:
        storage.upsert_dual(file_id, status="marked", now=int(time.time()))


def unmark(storage: Storage, file_id: str) -> None:
    storage.delete_dual(file_id)


def _set(storage: Storage, file_id: str, status: str, **kw: Any) -> None:
    storage.upsert_dual(file_id, status=status, now=int(time.time()), **kw)


def _ensure_content(storage: Storage, file_id: str) -> None:
    if storage.get_content_row(file_id):
        return
    from .client import PlaudClient
    from .config import load_config

    cfg = load_config()
    with PlaudClient(cfg) as client:
        content = client.file_content(file_id)
    storage.save_content(content, now=int(time.time()))


def _run_transcribe(storage: Storage, file_id: str) -> None:
    from .config import load_config
    from .transcribe import transcribe_file

    cfg = load_config()
    result = transcribe_file(cfg, file_id, diarize=True)
    storage.save_cmds_transcript(
        file_id=file_id,
        model=result["model"],
        language=result.get("language"),
        text=result.get("text") or "",
        segments_json=json.dumps(result.get("segments") or [], ensure_ascii=False),
        now=int(time.time()),
    )


def cmds_speakers(storage: Storage, file_id: str) -> list[str]:
    row = storage.get_cmds_transcript(file_id)
    if not row:
        return []
    segs = json.loads(row["segments"] or "[]")
    return sorted({s.get("speaker") for s in segs if s.get("speaker")})


def apply_speaker_map(storage: Storage, file_id: str, mapping: dict[str, str]) -> int:
    """Rename speakers across the whole CMDS transcript. Returns segments touched."""
    row = storage.get_cmds_transcript(file_id)
    if not row:
        return 0
    segs = json.loads(row["segments"] or "[]")
    touched = 0
    for s in segs:
        if s.get("speaker") in mapping and mapping[s["speaker"]]:
            s["speaker"] = mapping[s["speaker"]]
            touched += 1
    if touched:
        storage.update_cmds_segments(
            file_id, row["model"], json.dumps(segs, ensure_ascii=False), now=int(time.time())
        )
    return touched


def _remember_speakers(storage: Storage, mapping: dict[str, str]) -> None:
    """Feed confirmed real names into the saved-speakers roster (best effort)."""
    existing = {r["name"] for r in storage.list_speakers()}
    for name in mapping.values():
        name = (name or "").strip()
        if name and name not in existing and not name.lower().startswith("speaker"):
            try:
                storage.add_speaker(name=name, now=int(time.time()))
                existing.add(name)
            except Exception:
                pass


def _has_integrated(file_id: str, *, model: str) -> bool:
    from .paths import integrated_paths

    paths = integrated_paths(file_id, model=model, template=DUAL_TEMPLATE)
    return paths["summary"].exists() and paths["transcript"].exists()


def _run_integrate(storage: Storage, file_id: str, *, model: str, model_id: str) -> None:
    from .dual_speakers import _hint, load_transcription_context
    from .integrate import collect_inputs, integrate

    inputs = collect_inputs(storage, file_id)
    if not inputs["cmds_transcript"] and not inputs["plaud_transcript"]:
        raise RuntimeError("no transcript available for integration")
    integrate(
        file_id=file_id,
        model=model,
        template_name=DUAL_TEMPLATE,
        model_id=model_id,
        # Roster + misrecognition table so the fused transcript corrects
        # names ("홍길둥" → 홍길동) instead of propagating them.
        transcription_context=load_transcription_context(hint=_hint(storage, file_id)),
        **inputs,
    )


def _proposal(storage: Storage, file_id: str) -> list[dict[str, Any]]:
    """Speaker-name proposal for the confirmation step.

    Delegates to the LLM-backed proposer (dual_speakers) when possible; falls
    back to a bare identity list so the pipeline still pauses with something
    reviewable when the model is unavailable.
    """
    try:
        from .dual_speakers import propose_speaker_names

        proposal = propose_speaker_names(storage, file_id)
        if proposal:
            return proposal
    except Exception:
        pass
    return [
        {"speaker": sp, "name": "", "confidence": 0.0, "evidence": ""}
        for sp in cmds_speakers(storage, file_id)
    ]


def advance(
    storage: Storage,
    file_id: str,
    *,
    speaker_map: dict[str, str] | None = None,
    auto_approve: bool = False,
    to_vault: bool = False,
    model: str = "",
    model_id: str = "",
    progress: Callable[[str], None] = lambda msg: None,
) -> DualReport:
    """Run every stage that can run right now; stop at human checkpoints."""
    from . import app_config

    model = model or app_config.metadata_model()
    model_id = model_id or app_config.model_id_for(model) or ""
    report = DualReport(file_id=file_id)
    mark(storage, file_id)

    try:
        # -- content + ElevenLabs transcript ------------------------------
        _ensure_content(storage, file_id)
        if storage.get_cmds_transcript(file_id) is None:
            _set(storage, file_id, "transcribing")
            progress("transcribing via ElevenLabs…")
            _run_transcribe(storage, file_id)
            report.steps.append("transcribed")

        # -- speaker naming checkpoint ------------------------------------
        row = storage.get_dual(file_id)
        stored_map: dict[str, str] = json.loads(row["speaker_map"] or "{}") if row else {}
        if speaker_map:
            stored_map = {**stored_map, **speaker_map}

        if not stored_map:
            proposal = json.loads(row["speaker_proposal"] or "[]") if row else []
            if not proposal:
                progress("proposing speaker names…")
                proposal = _proposal(storage, file_id)
                _set(
                    storage,
                    file_id,
                    "relabel-pending",
                    speaker_proposal=json.dumps(proposal, ensure_ascii=False),
                )
                report.steps.append("proposed-speakers")
            if auto_approve:
                stored_map = {
                    p["speaker"]: p["name"]
                    for p in proposal
                    if p.get("name") and float(p.get("confidence") or 0) >= 0.7
                }
            if not stored_map:
                _set(storage, file_id, "relabel-pending")
                report.status = "relabel-pending"
                report.proposal = proposal
                report.detail = (
                    "speaker names need confirmation — approve/edit the proposal "
                    "(plaud dual <id> --map speaker_0=이름 … or the app sheet)"
                )
                return report

        # -- apply real names ---------------------------------------------
        touched = apply_speaker_map(storage, file_id, stored_map)
        if touched:
            report.steps.append(f"relabeled-{touched}")
        _remember_speakers(storage, stored_map)
        _set(
            storage,
            file_id,
            "integrating",
            speaker_map=json.dumps(stored_map, ensure_ascii=False),
        )

        # -- cross-analysis final transcript ------------------------------
        if not _has_integrated(file_id, model=model_id or model):
            progress(f"integrating Plaud × ElevenLabs with {model}…")
            _run_integrate(storage, file_id, model=model, model_id=model_id)
            report.steps.append("integrated")

        # -- metadata (best effort — never blocks the pipeline) -----------
        try:
            from .metadata import generate_note_metadata

            generate_note_metadata(storage, file_id, model=model)
            report.steps.append("metadata")
        except Exception as exc:
            progress(f"metadata generation skipped: {exc}")

        _set(storage, file_id, "vault-ready")
        report.status = "vault-ready"

        # -- vault landing (optional checkpoint) --------------------------
        if to_vault:
            from .dual_vault import send_dual_to_vault

            progress("sending to vault transcript inbox…")
            result = send_dual_to_vault(storage, file_id, model=model_id or model)
            if result.status != "ok":
                raise RuntimeError(f"vault send failed: {result.detail}")
            _set(storage, file_id, "vault-sent", vault_path=result.path)
            storage.update_usage_status(file_id, "vault-linked", now=int(time.time()))
            report.status = "vault-sent"
            report.vault_path = result.path
            report.steps.append("vault-sent")
        return report

    except Exception as exc:
        current = storage.get_dual(file_id)
        stable = current["status"] if current else "marked"
        storage.upsert_dual(file_id, status=stable, error=str(exc), now=int(time.time()))
        report.status = stable
        report.error = str(exc)
        return report
