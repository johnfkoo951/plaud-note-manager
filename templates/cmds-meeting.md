---
name: cmds-meeting
description: CMDS vault-ready 회의록 — frontmatter + info callout + Discussion/Next Steps, ~함체.
---

다음 회의 전사본을 CMDSPACE 볼트에 바로 저장할 수 있는 회의록(.md)으로 작성해주세요.
출력은 아래 형식의 마크다운 문서 하나만 — 설명 문장이나 코드펜스 없이 문서 본문만 출력합니다.

작성 규칙:
- 말투는 ~함/~임체 (예: "…하기로 합의함", "…확인이 필요함"). ~해요체 금지.
- 전사 순서가 아니라 **주제별로 재구성**. 군더더기·반복 제거, 수치·날짜·고유명사는 정확히 보존.
- frontmatter의 위키링크는 반드시 따옴표로 감싼다: "[[이름]]". description은 영어 1~2문장, 반드시 큰따옴표.
- 화자 이름을 모르면 화자A/화자B 또는 역할명(팀장, 교수님)을 사용.
- 액션 아이템의 기한이 언급되지 않았으면 날짜를 적지 않는다 (추측 금지).

출력 형식:

---
type: meeting
date: (회의 날짜 YYYY-MM-DD, 전사본/제목에서 추정)
attendees:
  - "[[구요한]]"
  - "[[참석자]]"
organization: (언급된 경우만, "[[조직명]]")
index: "[[🏷 Meeting Notes]]"
tags:
  - "#MeetingMinutes"
  - (도메인 태그 2~3개)
description: "(English, 1-2 sentences: what this meeting covered + key outcome)"
aliases: []
---

>[!info]
>- Meeting Title: {title}
>- Meeting Date: [[YYYY-MM-DD]]
>- Attendees: [[이름1]], [[이름2]]
>- Meeting Topic: (한 줄 요약)

## Summary
(3~5문장 핵심 요약, ~함체)

## Discussion
#### (주제 1)
- (핵심 포인트)
	- (세부 내용, 탭 들여쓰기)

#### (주제 2)
- …

## Decisions
1. (결정 사항 — 배경과 합의 내용 1~2줄)

## Next Steps
- [ ] (담당자) (할 일) (~YYYY-MM-DD, 명시된 경우만)

---

제목: {title}
키워드: {keywords}
참석자(전사 기준): {speakers}

전사본:

{transcript}
