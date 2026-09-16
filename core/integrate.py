"""Integrated summary: fuse CMDS (ElevenLabs) + Plaud transcripts and summaries.

CMDS gives accurate diarization but may miss/garble words.
Plaud gives better word coverage but worse speaker attribution.
The model is asked to produce BOTH a final transcript AND a summary. The
current template uses explicit BEGIN/END markers; the older default template
uses a single ``===TRANSCRIPT===`` separator. Both formats are supported so
existing slots keep working while the integrated template remains stricter.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .paths import integrated_paths
from .summarize import run_model
from .templates import load_template

_FT_RE = re.compile(
    r"===FINAL_TRANSCRIPT_BEGIN===\s*(.*?)\s*===FINAL_TRANSCRIPT_END===",
    re.DOTALL,
)
_SUM_RE = re.compile(
    r"===SUMMARY_BEGIN===\s*(.*?)\s*===SUMMARY_END===",
    re.DOTALL,
)


def split_integrated(raw: str) -> tuple[str, str]:
    """Pull the transcript / summary parts out of the model's combined output.

    Returns ``(transcript, summary)``. If explicit integrated markers are
    missing, fall back to the legacy ``===TRANSCRIPT===`` separator, where the
    summary is above the marker and the transcript is below it. If neither
    format appears, keep the model output as summary text and leave transcript
    empty so callers can still show the raw result.
    """
    ft = _FT_RE.search(raw)
    su = _SUM_RE.search(raw)
    if ft or su:
        transcript = ft.group(1).strip() if ft else ""
        summary = su.group(1).strip() if su else ""
        return transcript, summary

    if "===TRANSCRIPT===" in raw:
        summary, _, transcript = raw.partition("===TRANSCRIPT===")
        return transcript.strip(), summary.strip()

    return "", raw.strip()


def collect_inputs(storage: Any, file_id: str) -> dict[str, str]:
    """Assemble the CMDS/Plaud text inputs the integrated prompt needs.

    Mirrors what the `cmds-integrate` CLI builds; shared so the dual pipeline
    produces identical prompts.
    """
    import json

    cmds_text = ""
    speakers = ""
    cmds_row = storage.get_cmds_transcript(file_id)
    if cmds_row:
        segs = json.loads(cmds_row["segments"] or "[]")
        sp_set = sorted({s.get("speaker") for s in segs if s.get("speaker")})
        speakers = ", ".join(filter(None, sp_set))
        cmds_text = "\n".join(
            f"[{int(s.get('start_ms') or 0) / 1000:.1f}s] "
            f"{s.get('speaker') or ''}: {s.get('content') or ''}"
            for s in segs
        )

    plaud_text = ""
    plaud_summaries = ""
    title = ""
    keywords = ""
    content_row = storage.get_content_row(file_id)
    if content_row:
        title = content_row["title"] or ""
        keywords = ", ".join(json.loads(content_row["keywords"] or "[]")[:15])
        tr_segs = json.loads(content_row["transcript"] or "[]")
        plaud_text = "\n".join(
            f"[{int(s.get('start_time') or 0) / 1000:.1f}s] "
            f"{s.get('speaker') or ''}: {s.get('content') or ''}"
            for s in tr_segs
        )
        primary = content_row["summary_md"] or ""
        extras = json.loads(content_row["summary_extra"] or "[]")
        blocks = []
        if primary:
            blocks.append(f"### Primary\n\n{primary}")
        for i, body in enumerate(extras):
            blocks.append(f"### Template {i + 1}\n\n{body}")
        plaud_summaries = "\n\n".join(blocks) or "(없음)"

    return {
        "cmds_transcript": cmds_text,
        "plaud_transcript": plaud_text,
        "plaud_summaries": plaud_summaries,
        "title": title,
        "keywords": keywords,
        "speakers": speakers,
    }


def integrate(
    *,
    file_id: str,
    model: str,
    template_name: str,
    cmds_transcript: str,
    plaud_transcript: str,
    plaud_summaries: str,
    title: str = "",
    keywords: str = "",
    speakers: str = "",
    model_id: str = "",
    transcription_context: str = "",
) -> dict[str, Path]:
    """Run the integrated prompt and persist all/transcript/summary outputs."""
    template = load_template(template_name)
    prompt = template.render(
        title=title,
        keywords=keywords,
        speakers=speakers,
        cmds_transcript=cmds_transcript,
        plaud_transcript=plaud_transcript,
        plaud_summaries=plaud_summaries,
        transcription_context=transcription_context or "(없음)",
    )
    response = run_model(model, prompt, model_id=model_id or None)
    transcript, summary = split_integrated(response)

    output_model = model_id or model
    paths = integrated_paths(file_id, model=output_model, template=template_name)
    front = (
        f"---\nfile_id: {file_id}\nprovider: {model}\nmodel: {output_model}\n"
        f"template: {template_name}\nkind: integrated\n---\n\n"
    )
    paths["all"].write_text(front + response, encoding="utf-8")
    paths["transcript"].write_text(
        front.replace("kind: integrated", "kind: transcript")
        + (transcript or "(no transcript section)"),
        encoding="utf-8",
    )
    paths["summary"].write_text(
        front.replace("kind: integrated", "kind: summary") + (summary or "(no summary section)"),
        encoding="utf-8",
    )
    return paths
