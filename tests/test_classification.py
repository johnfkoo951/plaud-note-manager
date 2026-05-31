from core.classification import classify_snapshot, looks_like_raw_timestamp


def snapshot(title: str, keywords: list[str] | None = None) -> dict:
    return {
        "title": title,
        "keywords": keywords or [],
        "summaries": "",
        "integrated_summary": "",
    }


def test_classifies_specific_topic_over_generic_meeting() -> None:
    # A high-priority topical rule (shipped in the default taxonomy) beats the
    # generic meeting bucket.
    result = classify_snapshot(snapshot("재즈 보컬 합창 연주 음악 작업"))

    assert result.folder_name == "30. Jazz & Music"
    assert result.note_type == "creative"
    assert "Jazz" in result.tags


def test_classifies_business_consulting_and_contracts() -> None:
    consulting = classify_snapshot(snapshot("기업 C레벨 AX 코칭 및 임원 교육 회의"))
    contract = classify_snapshot(snapshot("강의 계약 조건 확정 및 구독료 결제 방식 협의"))

    assert consulting.folder_name == "13. Consulting & AX"
    assert contract.folder_name == "24. Contracts & Finance"


def test_classifies_lecture_only_when_not_a_planning_meeting() -> None:
    lecture = classify_snapshot(snapshot("강의: 옵시디언 기반 AI 문서 워크플로우"))
    meeting = classify_snapshot(snapshot("옵시디언 강의 기획 회의: 일정 조율"))

    assert lecture.folder_name == "11. AI 강의"
    assert meeting.folder_name in {"10. Meetings", "12. 지식관리"}


def test_empty_snapshot_returns_default_meeting() -> None:
    result = classify_snapshot({})

    assert result.folder_name == "10. Meetings"
    assert result.confidence == 0.35
    assert "default" in result.reason


def test_raw_timestamp_title_uses_secondary_text() -> None:
    snap = {
        "title": "2026-05-29 14:30:00",
        "keywords": [],
        "summaries": "기업 임원 AX 코칭 회의",
        "integrated_summary": "",
    }

    result = classify_snapshot(snap)

    assert result.folder_name == "13. Consulting & AX"


def test_excludes_block_match() -> None:
    knowledge = classify_snapshot(snapshot("옵시디언 계약 협의"))
    lecture = classify_snapshot(snapshot("강의 기획 회의 일정"))

    assert knowledge.folder_name != "12. 지식관리"
    assert lecture.folder_name != "11. AI 강의"


def test_lecture_matches_without_colon() -> None:
    result = classify_snapshot(snapshot("AI 교육 세미나 3회차"))

    assert result.folder_name == "11. AI 강의"


def test_looks_like_raw_timestamp() -> None:
    assert looks_like_raw_timestamp("2026-05-29")
    assert looks_like_raw_timestamp("2026-05-29 14:30:00")

    assert not looks_like_raw_timestamp("Weekly Sync")
    assert not looks_like_raw_timestamp("")
