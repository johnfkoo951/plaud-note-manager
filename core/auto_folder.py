"""LLM-assisted folder arbitration on top of the deterministic taxonomy.

Policy (v0.8 auto-foldering upgrade):

1. `classify_snapshot` (rules) always runs first — cheap, offline, testable.
2. Only when the rule verdict is weak (confidence below
   `app_config.classify_llm_threshold()`, or the default bucket) and the
   configured model is reachable, the LLM is asked to pick **one folder from
   the taxonomy** (closed vocabulary — it cannot invent folders) and to add
   topic tags / keywords for vault linking.
3. The LLM answer is validated against the taxonomy; anything unknown falls
   back to the rule verdict. The rule verdict's own alternatives are shown to
   the model so it arbitrates rather than free-associates.

Folder *placement* stays the caller's decision (`plaud classify --apply`,
`metadata-generate` min-confidence gate); this module only decides.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import app_config
from .classification import (
    RecordingClassification,
    classification_from_rule,
    classify_snapshot,
    rule_by_folder,
    rule_cmds,
    taxonomy,
)
from .tags import normalize_tags
from .templates import load_template


@dataclass
class AssistedClassification:
    classification: RecordingClassification
    used_llm: bool = False
    llm_tags: list[str] = field(default_factory=list)
    llm_keywords: list[str] = field(default_factory=list)
    llm_error: str = ""


def needs_llm(classification: RecordingClassification, threshold: float) -> bool:
    return classification.source == "default" or classification.confidence < threshold


def folder_menu() -> str:
    """Closed list of folders the model may choose from."""
    lines = []
    for rule in taxonomy():
        cmds = rule_cmds(rule) or "-"
        sample = ", ".join(rule.include[:6])
        hint = f" — {rule.hint}" if rule.hint else ""
        lines.append(
            f"- {rule.folder_name} | type={rule.note_type} | CMDS={cmds}{hint} | 예: {sample}"
        )
    return "\n".join(lines)


def _extract_json(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            candidate = text[start : end + 1]
    if not candidate:
        return {}
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _as_str_list(value: Any, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [v for v in re.split(r"[,，;\n]", value)]
    if not isinstance(value, list):
        return []
    out = [str(v).strip() for v in value if str(v).strip()]
    return out[:limit]


def classify_with_assist(
    snapshot: dict[str, Any],
    *,
    model: str | None = None,
    threshold: float | None = None,
    use_llm: bool | None = None,
    run_model=None,
    model_available=None,
) -> AssistedClassification:
    """Rules first; LLM arbitration only for weak verdicts."""
    rule_result = classify_snapshot(snapshot)
    threshold = app_config.classify_llm_threshold() if threshold is None else threshold
    use_llm = app_config.classify_llm_assist() if use_llm is None else use_llm
    if not use_llm or not needs_llm(rule_result, threshold):
        return AssistedClassification(rule_result)

    if run_model is None or model_available is None:
        from . import summarize

        run_model = run_model or summarize.run_model
        model_available = model_available or summarize.model_available
    model = model or app_config.classify_model()
    if not model_available(model):
        return AssistedClassification(rule_result, llm_error=f"{model} unavailable")

    summaries = str(snapshot.get("summaries") or "")[:3000]
    integrated = str(snapshot.get("integrated_summary") or "")[:3000]
    transcript = str(snapshot.get("cmds_transcript") or snapshot.get("transcript") or "")[:2500]
    prompt = load_template("classify").render(
        title=str(snapshot.get("title") or ""),
        keywords=", ".join(str(k) for k in (snapshot.get("keywords") or [])),
        speakers=str(snapshot.get("speakers") or ""),
        plaud_summaries=summaries,
        integrated_summary=integrated,
        transcript=transcript,
        folder_menu=folder_menu(),
        rule_folder=rule_result.folder_name,
        rule_confidence=f"{rule_result.confidence:.2f}",
        rule_reason=rule_result.reason,
        rule_alternatives=", ".join(rule_result.alternatives) or "-",
    )
    try:
        raw = run_model(model, prompt, model_id=app_config.model_id_for(model) or None)
    except Exception as exc:  # heterogeneous runner errors
        return AssistedClassification(rule_result, llm_error=str(exc)[:300])

    data = _extract_json(raw)
    folder_name = str(data.get("folder_name") or "").strip()
    rule = rule_by_folder(folder_name) if folder_name else None
    llm_tags = normalize_tags(_as_str_list(data.get("topic_tags"), 10))
    llm_keywords = _as_str_list(data.get("keywords"), 12)
    if rule is None:
        return AssistedClassification(
            rule_result,
            used_llm=True,
            llm_tags=llm_tags,
            llm_keywords=llm_keywords,
            llm_error=f"unknown folder from model: {folder_name!r}" if folder_name else "no folder",
        )
    try:
        confidence = float(data.get("confidence", 0.7))
    except (TypeError, ValueError):
        confidence = 0.7
    # The model must earn placement: never above 0.9 (rules can reach 0.95).
    confidence = min(0.9, max(0.5, confidence))
    reason = str(data.get("reason") or "").strip()[:160]
    classification = classification_from_rule(
        rule,
        keywords=list(snapshot.get("keywords") or []),
        extra_tags=llm_tags,
        confidence=confidence,
        reason=f"llm: {reason}" if reason else "llm arbitration",
        source="llm",
        alternatives=tuple(
            a for a in (rule_result.folder_name, *rule_result.alternatives) if a != rule.folder_name
        )[:3],
    )
    return AssistedClassification(
        classification, used_llm=True, llm_tags=llm_tags, llm_keywords=llm_keywords
    )
