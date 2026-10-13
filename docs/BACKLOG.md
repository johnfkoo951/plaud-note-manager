# Plaud Note Manager — 개발 백로그

- 작성: 2026-09-19 (Claude Fable 5.1, effort high) · 기준 커밋: `16afab3` · 버전 0.8.3
- 범위: 2026-09-16~19 세션에서 **미완으로 남긴 것**과 **작업 중 발견했으나 고치지 않은 결함**
- 모든 항목은 작성 시점에 코드로 재확인함 (기억이 아니라 `grep` 기준). 파일·라인 병기.
- 완료된 작업의 기록은 `PLAN-v0.6/0.7/0.8.md`, 요청 이력은 `REQUESTS.md`.

---

## 0. 한 줄 요약

| # | 과제 | 성격 | 영향 | 크기 |
|---|---|---|---|---|
| A | 앱 UI — 템플릿 뷰어 + 볼트 선택 전송 | 승인된 계획의 미완 파트 | CLI만 있고 앱에서 못 씀 | 중 |
| B | `note_context` 화자 공백 분리 | **데이터 오염 버그** | 메인 볼트 `speakers:`에 `[[Speaker]]`·`[[3]]` 유입 | 소 |
| C | `vault_send --to wiki` 미수정 경로 | **스키마 위반** | 위키에 불법 frontmatter를 쓰는 경로가 살아 있음 | 소 |
| D | legacy `cmds-*` 템플릿 이중 frontmatter | 기존 결함 | 해당 템플릿 산출물이 YAML 2겹 | 소 |
| E | `default.md`를 summarize 기본값으로 사용 | 설정 오류 | 섹션 3개가 빈 문자열로 렌더 | 소 |
| F | 슬롯 모드 `"integrated"` 하드코딩 | 확장성 | 새 통합 템플릿이 summary 모드로 오인 | 소 |
| G | 재전송 중복 방지 없음 | 운영 | 같은 녹음이 ` (2).md`로 계속 쌓임 | 중 |
| H | 위키 볼트 미인덱싱 | 기능 공백 | 교차 볼트 `related:` 해결 불가 | 중 |
| I | `vault_lint` 조건식 우연 동작 | 잠복 | 조건 수정 시 조용히 깨짐 | 소 |
| J | `model_available()` 거짓 양성 | 진단 | 쿼터 소진·티어 문제를 게이트가 통과 | 중 |
| K | Gemini 경로 차단 (외부 요인) | 환경 | 4사 중 1사 사용 불가 | 외부 |

---

## A. 앱 UI — 템플릿 뷰어 + 볼트 선택 전송 (미완)

2026-09-16 승인된 계획의 Part D. **Part A·B·C는 완료**, D만 남음.
백엔드는 전부 되어 있어 앱에 UI만 얹으면 된다 — `plaud vault-compose`, `plaud vaults`,
`plaud template-show`, `plaud templates` 모두 동작하고 JSON도 낸다.

현재 앱에 `vault-compose` 연결은 **0건**이다.

### A1. 설정에 템플릿 뷰어

지금 템플릿 UI는 `ContentView.swift:5208`의 "templates 폴더 열기" 버튼 하나뿐.

- `SettingsSheet`(`ContentView.swift:853`)에 `section("템플릿")` 추가. 기존 `section()`·
  `settingsRow()` 헬퍼 그대로 사용.
- 좌측 목록: `Database.listTemplates()` (`Database.swift:1044`) — 이미 있음
- 우측 본문: `runPlaudOutput(args: ["template-show", name])` — 읽기 전용
- `plaud templates`가 `description`도 내므로 목록에 부제로 표시

### A2. 템플릿 실행 + 볼트 선택 전송

`AddSlotSheet`(`ContentView.swift:5700`)의 템플릿 Picker와 `VaultSendMenu`(`:4945`)를 조합한 새 시트.

1. 템플릿 선택 → 2. 볼트 선택 (`plaud vaults --json`) → 3. **위키면 `collectionPurpose`
   입력란 노출**(비면 전송 비활성) → 4. `vault-compose` 호출 → 기존 `VaultSendOutcome`
   피드백 스트립 재사용

선행 작업 하나: `Database.AppConfig`(`Database.swift:841`)가 `obsidian_vault`/`wiki_vault`를
파싱하지 않는다. 피커에 경로를 보이려면 두 키 추가 필요.

