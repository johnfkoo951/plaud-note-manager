---
date created: 2026-06-15T13:46
date modified: 2026-09-06
---
# Plaud Access Layers — Web · Desktop · MCP · CLI · Skill · App

Plaud Cloud 데이터에 접근·조작·생산하는 여섯 가지 채널을 한 곳에 정리. 어떤 작업을 어떤 채널로 해야 하는지 결정할 때 본 문서를 SSOT로 사용한다.

- 최초 작성: 2026-05-20
- 최신 갱신: 2026-09-06 (**공식 CLI/MCP 배포 코드 재검증 + 앱 0.8.2 읽기 어댑터·인증 경로 반영** — CLI 0.3.11, MCP 0.3.10)
- 대상 저장소: `~/DEV/plaud-note-manager`
- 공개 랜딩: <https://plaud.cmdspace.work> — `web/` 서브폴더 + Vercel 배포
- Obsidian 요약: `<your-obsidian-vault>/70. Outputs/74. Projects/Plaud Note Manager/2026-05-20-plaud-access-layers.md`

## 0. 채널의 두 가지 역할 — Capture vs Manage

| 역할 | 의미 | 해당 채널 |
|---|---|---|
| **Capture** | 신규 녹음을 *생성* | Plaud Note 디바이스 (HW) · **Desktop** |
| **Manage** | 이미 클라우드에 있는 녹음을 *읽기 / 편집 / 가공* | **Web** · **Desktop** · **MCP** · **Skill** · **App** |

Desktop은 두 역할 모두 수행하는 유일한 소프트웨어 채널 — Plaud Note HW 없이도 Zoom/Meet/Teams 등 데스크톱 회의 오디오를 시스템 레벨에서 캡쳐해 Plaud 클라우드에 업로드한다.

## 1. 여섯 가지 채널 개요

| 축 | 위치 | 인증 방식 | 인터페이스 | 공식성 | 역할 |
|---|---|---|---|---|---|
| **Web** | `https://web.plaud.ai` | 브라우저 세션 | UI 클릭 | ✅ 공식 | Manage |
| **Desktop** | Plaud Desktop App (Mac Intel/Apple Silicon · Windows) | Plaud 계정 로그인 (앱 내장) | 네이티브 GUI | ✅ 공식 | **Capture + Manage** |
| **MCP** | `mcp__plaud__*` (MCP 연결 클라이언트) | 독립 OAuth (자동 refresh) — 로컬 서버는 `~/.plaud/tokens-mcp.json` | LLM tool call | ✅ 공식 | Manage (녹음 읽기) |
| **CLI** | `@plaud-ai/cli` (npm 전역 `plaud`) | 독립 OAuth 브라우저 로그인 — `~/.plaud/tokens.json` 자동 갱신 | 터미널 명령 | ✅ 공식 (changelog GA 2026-05-12 · 소개 글 08-24) | Manage (읽기 전용) |
| **Skill** | `~/.claude/skills/plaud-cloud-tools/` | cURL 캡쳐 → `.env` | bash 스크립트 | ⚠️ 비공식 (web API 리버스) | Manage |
| **App** | `~/DEV/plaud-note-manager/` | Web 쓰기: Keychain workspace pair + 앱 전용 WebKit session; 공식 읽기: 별도 CLI OAuth | Python CLI · SwiftUI macOS 앱 (agent 실행기는 향후) | Web API 비공식 + 공식 CLI 읽기 어댑터 | Manage |

> [!important] 명령 이름 규약 — `plaud` 는 두 개다
> **공식 CLI**의 실행 명령도 `plaud`, 본 저장소 **App**의 실행 명령도 `plaud` 다.
> `transcript` · `summary` · `search` 는 **이름이 같고 동작이 다르다**.
> - 이 문서에서 **App 열의 `plaud X` 는 프로젝트 `.venv/bin/python -m cli.main X`** 를 뜻한다 (`uv run plaud X`도 개발용 진입점).
> - 이 Mac의 전역 `plaud`는 공식 CLI지만 PATH 순서는 환경마다 다르다. 앱은 검증한 공식 package와 전용 Node의 절대 경로를 사용한다.
> 공식은 `PLAUD_API_BASE`, App은 `PLAUD_BASE_URL`/`PLAUD_AUTHORIZATION`을 사용한다. 인증 파일·OAuth grant를 공유하거나 복사하지 않는다.

App 구조 요약:

```
plaud-note-manager/
├── core/    # 공유 Python 라이브러리 (API 클라이언트·SQLite·메타데이터)
├── cli/     # typer CLI: uv run plaud <command>
├── skill/   # Claude Code skill (얇은 래퍼)
├── agent/   # 향후 자율 자동화를 위한 운영 레시피 (실행기·scheduler 미구현)
├── app/     # SwiftUI macOS 앱 (GRDB)
├── web/     # 공개 랜딩 (plaud.cmdspace.work · Vercel)
├── tests/   # pytest 스위트
└── data/    # SQLite, 다운로드, 메타 캐시
```

핵심 원칙: 인증·API 호출·메타 저장은 `core` 한 곳에만. 현재 CLI/skill/app과 향후
agent가 같은 core 계약을 공유한다.

## 2. Capture 능력 (Desktop 전용)

