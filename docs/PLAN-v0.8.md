# Plaud Note Manager v0.8 — 기능 개선·업그레이드·오류 점검 레포트

- 작성: 2026-09-03 (Claude Fable 5.1, effort xhigh) · 기준: v0.7 (dual pipeline + reuse) 미커밋 상태 위
- 관점: **CMDS 철학 — 지식 분류(Classification)와 연결(Connection)을 앱 안에서 끝내지 않고, Obsidian 메인 볼트·위키 볼트의 프론트매터 규약에 맞춰 넘겨준다.** 앱은 "인박스 레인에 내려놓기"까지만, 이후 라우팅은 볼트 쪽 CMDS Process 관할.
- 이 문서의 §1–§3은 점검 결과, §4–§6은 이번 세션에서 **구현 완료**한 내용, §7은 후속 제안.

---

## 0. 한 줄 요약

| 영역 | 점검 결과 | 조치 |
|---|---|---|
| 품질 게이트 | pytest가 **실제 `codex` CLI를 스폰**해 무한 대기 (테스트 격리 결함) | conftest 가드 추가 → 321 tests / 1.8s |
| 자동 폴더링 | 100% 키워드 규칙, 약한 판정(기본 버킷 0.35)이 그대로 저장됨 | 규칙 → **LLM 중재**(닫힌 폴더 메뉴) 2단계, `--llm/--no-llm` |
| 볼트 프론트매터 | 7필수 필드 중 `aliases`·`date modified`·`model/effort` 누락, `CMDS:`/`index:` 없음, 날짜 형식 불일치 | 공용 빌더 `core/frontmatter.py` — 표준 100% 준수 |
| [[키워드]] 연결 | 키워드→노트 리졸버는 있으나 **노트에 쓰이지 않음** | `related:`(실존 노트만) + `keywords:` + 본문 "관련 노트" |
| LLM 인증 | Grok 기본이 API 키, Gemini 플래그 오류, 로그인 상태 확인 수단 없음 | 4사 모두 CLI OAuth 기본, `plaud llm-auth`, 앱 Settings 섹션 |

---

## 1. 오류·결함 점검 (발견 순)

### 1.1 치명 — 테스트가 실제 구독 LLM을 호출
- `uv run pytest`가 23%에서 정지. 원인: `codex exec`가 PATH에 있으면 `model_available()`이 True → 일부 경로에서 실제 `run_model` 호출. PATH에서 4개 CLI를 빼면 301개가 1.5초에 통과.
- **조치**: `tests/conftest.py`에 autouse 가드 — `summarize.subprocess.run`·`summarize.httpx.post`를 `ModelFailed`로 차단. 모델이 필요한 테스트는 `run_model`을 명시적으로 스텁해야 한다.
- 같은 결의 격리 결함: 분류 테스트가 개발자 로컬 `data/classification.json`(gitignored 개인 taxonomy)에 의존. → autouse 가드로 항상 `DEFAULT_TAXONOMY` 사용.

### 1.2 데이터 손상 — `classify --apply`가 수명주기 상태를 되돌림
- `cli/main.py` classify가 `usage_status="unused"`를 무조건 전달 → 이미 `vault-linked`/`metadata-ready`인 녹음을 재분류하면 `unused`로 강등 (`storage.upsert_note_metadata`는 None만 보존).
- **조치**: 기존 행이 있으면 `status`/`usage_status`에 None 전달.

### 1.3 분류 신뢰도 이중 계산
- `confidence = 0.5 + matches*0.12 + (score-priority)*0.005`에서 `score-priority = 5*matches - penalty` → 매치 수가 두 번 반영. 요약문만으로 맞춘 케이스가 의도보다 높게 나옴.
- **조치**: `0.5 + matches*0.12 - penalty*0.005`. 회귀 테스트 추가.

### 1.4 LLM이 낸 `category`·`key_topics`·`attendees`가 버려짐
- `templates/metadata.md`는 `category`, `key_topics`, `action_items`, `obsidian_target_hint`를 요구하지만 `metadata.py`가 `category`를 규칙 결과로 덮어쓰고 나머지는 `metadata_json`에만 잠들어 있었다 (읽는 곳 없음).
- **조치**: `key_topics`를 링크 후보로 사용(§5). `category`는 이제 📚 값(`cmds`)으로 통일.