**테스트**: 앱 테스트 관례상 뷰가 아니라 순수 함수를 뺀다 —
`VaultComposeArguments.build(fileID:template:vault:purpose:) -> [String]`,
`PlaudTagCommandArgumentsTests`와 같은 형태.

**해결 (2026-10-12):** 설정에 읽기 전용 Templates 보기를, 녹음마다 `note-*` 템플릿과 볼트를 골라 `vault-compose`로 보내는 작성 시트(위키는 수집 목적 필수)를 추가했다 — app-01 39793ca, app-03 827112d, core-03 8b2109e, core-05 8cc23b6

---

## B. `note_context` 화자 공백 분리 — 데이터 오염

`core/vault_send.py:259`

```python
speakers = [s for s in str(snapshot.get("speakers") or "").replace(",", " ").split() if s]
```

스냅샷의 화자 문자열은 쉼표 구분이다 (`'Speaker 3, Speaker 4, 구요한, 안창현'`).
공백으로도 쪼개므로 **`Speaker 3` → `Speaker` + `3`** 두 조각이 된다. 조각은 플레이스홀더
판정도 빠져나가 `speakers:`에 `"[[Speaker]]"`, `"[[3]]"`로 들어간다 — 볼트에 고아 링크가 생긴다.

실제로 이 세션에서 위키 노트에 다음이 생성됐다:

```yaml
author:
  - "[[Speaker]]"
  - "[[3]]"
  - "[[speaker_0]]"
```

**위키 경로는 우회 완료** — `core/vault_compose.py`의 `human_speakers()`가 스냅샷을 쉼표로만
파싱하고 플레이스홀더를 거른다. 하지만 `note_context`를 쓰는 **메인 볼트 송출은 그대로 노출**돼 있다.

**조치**: `vault_compose.human_speakers()`를 `vault_send`로 올려 공유. 쉼표 분리 + 플레이스홀더
필터. 기존 노트 소급 수정은 하지 않는다(역사 기록).

**해결 (2026-10-12):** 화자 처리를 `core.metadata.human_speakers`(쉼표 분리, NFC 후 플레이스홀더 제외·중복 제거) 하나로 모아 메인 볼트 송출에도 `[[Speaker]]`·`[[3]]` 조각이 더는 생기지 않는다 — core-02 97aaf00

---

## C. `vault_send --to wiki` — 스키마 위반 경로가 살아 있음

`core/vault_send.py:537-539`, `DEFAULT_DEST = "00. Inbox"` (`:47`)

위키 분기가 하는 일은 `source_vault` 설정과 `related:` → `mainVaultRelated:` 전환뿐이다.
나머지는 메인 볼트 형태 그대로 — `type: transcript`(위키 어휘에 없음), `status: inProgress`
(위키 enum 아님), `CMDS:`/`index:`(위키는 `mainVaultCmds:`), `model`/`effort` 조건부 취급.
목적지도 `00. Inbox/`인데, 위키 resolver는 그 경로를 **`wiki-inbox`**(다른 프로필)로 매핑한다.

`vault-compose --to wiki`가 올바른 경로이고 볼트 resolver 검증까지 통과했지만,
**옛 경로가 제거되지 않아** 앱의 기존 "Wiki Vault" 메뉴(`ContentView.swift:4945` 부근)가
여전히 이쪽을 호출한다.

**조치** (택1):
1. `vault_send`의 `to == "wiki"`를 `vault_compose.compose_to_wiki()`로 위임
2. 또는 위키 전송을 `vault_send`에서 제거하고 앱 메뉴를 `vault-compose`로 교체

2번이 깔끔하다 — 두 벌의 위키 로직을 남길 이유가 없다. 단 `collectionPurpose` 입력이
필요하므로 **A2와 묶어서** 처리해야 한다.

**해결 (2026-10-12):** 2번으로 처리했다 — `vault-send`는 위키로 떨어지는 모든 대상(`--dest` 포함)을 `wiki_unsupported`로 거절하고, 앱의 Wiki 메뉴·팔레트는 수집 목적을 받는 `vault-compose --to wiki` 시트를 연다 — core-05 8cc23b6, app-03 827112d

---

