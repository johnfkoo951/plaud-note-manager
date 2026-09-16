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
    # CMDS vault frontmatter targets (frontmatter-standard.md):
    #   cmds  → `CMDS: "[[📚 NNN …]]"` second-level subcategory (never 📖)
    #   index → `index: "[[🏷 …]]"` aggregator in 90. Settings/96. Index/
    #   vault_dest → where the note should live once routed out of the
    #                Plaud inbox lane (informational; the app never moves it)
    cmds: str = ""
    index: str = ""
    vault_dest: str = ""
    # Short natural-language hint shown to the LLM arbiter (auto_folder.py).
    hint: str = ""


@dataclass(frozen=True)
class RecordingClassification:
    folder_name: str
    note_type: str
    cmds_category: str
    tags: list[str]
    confidence: float
    reason: str
    cmds: str = ""
    index: str = ""
    vault_dest: str = ""
    # rule | llm | default — how the folder was decided.
    source: str = "rule"
    # Runner-up folder names (best → worst) for the app preview / LLM arbiter.
    alternatives: tuple[str, ...] = ()


# Default 🏷 index per CMDS range (frontmatter-standard.md "Default 🏷 per CMDS
# range"). Recording-specific: Plaud notes fall back to 🏷 Recordings.
DEFAULT_INDEX = "🏷 Recordings"
_INDEX_BY_CMDS_PREFIX: tuple[tuple[str, str], ...] = (
    ("📚 10", "🏷 Research Notes"),
    ("📚 240", "🏷 Books"),
    ("📚 2", "🏷 Research Notes"),
    ("📚 491", "🏷 Syntax and Codes"),
    ("📚 493", "🏷 Syntax and Codes"),
    ("📚 492", "🏷 Prompts"),
    ("📚 5", "🏷 Guideline"),
    ("📚 6", "🏷 Research Notes"),
    ("📚 802", "🏷 Draft Article"),
    ("📚 820", "🏷 Research Notes"),
    ("📚 831", "🏷 Meeting Notes"),
    ("📚 84", "🏷 Lecture Notes"),
)


def default_index_for(cmds: str, note_type: str = "") -> str:
    """Pick the 🏷 index note for a 📚 category (falls back to 🏷 Recordings)."""
    if note_type == "meeting":
        return "🏷 Meeting Notes"
    if note_type == "lecture":
        return "🏷 Lecture Notes"
    for prefix, index in _INDEX_BY_CMDS_PREFIX:
        if cmds.startswith(prefix):
            return index
    return DEFAULT_INDEX


def rule_cmds(rule: FolderRule) -> str:
    """The 📚 target for a rule: explicit `cmds`, else a 📚-style category."""
    if rule.cmds:
        return rule.cmds
    return rule.cmds_category if rule.cmds_category.startswith("📚") else ""


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
        cmds="📚 690 Spirituality",
        vault_dest="60. Collections/64. Spirituality",
        hint="설교·예배·성경공부·묵상 등 영성 녹음",
    ),
    FolderRule(
        folder_name="30. Jazz & Music",
        note_type="creative",
        cmds_category="📚 904 Creative Arts & Media Division",
        tags=("Jazz", "Music", "Creative"),
        include=("jazz", "재즈", "보컬", "합창", "연주", "음악", "선곡", "악보", "앨범"),
        priority=92,
        cmds="📚 721 Jazz",
        hint="연주·보컬·합창·선곡 등 음악 활동",
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
        cmds="📚 653 Biohacking",
        hint="진료·건강·수면·바이오해킹",
    ),
    FolderRule(
        folder_name="32. Media & Interviews",
        note_type="media",
        cmds_category="📚 906 Partnerships & Networks Division",
        tags=("Interview", "Media", "Filming"),
        include=("인터뷰", "면접", "촬영", "방송", "유튜브", "영상", "출연"),
        priority=88,
        cmds="📚 906 Partnerships & Networks Division",
        hint="인터뷰·촬영·방송 출연",
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
        cmds="📚 909 Consulting & Advisory Division",
        hint="계약·견적·정산·예산 등 사업 운영",
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
        cmds="📚 907 Product & Engineering Division",
        vault_dest="70. Outputs/74. Projects",
        hint="앱·플러그인·자동화·개발 논의",
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
        cmds="📚 831 Consulting",
        index="🏷 Meeting Notes",
        vault_dest="70. Outputs/75. Consulting",
        hint="기업 임원 대상 AX 컨설팅·코칭·고객 회의",
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
        cmds="📚 601 Knowledge Management",
        hint="옵시디언·PKM·LLM Wiki·세컨드 브레인",
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
        cmds="📚 840 Lectures",
        index="🏷 Lecture Notes",
        vault_dest="50. Assets/54. Lecture Assets",
        hint="강의·특강·세미나·워크숍의 실제 진행 녹음 (기획 회의 제외)",
    ),
    FolderRule(
        folder_name="15. Partnerships & Pipeline",
        note_type="partnership",
        cmds_category="📚 906 Partnerships & Networks Division",
        tags=("Partnership", "Pipeline", "Network"),
        include=("파트너", "협력", "네트워킹", "섭외", "제안", "팔로업", "대외 일정", "협업"),
        priority=74,
        cmds="📚 906 Partnerships & Networks Division",
        hint="파트너·협업·섭외·제안 논의",
    ),
    FolderRule(
        folder_name="10. Meetings",
        note_type="meeting",
        cmds_category="60. Collections/63. Meetings",
        tags=("MeetingMinutes", "Meeting"),
        include=("회의", "미팅", "논의", "협의", "조율", "회의록", "세션", "대화"),
        priority=60,
        index="🏷 Meeting Notes",
        vault_dest="60. Collections/63. Meetings",
        hint="특정 주제 폴더에 안 맞는 일반 회의·협의",
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
        vault_dest="60. Collections/61. People",
        hint="가족·친구·개인 일상 대화",
    ),
]