### 1.5 taxonomy가 import 시 1회 고정
- `FOLDER_TAXONOMY` 모듈 전역 → 앱(장수 프로세스)에서 `data/classification.json` 수정이 반영 안 됨.
- **조치**: `taxonomy()` — 파일 mtime 변경 시 재로드. `reload_taxonomy()`, `rule_by_folder()` 추가.

### 1.6 Gemini CLI 호출 플래그 오류
- `gemini --prompt-interactive=false` + stdin. 공식 문서(automation 튜토리얼)상 헤드리스는 `-p <prompt>`, stdin은 *컨텍스트*로 취급. → 프롬프트가 무시되거나 대화형으로 빠질 수 있음.
- **조치**: `["gemini", "-p"]` + argv 전달(`PROMPT_AS_ARG`).

### 1.7 사소
- `dual_vault.py`의 `if "transcript" not in tags` 분기 도달 불가(항상 포함) — 빌더 통합으로 해소.
- `vault_send.py`가 `paths._safe` 프라이빗 심볼 import — 유지(리스크 낮음, 후속).
- `note_tags` PK에 `source` 없음 → 수동 태그와 AI 태그 충돌 시 재생성이 못 지움 — 후속(§7).
- ruff format 미적용 4파일 — 포맷 적용.
- Swift: 경고 0, 빌드 정상.

---

## 2. CMDS 관점 메타데이터·태그·필드 설계 (볼트 실측 기준)

### 2.1 볼트 정본에서 확인한 규약
- 정본: `.claude/rules/frontmatter-standard.md` — 필수 7필드 `type · aliases · description · author · date created · date modified · tags`.
- `description` 영어 1–2문장, **반드시 큰따옴표**. 날짜 `YYYY-MM-DDTHH:mm`(T 구분, 기존 앱은 공백 사용 → 불일치). 배열은 하이픈. 숫자만인 태그 금지.
- 에이전트 산출물은 `model:` + `effort:` 필수(2026-08-25 확장) — 없으면 Bases가 사람 노트로 오분류.
- `CMDS:` = 📚 2단계 하위분류만(📖 금지), `index:` = 🏷 인덱스 노트만. 범위별 기본 🏷 표 존재. **`🏷 Recordings` 인덱스가 이미 있음** → Plaud 레인 기본 index.
- 기존 08-1. Plaud 노트 실측: `date`(녹음일, `date created`와 구분), `source: plaud`, `plaud_id`, `dual`, `speakers`(따옴표 위키링크). 회의 노트는 `attendees`, `CMDS: "[[📚 908 …]]"`, `index: "[[🏷 Meeting Notes]]"`.
- 관계 필드: 메인 볼트는 `sourceNotes:`/`wikiVaultRelated:`, 위키 볼트는 `related:`/`source:`. 본문 링크는 항상 `[[위키링크]]`, 볼트 간 링크는 advanced-uri.
- 태그: 계층 접두어(`topic/`, `type/`) 관행 **없음**. 플랫 태그, 한글 허용, 복합어는 하이픈(`결정-지연`) — Plaud 레인이 이미 이 스타일.

### 2.2 앱이 이제 쓰는 프론트매터 (transcript 레인 예)
```yaml
---
type: transcript
aliases: []
description: "Dual-transcribed final transcript: 08-27 대학도서관의 AI 혁신 … Cross-analyzed from Plaud and ElevenLabs Scribe with confirmed speaker names."
author:
  - "[[구요한]]"
model: "claude-fable-5"
effort: "medium"
date created: 2026-09-03T11:40
date modified: 2026-09-03T11:40
date: 2026-08-27
tags:
  - plaud
  - transcript
  - lecture
  - AI-Ready-Data
CMDS: "[[📚 840 Lectures]]"
index: "[[🏷 Lecture Notes]]"
status: inProgress
source: plaud
plaud_id: e96d88d2ffca1d6b0bc7c6dc7dd84b0d
plaud_url: https://web.plaud.ai/file/e96d88d2ffca1d6b0bc7c6dc7dd84b0d
dual: true
speakers:
  - "[[유석현]]"
keywords:
  - "대학도서관"
  - "AI Ready Data"
related:
  - "[[유석현]]"
reuse-channels:
  - lecture:flagged
---
```
- `keywords:`는 원문 키워드(링크 아님, 검색·Bases용). `related:`는 **볼트에 실존하는 노트만** 위키링크 — 깨진 링크 0 원칙. 본문 끝에 `## 관련 노트` 섹션으로 그래프에도 연결.
- `CMDS:`/`index:`는 taxonomy 규칙에서 옴(§4). 인박스 단계라도 넣어두는 이유: Bases·Dataview가 인박스에서부터 카테고리 축으로 필터할 수 있고, 회의록화 시 그대로 상속.

