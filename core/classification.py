"""Deterministic recording taxonomy for Plaud folders and metadata."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .tags import normalize_tags


@dataclass(frozen=True)
class FolderRule:
    folder_name: str
    note_type: str
    cmds_category: str
    tags: tuple[str, ...]
    include: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    priority: int = 50


@dataclass(frozen=True)
class RecordingClassification:
    folder_name: str
    note_type: str
    cmds_category: str
    tags: list[str]
    confidence: float
    reason: str


# Generic, PII-free default taxonomy shipped with the project. The owner's
# personal rules are NOT here — a user can override the whole set with a
# gitignored data/classification.json (see load_taxonomy below).
DEFAULT_TAXONOMY: list[FolderRule] = [
    FolderRule(
        folder_name="22. Spirituality",
        note_type="spirituality",
        cmds_category="📚 690 Spirituality",
        tags=("Spirituality", "Sermon", "Bible"),
        include=(
            "설교",
            "예배",
            "교회",
            "성경",
            "로마서",
            "사도행전",
            "이사야",
            "대림절",
            "부활절",
        ),
        priority=95,
    ),
    FolderRule(
        folder_name="30. Jazz & Music",
        note_type="creative",
        cmds_category="📚 904 Creative Arts & Media Division",
        tags=("Jazz", "Music", "Creative"),
        include=("jazz", "재즈", "보컬", "합창", "연주", "음악", "선곡", "악보", "앨범"),
        priority=92,
    ),
    FolderRule(
        folder_name="23. Health & Biohacking",
        note_type="health",
        cmds_category="📚 653 Biohacking",
        tags=("Health", "Biohacking", "Medical"),
        include=(
            "병원",
            "진료",
            "치과",
            "의약",
            "세마글루타이드",
            "다이어트",
            "수면",
            "무호흡",
            "건강",
        ),
        priority=90,
    ),
    FolderRule(
        folder_name="32. Media & Interviews",
        note_type="media",
        cmds_category="📚 906 Partnerships & Networks Division",
        tags=("Interview", "Media", "Filming"),
        include=("인터뷰", "면접", "촬영", "방송", "유튜브", "영상", "출연"),
        priority=88,
    ),
    FolderRule(
        folder_name="24. Contracts & Finance",
        note_type="business-ops",
        cmds_category="📚 909 Consulting & Advisory Division",
        tags=("Contract", "Finance", "BusinessOps"),
        include=(
            "계약",
            "견적",
            "정산",
            "인보이스",
            "결제",
            "구독료",
            "사업자",
            "법인",
            "예산",
            "강의료",
        ),
        priority=86,
    ),
    FolderRule(
        folder_name="14. Product & Engineering",
        note_type="product-engineering",
        cmds_category="📚 907 Product & Engineering Division",
        tags=("Product", "Engineering", "Automation"),
        include=(
            "앱",
            "플러그인",
            "agent",
            "에이전트",
            "vercel",
            "airtable",
            "자동화",
            "개발",
        ),
        priority=84,
    ),
    FolderRule(
        folder_name="13. Consulting & AX",
        note_type="consulting",
        cmds_category="📚 909 Consulting & Advisory Division",
        tags=("Consulting", "AX", "CorporateEducation"),
        include=(
            "경영진",
            "임원",
            "ceo",
            "c레벨",
            "ax",
            "기업",
            "대외",
            "고객",
            "컨설팅",
            "코칭",
        ),
        priority=82,
    ),
    FolderRule(
        folder_name="12. 지식관리",
        note_type="knowledge-management",
        cmds_category="📚 901 Knowledge Management & Research Division",
        tags=("KnowledgeManagement", "Obsidian", "PKM"),
        include=(
            "옵시디언",
            "obsidian",
            "pkm",
            "지식관리",
            "llm wiki",
            "세컨드 브레인",
            "second brain",
        ),
        exclude=("계약", "견적"),
        priority=80,
    ),
    FolderRule(
        folder_name="11. AI 강의",
        note_type="lecture",
        cmds_category="📚 903 Teaching & Curriculum Division",
        tags=("Lecture", "AI교육", "Teaching"),
        include=(
            "강의",
            "강연",
            "특강",
            "세미나",
            "워크숍",
            "워크샵",
            "교육",
            "강좌",
            "커리큘럼",
        ),
        exclude=("회의", "미팅", "협의", "조율", "계약", "견적"),
        priority=81,
    ),
    FolderRule(
        folder_name="15. Partnerships & Pipeline",
        note_type="partnership",
        cmds_category="📚 906 Partnerships & Networks Division",
        tags=("Partnership", "Pipeline", "Network"),
        include=("파트너", "협력", "네트워킹", "섭외", "제안", "팔로업", "대외 일정", "협업"),
        priority=74,
    ),
    FolderRule(
        folder_name="10. Meetings",
        note_type="meeting",
        cmds_category="60. Collections/63. Meetings",
        tags=("MeetingMinutes", "Meeting"),
        include=("회의", "미팅", "논의", "협의", "조율", "회의록", "세션", "대화"),
        priority=60,
    ),
    FolderRule(
        folder_name="21. Personal & Family",
        note_type="personal",
        cmds_category="60. Collections/61. People",
        tags=("Personal", "Family"),
        include=(
            "가족",
            "연인",
            "친구",
            "부모",
            "엄마",
            "아빠",
            "삼촌",
            "일상",
            "사적인 대화",
            "개인",
        ),
        priority=58,
    ),
]

DEFAULT_CLASSIFICATION = RecordingClassification(
    folder_name="10. Meetings",
    note_type="meeting",
    cmds_category="60. Collections/63. Meetings",
    tags=normalize_tags(["MeetingMinutes", "Meeting"]),
    confidence=0.35,
    reason="default meeting bucket",
)


def _rule_from_dict(d: dict) -> FolderRule:
    return FolderRule(
        folder_name=d["folder_name"],
        note_type=d.get("note_type", "note"),
        cmds_category=d.get("cmds_category", ""),
        tags=tuple(d.get("tags", [])),
        include=tuple(d.get("include", [])),
        exclude=tuple(d.get("exclude", [])),
        priority=int(d.get("priority", 50)),
    )


def load_taxonomy() -> list[FolderRule]:
    """Return the active folder taxonomy.

    A gitignored ``data/classification.json`` (the user's personal rules) wins
    when present; otherwise the shipped, PII-free ``DEFAULT_TAXONOMY`` is used.
    This keeps real names / private categories out of distributed code while
    letting each user keep their own scheme locally.
    """
    from .paths import DATA_DIR

    user_file = DATA_DIR / "classification.json"
    if user_file.exists():
        try:
            raw = json.loads(user_file.read_text(encoding="utf-8"))
            folders = raw.get("folders", raw) if isinstance(raw, dict) else raw
            rules = [_rule_from_dict(r) for r in folders]
            if rules:
                return rules
        except Exception:
            pass
    return DEFAULT_TAXONOMY


FOLDER_TAXONOMY: list[FolderRule] = load_taxonomy()


def classify_snapshot(snapshot: dict[str, Any]) -> RecordingClassification:
    title = str(snapshot.get("title") or "")
    kw = list(snapshot.get("keywords") or [])
    keywords = " ".join(str(v) for v in kw)
    summaries = str(snapshot.get("summaries") or "")[:4000]
    integrated = str(snapshot.get("integrated_summary") or "")[:4000]
    primary_text = normalize_text(" ".join([title, keywords]))
    secondary_text = normalize_text(" ".join([summaries, integrated]))
    use_secondary = looks_like_raw_timestamp(title) or len(primary_text) < 12

    best: tuple[int, int, FolderRule, list[str]] | None = None
    for rule in FOLDER_TAXONOMY:
        includes = [term for term in rule.include if normalize_text(term) in primary_text]
        source_penalty = 0
        if not includes and use_secondary:
            includes = [term for term in rule.include if normalize_text(term) in secondary_text]
            source_penalty = 20
        if not includes:
            continue
        if any(normalize_text(term) in primary_text for term in rule.exclude):
            continue
        score = rule.priority + len(includes) * 5 - source_penalty
        # Tie-break equal scores by rule priority (then earlier list position).
        if best is None or (score, rule.priority) > (best[0], best[2].priority):
            best = (score, len(includes), rule, includes)

    if best is None:
        return DEFAULT_CLASSIFICATION

    score, match_count, rule, includes = best
    confidence = min(0.95, 0.5 + match_count * 0.12 + (score - rule.priority) * 0.005)
    tags = normalize_tags(["plaud", rule.note_type, *rule.tags, *kw[:8]])
    return RecordingClassification(
        folder_name=rule.folder_name,
        note_type=rule.note_type,
        cmds_category=rule.cmds_category,
        tags=tags[:20],
        confidence=round(confidence, 2),
        reason="matched " + ", ".join(includes[:5]),
    )


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().replace("·", " ")).strip()


def looks_like_raw_timestamp(title: str) -> bool:
    text = title.strip()
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?", text))