| 기능 | Web | Desktop | MCP | CLI | Skill | App |
|---|---|---|---|---|---|---|
| Zoom / Meet / Teams 자동 감지 + 녹음 | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| 시스템 오디오 캡쳐 (봇 참여 없이) | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| 헤드폰 착용 상태에서도 녹음 | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| 녹음 중 오디오 하이라이트 (AI 우선순위 마킹) | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| 녹음 중 스크린샷 (슬라이드·다이어그램) | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| 녹음 중 텍스트 노트 입력 (AI 컨텍스트) | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| AutoFlow (전사+요약+전달 자동 파이프라인) | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| 외부 오디오 파일 import | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| 자체 STT 재전사 (ElevenLabs Scribe 등) | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |

## 3. 읽기 능력 비교

| 데이터 | Web | Desktop | MCP | CLI | Skill | App |
|---|---|---|---|---|---|---|
| 파일 리스트 (id/name/duration/date) | ✅ | ✅ | ✅ `list_files` | ✅ `plaud files` | ✅ `list-files.sh` | ✅ `plaud sync` (+ SQLite 캐시) |
| `is_trans` / `is_summary` / `is_markmemo` 플래그 | △ UI 표시 | △ UI 표시 | ❌ | ✅ `plaud file` (보유 불리언) | ✅ | △ 캐시 후 derived (raw flag 미노출) |
| 트랜스크립트 (`transaction`) — 112개 언어 + 스피커 라벨 | ✅ | ✅ | ✅ `get_transcript` (+페이지네이션) | ✅ `plaud transcript` (`-o` 저장) | ✅ `transcript.sh` | ✅ `plaud transcript` |
| 정제 전사 (`transaction_polish`) — 화자 **실명** 반영 | ✅ | ✅ | ✅ `get_transcript block=transaction_polish` | ✅ `transcript --polished` | ✅ | ✅ |
| AI 요약 (`auto_sum_note`) | ✅ | ✅ | ✅ `get_note` | ✅ `plaud summary` (`-o` MD) | ✅ `summary.sh` | ✅ `plaud summary` |
| 다중 요약 / 템플릿 탭 (`consumer_note`) — Multidimensional | ✅ | ✅ | ✅ `get_note` 탭 배열·링크 본문 해석 | ✅ `summary --json` / `--all` (아래 한계 참조) | ✅ | ✅ |
| Outline | ✅ | ✅ | ✅ `get_transcript block=outline` | ✅ `transcript --block outline` | ✅ | ✅ `plaud outline` |
| Highlight (`high_light`) | ✅ | ✅ | △ `block=mark_memo` (누른 녹음만) | △ `transcript --highlights` (`mark_memo`) | ✅ | ✅ (`plaud contents`) |
| MarkMemo / Consumer Note (사용자 메모) | ✅ | ✅ | △ 템플릿 탭 `get_note` · markMemo `block=mark_memo` | △ `summary --json` / `transcript --block mark_memo` | ✅ | ✅ |
| 오디오 MP3 | ✅ | ✅ + 로컬 캐시 | △ 24h presigned URL | ✅ `plaud audio` (24h URL) | ✅ `download-file.sh` | ✅ `plaud download` |
| 스크린샷 (Desktop 녹음 시 첨부된 이미지) | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Ask Plaud** (자연어 Q&A, 인용 포함) | ✅ | ✅ | △ 도구 조합으로 유사 구현 · **저장된 답변 탭**은 `get_note`로 읽기 | ❌ | ❌ | △ `plaud cmds-summarize` 등으로 우회 |
| 폴더 트리 (`/filetag/`) | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ `plaud folders` |
| 인증 사용자 정보 | ✅ | ✅ | ✅ `get_current_user` (+`workspace_id`/`member_id` ← 2026-09-02) | ✅ `plaud me` | ❌ | ✅ `plaud auth` (Keychain, ID mask, optional live probe) |