## D. legacy `cmds-*` 템플릿 — 이중 frontmatter

`templates/cmds-meeting.md`, `cmds-lecture.md`, `cmds-coaching.md`

세 템플릿 모두 **자체 YAML frontmatter를 뱉도록 지시**한다. 그런데
`summarize()`(`core/summarize.py:387-390`)는 출력 파일에 래퍼 frontmatter
(`file_id`/`provider`/`model`/`template`)를 **무조건** 앞에 붙인다. 결과는 `---` 블록 2겹이고,
Obsidian Properties 패널이 깨진다.

신규 `note-*` 10종은 본문 섹션만 만들어 이 문제가 없다 (`tests/test_note_templates.py`가 고정).

**조치**: 세 템플릿을 `note-meeting`/`note-lecture`/`note-consulting`으로 대체 안내하고
deprecated 표시. 삭제는 기존 슬롯 설정이 깨지므로 하지 않는다 — `data/slots.json`이
템플릿명을 문자열로 들고 있다.

**해결 (2026-10-12):** 세 템플릿에 `deprecated`·`replaced_by`를 달아 CLI가 대체 템플릿을 안내하고, 새 슬롯 선택지와 템플릿 작성에서는 빠지며 기존 슬롯은 그대로 돈다(이미 만든 출력의 YAML 2겹은 남음) — core-04 4e337f7, app-02 734db1e

---

## E. `default.md`가 summarize 기본값

`core/slots.py:20`, `cli/main.py:2723`

`default.md`는 **통합(integrate) 프롬프트**다 — `{cmds_transcript}`, `{plaud_transcript}`,
`{plaud_summaries}`를 쓰고 `===TRANSCRIPT===` 마커를 낸다. 그런데 기본 슬롯과
`cmds-summarize --template` 기본값이 이걸 가리킨다.

`summarize()`는 `{transcript}`, `{title}`, `{keywords}`, `{speakers}` 4개만 넘기므로,
summarize 경로로 `default`를 돌리면 **세 섹션이 빈 문자열로 렌더**된 프롬프트가 모델에 간다.

**조치**: summarize 기본값을 `note-general`로 교체. `default.md`는 integrate 전용으로 남긴다.
기존 슬롯(`data/slots.json`)은 사용자 설정이므로 건드리지 않는다.

**해결 (2026-10-12):** `cmds-summarize`와 기본 슬롯(Python·앱 모두)의 기본 템플릿을 `note-general`로 바꾸고 `default.md`를 `kind: integrated`로 선언했다; 기존 `data/slots.json`은 건드리지 않는다 — core-04 4e337f7, core-03 8b2109e, app-02 734db1e

---

## F. 슬롯 모드 `"integrated"` 하드코딩

`app/Sources/PlaudNoteApp/ContentView.swift:5385`

```swift
if slot.template == "integrated" { return .integrated }
```

문자열 완전 일치다. 앞으로 통합형 템플릿을 다른 이름으로 추가하면 앱이 summary 모드로
오인하고, 사용자가 매번 수동으로 토글해야 한다.

**조치**: 템플릿이 통합형인지 선언할 방법이 필요하다. 다만 `core/templates.py`의 frontmatter
파서는 `description:`만 읽고, `save_template()`은 다른 키를 **파괴**한다(`templates.py:90-98`).
가장 싼 방법은 명명 규약(`*-integrated`) 또는 `core/templates.py`에 `kind:` 필드 추가.
후자가 옳지만 `save_template` 왕복 보존까지 함께 고쳐야 한다.

**해결 (2026-10-12):** 템플릿 front matter에 `kind`(summary·integrated·internal)를 도입하고 `template-save`가 다른 키를 보존하게 했으며, 앱 슬롯은 이름 대신 `kind`로 열리는 모드·Generate·Generate All을 정한다 — core-03 8b2109e, app-01 39793ca, app-02 734db1e

---

## G. 재전송 중복 방지 없음

`core/vault_send.py:463-470`, `:608`

`unique_note_path()`는 파일이 있으면 ` (2).md`, ` (3).md`로 비켜 쓴다. **`plaud_id` 기준 중복
검사가 없어서** 같은 녹음을 두 번 보내면 볼트에 두 개가 생긴다.
`storage.upsert_note_reference()`가 마지막 경로를 기록하지만 쓰기를 막지는 않는다.