DEFAULT_CLASSIFICATION = RecordingClassification(
    folder_name="10. Meetings",
    note_type="meeting",
    cmds_category="60. Collections/63. Meetings",
    tags=normalize_tags(["MeetingMinutes", "Meeting"]),
    confidence=0.35,
    reason="default meeting bucket",
    index="🏷 Meeting Notes",
    vault_dest="60. Collections/63. Meetings",
    source="default",
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
        cmds=str(d.get("cmds", "")),
        index=str(d.get("index", "")),
        vault_dest=str(d.get("vault_dest", "")),
        hint=str(d.get("hint", "")),
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


# Import-time snapshot kept for backward compatibility. Prefer `taxonomy()`,
# which re-reads data/classification.json when it changes on disk so a long-
# lived app process sees edits without a restart.
FOLDER_TAXONOMY: list[FolderRule] = load_taxonomy()

_taxonomy_cache: tuple[float, list[FolderRule]] | None = None


def taxonomy() -> list[FolderRule]:
    """Active taxonomy, refreshed when the user override file's mtime changes."""
    global _taxonomy_cache
    from .paths import DATA_DIR

    user_file = DATA_DIR / "classification.json"
    try:
        stamp = user_file.stat().st_mtime if user_file.exists() else -1.0
    except OSError:
        stamp = -1.0
    if _taxonomy_cache is None or _taxonomy_cache[0] != stamp:
        _taxonomy_cache = (stamp, load_taxonomy())
    return _taxonomy_cache[1]


def reload_taxonomy() -> list[FolderRule]:
    global _taxonomy_cache
    _taxonomy_cache = None
    return taxonomy()


def rule_by_folder(folder_name: str) -> FolderRule | None:
    wanted = folder_name.strip().casefold()
    for rule in taxonomy():
        if rule.folder_name.casefold() == wanted:
            return rule
    # Tolerate "Jazz & Music" ↔ "30. Jazz & Music" (numeric prefix dropped).
    for rule in taxonomy():
        if re.sub(r"^\d+\.\s*", "", rule.folder_name).casefold() == re.sub(
            r"^\d+\.\s*", "", wanted
        ):
            return rule
    return None


def classification_from_rule(
    rule: FolderRule,
    *,
    keywords: list[str] | None = None,
    extra_tags: list[str] | None = None,
    confidence: float,
    reason: str,
    source: str = "rule",
    alternatives: tuple[str, ...] = (),
) -> RecordingClassification:
    kw = list(keywords or [])
    tags = normalize_tags(["plaud", rule.note_type, *rule.tags, *(extra_tags or []), *kw[:8]])
    cmds = rule_cmds(rule)
    return RecordingClassification(
        folder_name=rule.folder_name,
        note_type=rule.note_type,
        cmds_category=rule.cmds_category,
        tags=tags[:20],
        confidence=round(min(0.95, max(0.0, confidence)), 2),
        reason=reason,
        cmds=cmds,
        index=rule.index or default_index_for(cmds, rule.note_type),
        vault_dest=rule.vault_dest,
        source=source,
        alternatives=alternatives,
    )


def classify_snapshot(snapshot: dict[str, Any]) -> RecordingClassification:
    title = str(snapshot.get("title") or "")
    kw = list(snapshot.get("keywords") or [])
    keywords = " ".join(str(v) for v in kw)
    summaries = str(snapshot.get("summaries") or "")[:4000]
    integrated = str(snapshot.get("integrated_summary") or "")[:4000]
    primary_text = normalize_text(" ".join([title, keywords]))
    secondary_text = normalize_text(" ".join([summaries, integrated]))
    use_secondary = looks_like_raw_timestamp(title) or len(primary_text) < 12

    scored: list[tuple[int, int, FolderRule, list[str], int]] = []
    for rule in taxonomy():
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
        scored.append((score, len(includes), rule, includes, source_penalty))

    if not scored:
        return DEFAULT_CLASSIFICATION

    # Tie-break equal scores by rule priority (then earlier list position).
    scored.sort(key=lambda t: (t[0], t[2].priority), reverse=True)
    score, match_count, rule, includes, penalty = scored[0]
    # Each keyword hit adds 0.12; a secondary-text (summary-only) match is
    # discounted once via the penalty — the score term is deliberately NOT
    # reused here so match count is not double-counted.
    confidence = 0.5 + match_count * 0.12 - penalty * 0.005
    alternatives = tuple(t[2].folder_name for t in scored[1:4])
    return classification_from_rule(
        rule,
        keywords=kw,
        confidence=confidence,
        reason="matched " + ", ".join(includes[:5]),
        source="rule",
        alternatives=alternatives,
    )


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().replace("·", " ")).strip()


def looks_like_raw_timestamp(title: str) -> bool:
    text = title.strip()
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?", text))