**2026-09-06 배포 코드 확인:** CLI 0.3.11의 `summary --json`은 탭 배열을 보존하지만 `data_link` 본문은 가져오지 않는다. `--all`은 `auto_sum_note`가 없을 때 다른 탭이 있어도 조기 반환할 수 있다. MCP 0.3.10 `get_note`는 링크 본문을 해석한다. 전사는 본문이 없어도 CLI exit 0일 수 있어 export 파일 존재·내용을 확인해야 한다. [CLI 배포물](https://registry.npmjs.org/@plaud-ai/cli/-/cli-0.3.11.tgz), [MCP 배포물](https://registry.npmjs.org/@plaud-ai/mcp/-/mcp-0.3.10.tgz)

## 4. 쓰기 / 편집 능력

| 작업 | Web | Desktop | MCP | CLI | Skill | App |
|---|---|---|---|---|---|---|
| **세션 제목 변경** | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ `plaud rename` · 우클릭 "Rename…" (2026-05-20 추가) |
| **폴더 생성** | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ `plaud folder-create` → `POST /filetag/` |
| **폴더 이름변경 / 색 / 아이콘** | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ `plaud folder-rename` → `PATCH /filetag/{id}` |
| **폴더 삭제** | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ `plaud folder-delete` → `DELETE /filetag/{id}` |
| **파일을 폴더에 할당 / 이동** | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ `plaud move` → `PATCH /file/{id}` (`filetag_id_list`) |
| **AI 기반 자동 분류 → 폴더 배치** | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ `plaud classify --apply` (앱은 미리보기 + 선택 적용 + `classify-undo`) |
| **폴더 배치 계획 미리보기** | — | — | ❌ | ❌ | ❌ | ✅ `plaud folder-plan` · 앱 자동분류 미리보기 시트 |
| **전체 내용 검색** (제목·전사본·요약) | △ | △ | ❌ | ❌ | ❌ | ✅ `plaud search` (FTS5 trigram, 한국어 부분 일치) |
| **태그 정리 / 핀 / 중첩 뷰** | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ `plaud tag-add` · `tags-all` · `tag-pin` |
| 스피커 라벨 편집 (Plaud 서버) | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ `plaud plaud-relabel` (서버 trans_result PATCH, 2026-06-11) |
| 트랜스크립트 텍스트 수정 (Plaud 서버) | ✅ | ✅ | ❌ | ❌ | ❌ | △ (같은 trans_result 엔드포인트로 가능 — 앱은 화자 변경만 노출) |
| 세션 삭제 / 휴지통 | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| 공유링크 생성 | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **녹음 중 실시간 라벨 / 하이라이트** | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **AutoFlow 정책 설정** (회의 종료 후 자동 송출) | △ | ✅ | ❌ | ❌ | ❌ | ❌ |

## 5. 통합 / 내보내기

| 항목 | Web | Desktop | MCP | CLI | Skill | App |
|---|---|---|---|---|---|---|
| Export 형식 가짓수 | 여러 종 | **27+ 형식** (Plaud 마케팅 표기) | — (필요 시 LLM 변환) | △ `-o` 로 txt/md 저장 | △ 트랜스크립트 JSON · 요약 MD | △ 트랜스크립트 / 요약 / 오디오 + Obsidian 송출 |
| Zapier | ❌ | ✅ | ❌ (대신 MCP→다른 MCP 체인) | ❌ | ❌ | ❌ |
| Notion / Slack / 이메일 직결 | △ 수동 | ❌ (Zapier 경유) | ✅ `plaud-export` 체인 | ❌ | ❌ | ✅ `plaud meeting-note` · `plaud obsidian` (Obsidian 직결) |
| 자체 LLM 후처리 (Grok/Claude/Codex/Gemini) | ❌ | ❌ | △ 사용자 컨텍스트 내 | ❌ | ❌ | ✅ `plaud metadata-generate` · `cmds-summarize` |
| 팀 공유 | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| 커스텀 어휘 (Custom Vocabulary) | △ | ✅ | ❌ | ❌ | ❌ | ❌ |

## 6. App 전용 로컬 메타 (Plaud 서버에는 미동기화)

App은 Plaud (및 Desktop) 이 지원하지 않는 사용자 워크플로우용 메타를 로컬 SQLite에 별도 보존한다.

| 항목 | App 명령 |
|---|---|
| Obsidian-style 태그 | `plaud tag-add` / `plaud tag-remove` |
| `usage_status` (unused / metadata-ready / vault-linked …) | `plaud usage-status` |
| 자체 스피커 재라벨 (ElevenLabs Scribe + saved speakers) | `plaud cmds-relabel` |
| 자동 메타데이터 생성 (Grok / Claude / Codex / Gemini) | `plaud metadata-generate` · **상시 자동**: sync 후 신규 파일 자동 생성 (`auto_metadata` 기본 on, 기본 모델 `metadata_model: codex` = GPT 구독 경로; 수동 배치는 `plaud metadata-auto [--backfill]`) |
| Obsidian 회의록 · 강의록 생성 | `plaud meeting-note` · `plaud obsidian` |
| 자체 트랜스크립션 (Plaud STT 대체) | `plaud cmds-transcribe` |
| 자체 요약 · 통합 | `plaud cmds-summarize` · `plaud cmds-integrate` |
| 슬롯 · 템플릿 · 모델 프리셋 | `plaud slots` · `plaud templates` · `plaud models` |

## 7. 채널 선택 라우팅 (의사결정 가이드)

### 7.1 신규 녹음 만들기 (Capture)

| 상황 | 추천 |
|---|---|
| Zoom / Meet / Teams 회의 자동 녹음 | **Desktop** (자동 감지 + 시스템 오디오) |
| 데스크톱 화면 강의 / 데모 녹화 | **Desktop** (오디오 + 스크린샷) |
| 오프라인 회의 (대면) | Plaud Note HW (디바이스) |
| 기존 오디오 파일 (.mp3 등) 처리 | **Desktop** import 또는 **App** `cmds-transcribe` (ElevenLabs) |

### 7.2 기존 녹음 다루기 (Manage)

| 목적 | 추천 |
|---|---|
| Claude 대화 중 자연어로 "최근 녹음 / 요약" | **MCP** |
| **터미널·스크립트·크론에서 전사/요약 파일 뽑기** | **CLI** (공식 · `plaud summary <id> -o s.md`) |
| 화면 없는 서버(Mac mini·홈서버)에서 정기 추출 | **CLI** (독립 OAuth 초기 연결·갱신 실패 처리 필요) |
| 빠른 GUI 브라우징 + 인앱 Q&A (Ask Plaud) | **Desktop** 또는 **Web** |
| 오디오 일괄 백업 (다수 파일) | **CLI** (공식 우선) · **Skill** · **App** |
| `is_summary=true` 인 것만 일괄 처리 | **Skill** |
| 사용자 markMemo · highlight 접근 (API) | **MCP** `block=mark_memo` 또는 **CLI** `--highlights`; 보유 블록 확인 |
| 노션 / 이메일 / Slack 등 다운스트림 푸시 | **MCP** → `plaud-export` skill 체인 (자동) · 또는 **Desktop** → Zapier |
| **폴더 만들기 · 옮기기** | **Web** · **Desktop** · **App** (셋 다 가능) |
| **AI 기반 자동 폴더 분류** | **App** (유일 경로 — 규칙 taxonomy → 약한 판정만 LLM 중재, v0.8) |
| **세션 제목 변경** | **Web** · **Desktop** · **App** |
| Plaud 서버에 스피커 라벨 반영 | **App** `plaud-relabel` 또는 **Web · Desktop** |
| Obsidian 회의록 / 강의록 생성 | **App** (`plaud meeting-note` · `plaud obsidian`) |
| 자체 STT 재전사 · 자체 스피커 라벨 (로컬 보존) | **App** (`cmds-transcribe` · `cmds-relabel`) |
| 세션 삭제 / 휴지통 | **Web** 또는 **Desktop** |
| 27+ 형식 export | **Desktop** |
| **폴더 / 태그 조회** | **Web · Desktop · App** (CLI·MCP는 폴더를 못 본다) |

전체 라이브러리 수집에는 **필터 없는 MCP pagination → 로컬 검색**을 사용한다. CLI `search`와 필터 있는 MCP `list_files`는 최근 500개까지 검사하며, CLI `recent`는 최대 300개·`today`는 최대 50개다. `files`/`file`의 잘린 사람용 표는 JSON 캐시로 파싱하지 않는다. 긴 전사는 MCP `next_cursor`가 없어질 때까지 수집한다. 구체적인 자동화 설계와 근거는 [`REVIEW-2026-09-06-AUTH-ACCESS.md`](REVIEW-2026-09-06-AUTH-ACCESS.md)에 있다.

## 8. 데이터 모델 차이 노트

### 8.1 Plaud API 필드 이름 비일관성
- `/file/simple/web` 응답 → `filename`
- `/file/detail/{id}` 응답 → `file_name`
- App `PlaudFile` 모델은 `filename` 사용 → PATCH 키도 `filename` 으로 가정 (`rename_file`)
- 향후 만약 PATCH 가 거부되면 DevTools로 web rename 요청 캡쳐해 키 재확인 필요

### 8.2 MCP `get_file` 응답의 9개 top-level 키
```
created_at · duration · id · name · note_list · presigned_url ·
serial_number · source_list · start_at
```
- **폴더 / 태그 / `filetag_id_list` 가 전혀 노출되지 않음** → MCP만으로는 폴더 구조 무지.
- `source_list[*].data_type`: `transaction` (트랜스크립트) · `outline`
- `note_list[*].data_type`: `auto_sum_note` · `consumer_note` (템플릿 탭 · Ask Plaud 저장 답변 — 탭당 1건, `data_tab_name`으로 구분) — 세션마다 가변

### 8.2.1 MCP 확장 (2026-09-02 실측)

MCP는 여전히 7 tools · read-only 지만, 두 도구의 범위가 넓어졌다.

- **`get_transcript(block=…)`** — `transaction`(기본) · `transaction_polish` · `outline` · `mark_memo`.
  - `transaction_polish`: 문장 정제 + **`speaker`에 실명** (`original_speaker`는 `Speaker N` 유지) → §8.3의 "MCP는 화자 익명" 한계가 부분 해소.
  - `mark_memo`: 디바이스 하이라이트 버튼을 누른 녹음에만 존재. 미보유 시 가용 블록 목록을 응답으로 알려줌 (탐색에 활용 가능).
  - 커서 페이지네이션: `limit`(≤500) · `next_cursor` · `total`/`offset`/`returned` → 장시간 녹음의 페이로드 제어 가능.
- **`get_note` 멀티탭** — 앱 탭 하나당 배열 항목 하나. 실측 예(1개 강의 파일): `auto_sum_note`/Summary + `consumer_note`/Intent Analysis + Lecture Summary + Speech Summary = 4탭. `consumer_note`엔 `data_link`(presigned **300초**)가 붙고 `data_error_code`가 문자열 `"1"` (auto_sum은 정수 `10`).
- **`get_current_user`에 `workspace_id`/`member_id`** — App이 리버스한 2-tier workspace 구조(§10)가 공식 채널에도 노출.

여전히 미노출: 폴더/태그(`filetag_id_list`) · `is_trans`/`is_summary` 플래그 · 스크린샷 첨부물.

> [!warning] 출처 주의 — 이 절은 공식 문서에 없다
> 2026-09-03 <https://docs.plaud.ai/plaud-mcp-cli/mcp> 전문 대조 결과, 위 확장(`block`·페이지네이션·멀티탭·`workspace_id`)은
> **공식 문서에 한 줄도 기재돼 있지 않았다.** 당시 근거는 라이브 호출 실측이었다.
> → Plaud MCP 의존 코드는 분기마다 **설치된 tool schema 자체를 diff** 해서 재검증할 것. 문서 갱신을 기다리면 늦는다.

2026-09-06에는 공식 MCP 0.3.10 배포 코드에서도 블록·cursor·다중 탭 처리를 확인했다. 다만 소스의 지원 옵션은 모든 녹음에서 해당 콘텐츠가 존재한다는 증거가 아니다.

### 8.3 스피커 정보 구조
- 각 transcript segment: `{ start_time, end_time, content, speaker, original_speaker }`
- `speaker` ≠ `original_speaker` 인 경우 사용자가 라벨 편집한 것 (Plaud 서버 측, Web 또는 Desktop 에서 가능)
- App의 `cmds-relabel` 은 로컬 SQLite 의 `cmds_segments` 만 수정 — Plaud 서버 미반영

### 8.4 Desktop이 만든 녹음의 추가 메타 (추정)
- 시스템 오디오 vs 마이크 vs 혼합 식별 필드 가능성
- 첨부된 스크린샷·텍스트노트의 `data_type` 은 미관측 — 향후 DevTools 또는 MCP `get_file` 응답 검증 필요
- HW (Plaud Note) 녹음과 동일한 `file_id` 체계 공유 — `serial_number` 가 디바이스/Desktop 식별자 역할로 추정

## 9. Plaud Web URL 규칙

Plaud 세션은 `file_id` 가 영구 식별자 — 제목을 바꿔도 URL 은 동일하게 유지.

```
https://web.plaud.ai/file/{file_id}
```

예: `https://web.plaud.ai/file/07a961a46137012b9e1928bb9c088f6c`

Desktop 으로 만든 녹음, HW 로 만든 녹음, App `cmds-transcribe` 결과 모두 같은 URL 패턴 적용 (cloud sync 후).

App 에서 사용:
- CLI: `uv run plaud web <file-id>` — URL 출력
  - `--open` / `-o`: 기본 브라우저로 열기
  - `--copy` / `-c`: macOS 클립보드 복사
- SwiftUI:
  - 상세 헤더에 Safari 아이콘 버튼 (제목 옆)
  - 파일 우클릭 → "Open in Plaud Web"
  - 파일 우클릭 → "Copy Plaud URL"
  - 내부적으로 `FileStore.openInPlaudWeb(_:)` / `copyPlaudWebURL(_:)` 사용

## 10. 인증 토큰 / 자격증명 위치

| 채널 | 파일 / 저장소 |
|---|---|
| Web | 브라우저 쿠키 |
| Desktop | 앱 내장 (계정 로그인 후 OS keychain 으로 추정) |
| MCP | 로컬 공식 서버는 `~/.plaud/tokens-mcp.json` (독립 OAuth 자동 refresh) |
| **CLI (공식 `@plaud-ai/cli`)** | `~/.plaud/tokens.json` (OAuth, 자동 갱신) · 설정 `~/.plaud/cli.yaml` |
| Skill `plaud-cloud-tools` | `~/.claude/skills/plaud-cloud-tools/.env` |
| App `plaud-note-manager` | workspace 자격증: macOS Keychain 단일 항목 · 계정 HttpOnly 세션: 영속 WebKit store |

**App 헤드리스 자동 갱신 (2026-07 신규)**: Plaud web 인증은 2-tier OAuth이며
`POST {domain}/user-app/auth/workspace/refresh/{wid}` 가 24h 토큰을 재발급한다.
워크스페이스 refresh token을 1회 부트스트랩하면 (Embedded Web Login이 자동
캡쳐, 또는 devtools `copy(localStorage.getItem("workspaceList"))` →
`plaud ws-bootstrap`) 이후 모든 CLI 명령이 만료 6h 전에 토큰을 자동 갱신한다.
refresh token은 매 사용 시 로테이션 → Keychain 단일 JSON 항목에 원자적으로
영속화, 동시 갱신은 공용 `auth.lock` flock으로 직렬화. 수동 강제:
`plaud ws-refresh`. 끄기:
`PLAUD_AUTO_REFRESH=0`.

**App 계정-세션 무인 복구 (2026-08-27)**: Plaud가 workspace refresh chain을
표시 만료일 전에 `-419/-420`으로 폐기해도, 앱은 필요할 때만 숨은
`WKWebsiteDataStore.default()`를 띄워 HttpOnly 계정 세션으로 새 workspace
token 쌍을 발급한다. 새 쌍은 WebKit localStorage와 Keychain에 같은 세대로
저장하고 원래 명령을 1회 재시도한다. 계정 세션까지 만료된 경우에만
상호작이 필요한 Plaud Web Login을 연다. `pld_ut`/`pld_urt`는 WebKit 밖으로
복사하지 않는다.

**App 브라우저 복구 (2026-09-06 경계 정리)**: 일상적인 자동 복구는 Keychain의
workspace refresh → **앱 전용 영속 WKWebView 세션** 순서다. cURL import는
검증한 access를 저장하지만 자동으로 Chrome 프로필을 읽지 않는다. Auth 시트의
"자동 갱신 연결"은 앱 전용 Web Login을 사용한다.

기존 Chrome 세션에서 인증 자료를 읽는 기능은 설명이 있는 **"Chrome 연결" 버튼을
직접 선택하는 경우**에만 실행한다. CLI의 `auth-recover`는 아래 드라이버를 유지하지만
그 존재가 앱의 자동 실행을 뜻하지 않는다. 브라우저 보안 설정은 변경하지 않는다.
각 단계는 capture → 서버 검증 → bootstrap 순서다.

| 순위 | 드라이버 | 조건 | 비고 |
|---|---|---|---|
| 1 | `chrome-disk` | 사용자가 기존 Chrome 인증 자료 연결을 명시적으로 선택 | Chrome localStorage LevelDB 파싱 (`ccl_chromium_reader`). Chrome 실행/보안 설정 변경 불필요 |
| 2 | `chrome` | Chrome 실행 + View→Developer→"Allow JavaScript from Apple Events" | AppleScript `execute javascript` — 라이브 세션이라 디스크 사본이 로테이션돼 낡았을 때의 보루 |
| 3 | `cmux` | cmux 브라우저에 Plaud 로그인 | `cmux browser open/wait/eval` |
| 4 | Tier 2 | 사용자 1회 로그인 | 앱 Auth 시트 Embedded Web Login |

`FileStore+Auth.selfHealAuthIfNeeded`는 토큰 세대별로 자동 복구를 제한하며,
네트워크·로컬 저장소 오류를 로그인 만료로 단정하지 않는다. 아직 유효한 cURL
access만 있는 경우 현재 사용을 계속 허용하고 자동 갱신 연결을 따로 안내한다.

공식 CLI/MCP는 자신의 OAuth token을 만료 60초 전부터 갱신하며, 서버의 `expires_in`을
따른다. 고정된 refresh token 수명은 확인되지 않았다. CLI와 MCP는 파일·기본 OAuth
client가 달라 인증을 복사하지 않는다. 문서의 24시간 오디오 URL 수명과 token 수명도
구분한다. [공식 CLI 설정](https://docs.plaud.ai/plaud-mcp-cli/cli)

**0.8.2 공식 읽기 연결:** `official-status`는 설치 metadata·토큰 파일 존재만 확인하고,
`--live`만 독립 OAuth 연결을 검증한다. `official-read FILE_ID`는 요약·전사를 읽어
미리보기로 반환하며 기존 DB나 Cloud를 변경하지 않는다. 9월 6일 live 진단은
MCP 인증 성공 / 최종 앱의 공식 CLI 연결·요약·전사 읽기가 성공했다. 초기 제한된 CLI 환경의 `needs_login` 결과와 달랐으므로 실행 환경을 함께 확인해야 한다. Web access 유효 여부와 별개다.

401 발생 시:
- MCP → `login` 도구 호출
- 공식 CLI → 공식 package의 `plaud login` (이 앱 CLI와 실행 경로 구분)
- Skill → web.plaud.ai 에서 cURL 재캡쳐 후 `onboard` 재실행
- App → 부트스트랩되어 있으면 자동 복구 (아무 명령이나 실행, 또는
  `uv run plaud ws-refresh`) · 체인 단절 시 앱 전용 WebKit 세션 복구 ·
  Chrome 인증 자료 연결은 Auth 시트에서 명시적으로 선택 · 미부트스트랩: 앱 Auth 버튼 (Embedded Web
  Login 권장 — 자동 갱신까지 활성화) · CLI: cURL 복사 후
  `uv run plaud refresh-auth` (클립보드 자동) ·
  최후 fallback `pbpaste | uv run plaud onboard`
- Desktop → 앱 내 재로그인

## 11. App API 엔드포인트 매핑

| App 명령 | HTTP | 경로 |
|---|---|---|
| `plaud sync` / `plaud list` | GET | `/file/simple/web` |
| `plaud detail` / `plaud contents` | GET | `/file/detail/{id}` |
| `plaud download` | GET | `/file/temp-url/{id}` → S3 |
| `plaud audio-url` | GET | `/file/temp-url/{id}` (스트리밍용 signed URL) |
| `plaud folders` | GET | `/filetag/` |
| `plaud folder-create` | POST | `/filetag/` |
| `plaud folder-rename` | PATCH | `/filetag/{id}` |
| `plaud folder-delete` | DELETE | `/filetag/{id}` |
| `plaud move` | PATCH | `/file/{id}` (`filetag_id_list`) |
| `plaud rename` | PATCH | `/file/{id}` (`filename`) ← 2026-05-20 신규 |
| `plaud web` | — | URL 패턴 출력 (API 호출 없음) |
| `plaud note-edit` | POST | `/ai/update_note_info` ← 2026-05-31 신규 |
| `plaud server-speakers` | GET | `/speaker/list` ← 2026-05-31 신규 |
| `plaud speaker-rename-server` | POST | `/speaker/sync` ← 2026-05-31 신규 |

### 11.1 2026-05-31 web 캡쳐로 발굴한 서버 쓰기 엔드포인트

DevTools 캡쳐(`Copy as cURL`)로 확인. 인증은 기존 cURL 헤더 그대로 — 즉 **App에서 호출 가능**.

- **`POST /ai/update_note_info`** — 노트(요약) 본문·제목 서버 수정. body:
  ```json
  {"file_id":"...", "note_id":"auto_sum:<hash>:<file_id>", "note_type":"auto_sum_note",
   "note_content":"<markdown>", "note_tab_name":"Summary", "note_title":"..."}
  ```
  `note_id`/`note_type`/`note_tab_name` 은 `/file/detail/{id}` 의 `note_list` 항목에서.
- **`GET /speaker/list`** — 서버 화자 로스터(성문 프로필). ⚠️ 응답 래퍼 키 미확인 — 실제 응답 캡쳐 후 파싱 확정 필요.
- **`POST /speaker/sync`** — 화자 프로필 생성/이름변경. body `{"speakers":[{speaker_id, speaker_name, speaker_type, sample_counts, embeddings, need_sync, ...}]}`. **성문 단위 rename** (해당 목소리가 나온 모든 녹음에서 이름 변경). rename 시 `list` 레코드를 그대로 echo + `speaker_name` 만 교체.

미발굴 (캡쳐 필요): 세션 삭제/휴지통(`is_trash` PATCH 추정), 공유링크 생성(`/share` 계열 추정 — 응답에 URL/토큰), 트랜스크립트 세그먼트 텍스트 수정.

## 12. 채널 조합 패턴 (실전)

### 12.1 회의 워크플로우 (가장 흔한 풀스택 시나리오)
1. **Desktop** — Zoom 회의 자동 감지 → 녹음 + 슬라이드 스크린샷
2. (자동 sync) — Plaud Cloud 업로드
3. **App** `plaud sync` 후 `plaud classify`로 미리보기 → 결과를 확인한 뒤에만
   `plaud classify --apply` — 자동 폴더 분류
4. **App** `plaud meeting-note <file-id>` — Obsidian 회의록 생성
5. **MCP** — Claude 대화 중 follow-up 이메일 초안 (`plaud-followup`)
6. **Web** — Plaud Web URL 박은 노션 페이지에서 원본 재청취

### 12.2 강의 / 컨설팅 워크플로우
1. Plaud Note HW (대면) 또는 **Desktop** (원격) 으로 캡쳐
2. **Web** 또는 **Desktop** 에서 스피커 라벨 수동 편집 (Plaud 서버 반영)
3. **App** `plaud cmds-transcribe` — 자체 STT 로 보강 (한국어 정확도 향상)
4. **App** `plaud obsidian <file-id>` — Obsidian 강의록 송출

### 12.3 빠른 검색 / 질의응답
- 시각적 브라우징 + Ask Plaud: **Desktop** 또는 **Web**
- 자연어로 Claude 대화 중: **MCP**
- 코드 / 스크립트 자동화: **Skill** 또는 **App**

## 13. 변경 이력

- **2026-09-06**: 공식 CLI 0.3.11 / MCP 0.3.10 배포물·설치 코드 재검증.
  - CLI 정제 전사·Outline·mark_memo·다중 요약 지원과 링크 본문 한계를 반영.
  - 전체 목록 수집과 최근 500개 검색을 구분하고 서버 화자 편집 라우팅 정정.
  - 0.8.2 공식 읽기 어댑터·독립 인증 상태·앱 WebKit 자동 복구와 명시적 Chrome 연결 경계 반영.
  - 공식 changelog GA **2026-05-12**, 소개 블로그 게시일 **2026-08-24**를 구분.
  - 저장소 문서만 갱신했으며 공개 랜딩은 배포하지 않음.
- **2026-09-03** (2차): 공식 문서 전문 대조 + 정정
  - <https://docs.plaud.ai/plaud-mcp-cli/mcp> · `/cli` 전문을 읽어 §3 표와 대조.
  - 정정: §3 "인증 사용자 정보" 의 CLI 열 ❌ → ✅ `plaud me` (공식 CLI에 존재).
  - 외부 링크를 현행 URL(`/plaud-mcp-cli/…`)로 교체 + `llms.txt` 색인 추가.
  - ⚠️ **확인된 문서 공백**: 공식 MCP 문서는 `get_transcript` 를 여전히
    "full transcript with timestamps and speaker labels" 로만 기술하며
    **`block` 파라미터·페이지네이션·`get_note` 멀티탭·`workspace_id` 를 전혀 언급하지 않는다**
    (§8.2.1 은 실측이 유일한 출처). rate limit·quota·plan tier 도 양쪽 문서 모두 미공개.
- **2026-09-03**: **공식 CLI를 6번째 채널로 추가** (§1 · §2–5 표에 CLI 열 삽입)
  - `@plaud-ai/cli` v0.3.11 (npm, 2026-08-20 배포 · 2026-08-24 MCP와 동시 발표).
    Node 20+, `plaud login` OAuth → `~/.plaud/tokens.json` 자동 갱신.
  - **공식 개발자 API** `https://platform.plaud.ai/developer/api` 사용 — web API 리버스가 아니다.
    설정 `~/.plaud/cli.yaml` (`api_base` · `timeout`), 환경변수 `PLAUD_API_BASE` 등.
  - 능력: `files`/`today`/`recent`/`search`(파일명, 최근 500개) · `file` · `transcript` · `summary`
    (`-o` 파일 저장) · `audio`(24h URL). **쓰기·폴더·태그·본문검색은 전부 ❌.**
  - 정정: §10의 `~/.plaud/tokens.json` 을 `plfetch 외부 도구`로 표기했던 것은 오기 — 공식 CLI의 토큰 저장소다.
  - ⚠️ **이름 충돌**: 공식 CLI 실행 명령도 `plaud`, 본 저장소 CLI도 `plaud`.
    `transcript`·`summary`·`search` 는 이름이 같고 동작이 다르다.
    본 저장소 명령은 **항상 `uv run plaud`** 로 부른다 (전역 `plaud` = 공식 CLI).
  - 함의: **읽기 전용 자동화는 더 이상 리버스가 필요 없다.** Skill(cURL)의 남은 역할은
    공식 API가 열지 않은 폴더·쓰기·markMemo와 비상구.

- **2026-09-02**: MCP 채널 능력 확장 반영 (문서만 갱신 — 코드 변경 없음)
  - `get_transcript`에 `block` 파라미터 + 커서 페이지네이션, `get_note` 탭 배열,
    `get_current_user`의 `workspace_id`/`member_id` 를 라이브 호출로 검증 (§3 · §8.2.1)
  - 기존 표기 정정: MCP 열의 다중 요약 ❌ → ✅, Outline △ → ✅, Highlight ❌ → △
  - 보고서: 볼트 `70. Outputs/74. Projects/Plaud Note Manager/2026-08-29-plaud-mcp-scope-report.md`
- **2026-08-15**: v0.6 — 자동 메타데이터 + Tier-1 인증 복구 (`docs/PLAN-v0.6.md`)
  - `plaud metadata-auto` + sync-content 훅: 신규 파일 메타데이터 상시 자동 생성
    (`auto_metadata` 기본 on · 소스 해시 멱등 · 실패 백오프 · 폴더는 제안만 ·
    과거 백로그는 `--backfill` 옵트인)
  - 메타데이터 기본 모델 분리: `metadata_model: codex` (GPT, Codex CLI 구독 인증 —
    API 키 불필요) · `plaud config-metadata-model` · 앱 Settings › Metadata 피커/토글
  - `plaud auth-recover`: cmux 브라우저 세션에서 workspaceList 재수확 →
    headless refresh 재가동 (§10 Tier-1) · 앱 Auth 시트 "Recover Now" 버튼 ·
    agent 게이트 완화 (expired 시 1회 자동 복구 시도)
- **2026-06-11**: App 에 Plaud Web Login 인증 추가 (`codex/web-login-auth` 브랜치)
  - 앱 툴바 Auth 버튼 → "Authenticate with Plaud" 시트 — browser cURL import
    (Open Plaud → Import Copied cURL) 기본 · embedded Web Login (WKWebView 캡쳐) fallback
  - CLI: `plaud auth` (오프라인 만료 카운트다운 · `--live` 실검증) ·
    `plaud refresh-auth` (클립보드 cURL · `--stdin` · `PLAUD_ENV_FILE` 존중) ·
    `plaud web-auth --stdin` (앱 내부 WKWebView 캡쳐 JSON 브리지)
  - validate-before-write: live 검증 통과 후에만 `.env` 기록 — 401/403 거부·만료
    토큰이면 `.env` 불변 (`live_auth_failed`), 네트워크 불통이면 저장 후
    `live_check_unavailable` (tri-state)
  - 보안: `.env` 0600 기록 · `.env.bak-<epoch>` 백업 제거 · 캡쳐 핸들러
    `*.plaud.ai` origin 제한
- **2026-05-22**: 공개 랜딩 페이지 배포 — <https://plaud.cmdspace.work>
  - `web/` 서브폴더 (cmdspace-web-builder v4.3 Landing 템플릿)
  - Vercel 프로젝트 `plaud` · Cloudflare DNS `plaud.cmdspace.work` CNAME
  - 페이지 구성: Hero · The Idea · 5 Channels · Capture vs Manage · Read vs Write · Routing matrix · Workflow combo · CTA
- **2026-05-22**: Plaud Desktop 축 추가 (캡쳐 + 매니지 이중 역할 반영, 채널 조합 패턴 §12 신설)
- **2026-05-20**: App 에 Plaud Web URL 연결 기능 추가
  - `cli/main.py::web` (`uv run plaud web <file-id> [--open] [--copy]`)
  - `FileStore.swift`: `plaudWebURL` · `openInPlaudWeb` · `copyPlaudWebURL`
  - `ContentView.swift`: 상세 헤더 Safari 버튼 + 우클릭 메뉴 "Open in Plaud Web" / "Copy Plaud URL"
- **2026-05-20**: App 에 세션 제목 변경 기능 추가
  - `core/client.py::rename_file` (`PATCH /file/{id}` with `{"filename": …}`)
  - `core/storage.py::set_file_name` (로컬 SQLite 동기 업데이트)
  - `cli/main.py::rename` (`uv run plaud rename <file-id> <new-name>`)
  - `app/Sources/PlaudNoteApp/FileStore.swift::renameFile`
  - `app/Sources/PlaudNoteApp/ContentView.swift` 파일 우클릭 메뉴 "Rename…" + `promptForFilename` NSAlert 헬퍼

## 14. 관련 문서

- 본 저장소: `docs/APP-FEATURES-AND-DESIGN.md` (현재 앱 기능·설계 정본), `STATUS.md` (개발 현황), `docs/STORAGE.md` (SQLite 스키마), `docs/REQUESTS.md` (인증·요청 패턴), `docs/DISCLOSURE.md` (공시 / 정책)
- 위성 위키 볼트: `20. Wiki/22. Entities/Plaud MCP Server.md`
- Obsidian 볼트 요약본: `<your-obsidian-vault>/70. Outputs/74. Projects/Plaud Note Manager/2026-05-20-plaud-access-layers.md`
- 외부:
  - <https://docs.plaud.ai/plaud-mcp-cli/changelog> — GA 2026-05-12 (문서의 v1.0.0과 npm 패키지 버전은 별도)
  - <https://www.plaud.ai/pages/plaud-desktop> — Desktop 공식 페이지
  - <https://docs.plaud.ai/plaud-mcp-cli/mcp> — MCP 개발자 문서 (현행 URL)
  - <https://docs.plaud.ai/plaud-mcp-cli/cli> — **공식 CLI 개발자 레퍼런스** (현행 URL)
  - <https://docs.plaud.ai/llms.txt> — 공식 문서 색인 (LLM 친화 목록)
  - <https://support.plaud.ai/hc/en-us/articles/57751026815257-Plaud-CLI> — 공식 CLI 지원 문서
  - <https://www.plaud.ai/blogs/news/introducing-plaud-mcp-and-cli> — MCP·CLI 발표 (2026-08-24)
  - <https://github.com/johnfkoo951/plfetch> — 외부 CLI fork