**조치**: 전송 전 `note_references`에서 같은 `file_id` + `kind`의 기존 경로를 조회해,
파일이 실존하면 (a) 덮어쓸지 (b) 건너뛸지 (c) 새로 만들지 사용자에게 묻는다.
CLI는 `--on-duplicate skip|overwrite|new` 플래그.

**남은 생산 경로 (G 범위 밖)**: `vault-send`와 `vault-compose`는 이제 기본이 `ask`이고
(위키는 raw source가 불변이라 overwrite를 거절, new는 새 버전을 만들고 이전 것을 보존),
아래 세 경로는 아직 이 검사를 거치지 않는다. `dual-send`는 여전히 ` (2).md`로 번호를 붙여
새로 쓴다(`tests/test_dual_vault.py`가 이 동작을 고정). 목록 컨텍스트 메뉴의 구형
"Send to Obsidian"(`plaud obsidian`)은 Claude가 참조 기록 없이 파일링한다. meeting-note는
기존 노트를 묻지 않고 제자리에서 덮어쓴다.

**해결 (2026-10-12):** `vault-send`·`vault-compose`에 `--on-duplicate ask|skip|overwrite|new`(기본 ask, 모델·Terminal 전에 판단)를 더했고, 앱은 열기·덮어쓰기·새로 만들기·취소 대화상자로 같은 요청을 다시 실행한다(위키·Claude Terminal은 덮어쓰기 없음) — core-06 60ff8d9, app-04 6f2ee73

**추가 수정 (2026-10-13):** 전체 diff 리뷰 두 건이 찾은 교차 결함 — 다른 녹음의 노트를 소유자로 오인하던 동일 이름 판정과 오래된 참조 행을 노트 자신의 식별자(`plaud_id`·위키 `source`)로만 확인하고, 이해할 수 없는 front matter는 소유권을 주장하지 않게 했다; 회의록 실행이 송출 참조를 `draft`로 바꾸던 것도 막았다 — core-09 7502bd0, core-10 03b2c57

---

## H. 위키 볼트 미인덱싱

`core/vault_index.py:69-72`

```python
def default_vaults() -> list[str]:
    vault = app_config.obsidian_vault()
    return [vault.name] if vault else []
```

메인 볼트만 인덱싱한다. 따라서 `related_notes()`가 **위키 노트를 절대 찾지 못한다** —
위키에 raw source를 넣기 시작한 지금은 실질적 공백이다. 위키 노트끼리의 `related:`도,
메인→위키 역방향 링크 후보도 비어 나온다.

추가로 `related_notes()`의 기본 `min_confidence=0.9`는 태그 매칭(0.7)을 제외한다
(`vault_index.py:469-471`). 제목·별칭 완전일치만 링크가 된다.

**조치**: `default_vaults()`가 위키도 포함하도록 확장. 단 `vault_notes` 행에 볼트 구분이
있으므로 교차 볼트 링크는 **advanced-uri로만** 만들어야 한다(`[[…]]` 금지).

**해결 (2026-10-13):** 관련 노트 조회를 실제로 쓰는 목적지 볼트로 한정하고 파일 존재를 확인하며(다른 볼트 노트가 `[[…]]`로 들어가던 잠복 결함 포함), 위키도 자기 루트로 인덱싱해 메인 노트에는 컴파일된 위키 노트만 `wikiVaultRelated`로 연결한다; 인덱스는 링크 계산 시 시간당 1회 증분 갱신하며 실패해도 송출을 막지 않는다 — core-08

---

## I. `vault_lint` 조건식이 우연히 동작

`core/vault_lint.py:140`

```python
if "source: plaud" not in text.split("\n---", 2)[0] if text.startswith("---") else True:
```

조건 표현식 결합 순서 덕분에 의도대로 동작하지만, 읽는 사람 기준으로는 틀린 코드다.
누가 조건을 손대면 조용히 깨진다.

**조치**: 괄호로 명시하거나 두 줄로 분리.

참고로 이 lint는 모든 plaud 레인 노트에 `model:`을 요구하는데, 실제 레인 노트 3건에는
없다(필드 도입 전 생성). 지금 돌리면 전부 issue로 잡힌다.