### 2.3 태그 정책 (3층)
1. **레인 태그**: `plaud`, `transcript`|`meeting`|`lecture`… (자동, 고정)
2. **폴더 태그**: taxonomy 규칙의 `tags` (예: `Lecture`, `AI교육`) — 폴더와 1:1
3. **주제 태그**: Plaud 키워드 상위 8 + LLM `topic_tags`(3–8, 한글 하이픈) — `normalize_tags` + 숫자만 제거
- 총 20개 상한. `#`·공백·`/` 없음. 수동 태그(`source=manual`)는 재생성 시 보존.

---

## 3. LLM 인증 — OAuth 4사 통일

| provider | CLI | 로그인 | 상태 확인 | 이전 기본 | 이제 |
|---|---|---|---|---|---|
| Claude | `claude` | `claude auth login` (Max) | `claude auth status` JSON | cli | cli |
| ChatGPT/Codex | `codex` | `codex login` | `codex login status` / `~/.codex/auth.json` | cli | cli |
| Gemini | `gemini` | `gemini` 1회 실행 → Login with Google | `~/.gemini/oauth_creds.json` | cli(플래그 오류) | cli(`-p`) |
| Grok | `grok` | `grok login` (SuperGrok) | `~/.grok/auth.json` | **api** | **cli** |

- `summarize._run_cli`가 provider별 API 키 env(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `XAI_API_KEY`)를 **자식 프로세스에서 제거** → CLI가 env 키 대신 OAuth 세션을 쓴다 (기존엔 Grok만).
- 앱은 비밀번호·토큰을 절대 다루지 않는다. 각 벤더 CLI의 OAuth 플로우·크리덴셜 캐시를 **조회만** (`core/llm_auth.py`).
- 실측(2026-09-03): Claude Max ok · ChatGPT ok · SuperGrok ok · **Gemini CLI 미설치** → `npm i -g @google/gemini-cli` 후 `gemini` 실행해 Google 로그인하면 끝.

---

## 4. 구현 — 자동 폴더링 v2

```
snapshot ──▶ classify_snapshot (rules)  ──confidence ≥ 0.6──▶ 확정 (source=rule)
                    │
             < 0.6 or default
                    ▼
          classify_with_assist ──▶ templates/classify.md ──▶ LLM (classify_model)
                    │  닫힌 폴더 메뉴 + 규칙 판정·차점 후보 제시
                    ▼
          folder_name ∈ taxonomy ? ──yes──▶ 확정 (source=llm, conf ≤ 0.9, topic_tags, keywords)
                    │ no
                    ▼
                규칙 판정 유지 (llm_error 기록)
```
- 모델은 폴더를 **발명할 수 없다**(메뉴 밖 답 → 규칙 유지). 신뢰도 0.5–0.9로 클램프(규칙 최대 0.95). 폴더 *이동*은 여전히 `--apply`/min-confidence 게이트 뒤.
- taxonomy 규칙에 `cmds`·`index`·`vault_dest`·`hint` 필드 추가. `plaud taxonomy-upgrade`가 사용자 `data/classification.json`에 누락 필드를 채움(백업 `.bak`, 기존 값 보존). 실행 완료: 12개 폴더 갱신.
- 실측: "08-27 대학도서관의 AI 혁신…" — 규칙 0.35 `10. Meetings` → Claude 중재 0.9 `11. AI 강의` (📚 840 · 🏷 Lecture Notes), 근거: "충남대 유석현 교수 30분 발표, 사회자 소개·질의응답 구조". 15초.
- 설정: `classify_llm_assist`(기본 on), `classify_llm_threshold`(0.6), `plaud config-classify-assist`, `plaud classify --llm/--no-llm`.
- 앱: 분류 미리보기 행에 📚/🏷 표시 + 보라색 **LLM** 배지(중재 결과).

