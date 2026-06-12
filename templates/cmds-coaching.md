---
name: cmds-coaching
description: CMDS vault-ready 1:1 코칭/컨설팅 세션 노트 — 세션 흐름 + 인사이트 + 후속 액션, ~함체.
---

다음 1:1 코칭/컨설팅 세션 전사본을 CMDSPACE 볼트에 바로 저장할 수 있는 세션 노트(.md)로 작성해주세요.
출력은 아래 형식의 마크다운 문서 하나만 — 설명 문장이나 코드펜스 없이 문서 본문만 출력합니다.

작성 규칙:
- 말투는 ~함/~임체. 코치(구요한)의 제안과 코치이의 반응·결정을 구분해 기록.
- 시간 순서가 아니라 **다룬 주제 단위로 재구성**. 코치이가 보인 인식 변화나 "아하 모먼트"는 별도 섹션에.
- 민감할 수 있는 개인 정보는 사실 위주로 담백하게. 수치·도구명·약속은 정확히 보존.
- frontmatter 위키링크는 따옴표 필수: "[[이름]]". description은 영어 1~2문장, 큰따옴표 필수.

출력 형식:

---
type: meeting
date: (세션 날짜 YYYY-MM-DD)
attendees:
  - "[[구요한]]"
  - "[[코치이]]"
organization: (언급된 경우만, "[[조직명]]")
index: "[[🏷 Meeting Notes]]"
tags:
  - "#MeetingMinutes"
  - "#코칭"
  - (도메인 태그 1~2개)
description: "(English, 1-2 sentences: coaching session focus + agreed next steps)"
aliases: []
---

>[!info]
>- Session: {title}
>- Date: [[YYYY-MM-DD]]
>- Participants: [[구요한]], [[코치이]]
>- Focus: (한 줄)

## 세션 개요
(3~4문장: 어떤 맥락에서 무엇을 다뤘는지, ~함체)

## 다룬 주제
#### (주제 1)
- 현재 상태/고민: …
- 논의·제안: …
- 코치이의 반응/결정: …

#### (주제 2)
- …

## 인사이트
- (세션 중 드러난 인식 변화, 깨달음, 강조된 원칙)

## 후속 액션
- [ ] (누가) (무엇을) (~기한, 명시된 경우만)

## 다음 세션
- (다음 일정/주제가 언급된 경우만)

---

제목: {title}
키워드: {keywords}
화자: {speakers}

전사본:

{transcript}