**해결 (2026-10-12):** 조건식을 `frontmatter` 변수와 단순한 `if` 두 줄로 풀고, 세 경우를 고정하는 회귀 테스트를 더했다(동작 동일; `model:` 누락 레인 노트 건은 그대로) — core-01 ef73fc5

---

## J. `model_available()` 거짓 양성

`core/summarize.py:113-123`

바이너리가 PATH에 있는지만 본다. **호출 가능 여부는 검사하지 않는다.**
이 세션에서 실제로 문제가 됐다 — Codex 쿼터가 소진된 상태에서 게이트를 통과했고,
`propose_speaker_names`가 빈 스켈레톤을 만들어 캐시했다.

부분 완화는 완료:
- `summarize._cli_reason()`이 stderr **꼬리**에서 실제 사유를 뽑는다 (배너가 앞 400자를
  먹어 `"usage limit"`이 잘려나가던 문제)
- `dual_speakers.SpeakerProposalUnavailable` + `proposal_is_empty()`로 빈 제안 재시도
- 화자 확인은 LLM 없이도 동작 (`SpeakerSamples`)

**남은 것**: 다른 호출 지점(`auto_metadata`, `classify`, `integrate`)은 여전히 게이트만 믿는다.
매 호출 프리플라이트는 비용이 들어 권하지 않고, 대신 **실패 사유를 저장·표시**하는 패턴을
`dual_speakers`처럼 확산시키는 편이 낫다.

**해결 (2026-10-13):** auto_metadata·classify 보조·integrate가 실패 사유를 각자의 자리에 기록하고(`auto_meta.last_error`, `llm_status`/`llm_error`, `<model>__<template>.error.json`) 앱이 해당 위치에 표시한다; 공급자 진단 원문·프롬프트·키는 저장하지 않고 통제된 사유 문구만 남긴다 — core-07 77051f5, app-05 7348815

---

## K. Gemini 경로 차단 (외부 요인)

로그인은 완료됐다(`~/.gemini/oauth_creds.json` 존재). 그러나 실호출이 막힌다:

```
IneligibleTierError: This client is no longer supported for Gemini Code Assist
for individuals. To continue using Gemini, please migrate to the Antigravity
suite of products: https://antigravity.google
ineligibleTiers: [ tierId: 'free-tier' ]
```

Google이 **개인용 Gemini CLI 무료 티어 OAuth를 중단**했다. 코드 문제가 아니다.

현재 프로바이더 상태 (2026-09-19 실측):

| 프로바이더 | 상태 |
|---|---|
| claude | ✓ |
| codex | ✓ (9/19 쿼터 복구 확인) |
| grok | ✓ |
| gemini | ✗ 티어 차단 |

**선택지**: (a) `GEMINI_API_KEY`로 api 백엔드 전환 — `config-backend gemini api`,
(b) Antigravity 이전, (c) 3사로 운용. 앱은 이미 3사로 충분하므로 급하지 않다.

---

## 건드리지 않기로 한 것

- **자동 폴더 라우팅** — 볼트 CMDS Process 관할 (`dual_vault.py` docstring 명시)
- **기존 볼트 노트 소급 수정** — 과거 경로·형식은 그 시점의 정확한 기록
- **`data/slots.json`** — 사용자 설정. 템플릿 정리 시에도 자동 마이그레이션하지 않는다
- **`templates/plaud-lane.base`** — 소비자이지 생성물이 아니다. 단 신규 템플릿이
  `CMDS`/`index`/`reuse-channels`를 채우면 지금 비어 있는 "CMDS별"·"재사용 후보" 2개 뷰가 살아난다

---

## 부록 — 테스트 위생 주의

새 테스트를 쓸 때 `summarize()`를 호출하면 **실제 `data/summaries/`에 파일이 쓰인다**.
이 세션에서 실제로 오염이 발생했고, 그 캐시 때문에 모델 실패 테스트가 **엉뚱한 이유로
통과**했다.

`tests/test_vault_compose.py`의 `isolated_data_dir` 픽스처가 해법 —
`paths.DATA_DIR` 계열 상수와 `_override`를 함께 monkeypatch한다. 전역 `conftest.py`로
올릴지는 판단 필요(기존 테스트 영향 확인 후).

현황: 567 Python tests · 54 Swift tests · ruff 클린 · 실제 `data/` 무변화.