## 5. 구현 — 연결(Connection)

- `vault_index.related_wikilinks(terms)`: 제목 정확일치(1.0) → alias(0.9)만 채택(태그 0.7은 기본 제외). 후보 = Plaud 키워드 + LLM `key_topics`/`keywords` + 화자 실명. 최대 8개.
- `metadata_json`에 `cmds`, `index`, `vault_dest`, `classification_source`, `classification_alternatives`, `link_candidates`, `related` 저장 → 앱/CLI/볼트 송출이 같은 값을 본다.
- `vault_send`(요약 노트)와 `dual_vault`(전사본)가 `core/frontmatter.CmdsFrontmatter` 하나를 쓴다. `note_context()`가 저장된 메타데이터 우선, 없으면 규칙 분류로 CMDS/index/keywords/related/speakers를 채움.

## 6. 구현 — 인증

- `core/llm_auth.py`: `inspect_all()` → provider·backend·CLI 설치·OAuth 로그인·계정·API키 env·ready. `launch_login(provider)`는 현재 터미널에 벤더 로그인 실행(API 키 env 제거).
- CLI: `plaud llm-auth [--json]`, `plaud llm-auth-login <provider>`.
- 앱 Settings › **LLM OAuth login** 섹션: 4행 상태 + 미완 시 실행할 명령 표시 + Refresh.
- 기본 backend 4사 모두 `cli`. (기존 `data/config.json`에 `grok: api`가 남아있으면 `plaud config-backend grok cli`.)

## 7. 후속 제안 → 같은 날 구현 완료 (2026-09-03 오후)

1. ✅ 회의록 노트(`fallback_meeting_note`)도 `CmdsFrontmatter` 사용 — `attendees`·`CMDS`·`model/effort`·`keywords`·`related` 일관.
2. ✅ 수동 태그 우선: `add_note_tags(source="manual")`이 기존 ai/auto 행을 manual로 승격 → 재생성 시 보존.
3. ✅ 위키 볼트 송출(`vault-send --to wiki`)은 `related:` 대신 `source-vault:` + `mainVaultRelated:`(advanced-uri) — 볼트 간 위키링크 금지 규약 준수.
4. ✅ Bases 뷰 `templates/plaud-lane.base` + `plaud vault-base` (Inbox 미처리 / CMDS별 / 재사용 후보 / 듀얼 전사본 4개 뷰). 볼트 08-1 레인에 설치 완료.
5. ✅ `plaud vault-lint [--fix]` — 08-1 레인 노트의 공백 날짜·미인용 description·bare 위키링크·숫자 태그·`date modified`/`aliases`/`model` 누락을 검사, `--fix`는 프론트매터 라인만 국소 수정(본문·키 순서 불변).
6. ✅ 앱 분류 미리보기: 차점 폴더 드롭다운 → `classify --apply --folder <name>`(source=manual, conf 1.0).
7. ✅ Gemini CLI 0.58.0 설치. 남은 1회 작업: 터미널에서 `gemini` 실행 → "Login with Google" → `plaud llm-auth`로 4/4 확인.

## 8. 변경 파일

- 신규: `core/auto_folder.py`, `core/frontmatter.py`, `core/llm_auth.py`, `core/vault_lint.py`, `templates/classify.md`, `templates/plaud-lane.base`, `tests/test_auto_folder.py`, `tests/test_frontmatter.py`, `tests/test_llm_auth.py`, `tests/test_vault_lint.py`
- 수정: `core/classification.py`(필드·lazy taxonomy·신뢰도), `core/metadata.py`(assist·related), `core/vault_send.py`, `core/dual_vault.py`, `core/vault_index.py`(`related_wikilinks`), `core/summarize.py`(gemini `-p`, env strip 4사), `core/app_config.py`(3 설정), `cli/main.py`(classify/folder-plan/taxonomy-upgrade/llm-auth/config-classify-assist), `tests/conftest.py`(모델 차단·taxonomy 격리), `tests/test_classification.py`, `app/…/FileStore.swift`, `app/…/ContentView.swift`
- 게이트: `pytest` 326 passed · `ruff check` · `ruff format --check` · `swift build`
