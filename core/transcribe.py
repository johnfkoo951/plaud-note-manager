"""ElevenLabs Scribe transcription pipeline.

Workflow:
  1. Resolve Plaud temp_url for the file.
  2. Stream audio into a tempfile (mp3 for ElevenLabs compatibility).
  3. POST multipart to /v1/speech-to-text with diarize=true.
  4. Group word-level results into speaker segments.
  5. Persist into cmds_transcripts. Delete tempfile.

The API key is loaded from `ELEVENLABS_API_KEY` env var, falling back
to a regex parse of `~/.zshrc` so users with the key in their shell
profile don't need to duplicate it into our `.env`.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx

from .client import PlaudClient
from .config import PlaudConfig


def load_elevenlabs_key() -> str | None:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if key:
        return key
    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
        text = zshrc.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'export\s+ELEVENLABS_API_KEY\s*=\s*["\']?([^"\'\s]+)', text)
        if m:
            return m.group(1)
    return None


def transcribe_file(
    cfg: PlaudConfig,
    file_id: str,
    *,
    diarize: bool = True,
    model_id: str = "scribe_v1",
    language_code: str | None = None,
    num_speakers: int | None = None,
) -> dict[str, Any]:
    """Run the full pipeline for one Plaud file. Returns the structured result."""
    api_key = load_elevenlabs_key()
    if not api_key:
        raise RuntimeError(
            "ELEVENLABS_API_KEY not found in env or ~/.zshrc. "
            "Add `export ELEVENLABS_API_KEY=...` to your shell profile."
        )

    tmp_path: Path | None = None
    try:
        with PlaudClient(cfg) as client:
            # Use the regular (mp3) URL for broad codec compatibility.
            temp_resp = client._get_json(f"/file/temp-url/{file_id}")
            url = temp_resp.get("temp_url") or temp_resp.get("temp_url_opus")
            if not url:
                raise RuntimeError(f"missing temp_url for {file_id}")

            with tempfile.NamedTemporaryFile(
                prefix=f"plaud_{file_id}_", suffix=".mp3", delete=False
            ) as tmp:
                tmp_path = Path(tmp.name)
                with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
                    r.raise_for_status()
                    for chunk in r.iter_bytes():
                        tmp.write(chunk)

        with httpx.Client(timeout=600) as client:
            with tmp_path.open("rb") as f:
                files = {"file": (tmp_path.name, f, "audio/mpeg")}
                data: dict[str, str] = {
                    "model_id": model_id,
                    "diarize": "true" if diarize else "false",
                    "timestamps_granularity": "word",
                }
                if language_code:
                    data["language_code"] = language_code
                if num_speakers and num_speakers > 0:
                    data["num_speakers"] = str(num_speakers)
                resp = client.post(
                    "https://api.elevenlabs.io/v1/speech-to-text",
                    headers={"xi-api-key": api_key},
                    files=files,
                    data=data,
                )
            resp.raise_for_status()
            payload = resp.json()
    finally:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)

    segments = group_words_into_segments(payload.get("words") or [])
    return {
        "file_id": file_id,
        "model": model_id,
        "language": payload.get("language_code"),
        "language_probability": payload.get("language_probability"),
        "text": payload.get("text") or "",
        "segments": segments,
        "raw_words_count": len(payload.get("words") or []),
    }


def group_words_into_segments(
    words: list[dict[str, Any]], *, max_silence_s: float = 1.5
) -> list[dict[str, Any]]:
    """Coalesce word events into speaker-bounded segments."""
    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for w in words:
        if w.get("type") not in (None, "word", "spacing"):
            continue
        text = w.get("text") or ""
        speaker = w.get("speaker_id") or w.get("speaker") or "speaker_0"
        start = float(w.get("start") or 0)
        end = float(w.get("end") or start)

        gap = start - (current["end_ms"] / 1000.0) if current else 0.0
        if current is None or current["speaker"] != speaker or gap > max_silence_s:
            if current is not None:
                segments.append(current)
            current = {
                "speaker": speaker,
                "start_ms": int(start * 1000),
                "end_ms": int(end * 1000),
                "content": text.strip(),
            }
        else:
            current["end_ms"] = int(end * 1000)
            current["content"] = (current["content"] + " " + text).strip()

    if current is not None:
        segments.append(current)
    return segments
