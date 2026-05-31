# Plaud Access Layers — Web · Desktop · MCP · Skill · App

Plaud Cloud 데이터에 접근·조작·생산하는 다섯 가지 채널을 한 곳에 정리. 어떤 작업을 어떤 채널로 해야 하는지 결정할 때 본 문서를 SSOT로 사용한다.

- 최초 작성: 2026-05-20
- 최신 갱신: 2026-05-22 (Desktop 축 추가 · 공개 랜딩 배포)
- 대상 저장소: `~/DEV/plaud-note-manager`
- 공개 랜딩: <https://plaud.cmdspace.work> — `web/` 서브폴더 + Vercel 배포
- Obsidian 요약: `<your-obsidian-vault>/70. Outputs/74. Projects/Plaud Note Manager/2026-05-20-plaud-access-layers.md`

## 0. 채널의 두 가지 역할 — Capture vs Manage

| 역할 | 의미 | 해당 채널 |
|---|---|---|
| **Capture** | 신규 녹음을 *생성* | Plaud Note 디바이스 (HW) · **Desktop** |
| **Manage** | 이미 클라우드에 있는 녹음을 *읽기 / 편집 / 가공* | **Web** · **Desktop** · **MCP** · **Skill** · **App** |

Desktop은 두 역할 모두 수행하는 유일한 소프트웨어 채널 — Plaud Note HW 없이도 Zoom/Meet/Teams 등 데스크톱 회의 오디오를 시스템 레벨에서 캡쳐해 Plaud 클라우드에 업로드한다.

## 1. 다섯 가지 채널 개요

| 축 | 위치 | 인증 방식 | 인터페이스 | 공식성 | 역할 |
|---|---|---|---|---|---|
| **Web** | `https://web.plaud.ai` | 브라우저 세션 | UI 클릭 | ✅ 공식 | Manage |
| **Desktop** | Plaud Desktop App (Mac Intel/Apple Silicon · Windows) | Plaud 계정 로그인 (앱 내장) | 네이티브 GUI | ✅ 공식 | **Capture + Manage** |
| **MCP** | `mcp__plaud__*` (Claude Code MCP server) | OAuth (자동 refresh) — `~/.plaud/tokens-mcp.json` | LLM tool call | ✅ 공식 | Manage |
| **Skill** | `~/.claude/skills/plaud-cloud-tools/` | cURL 캡쳐 → `.env` | bash 스크립트 | ⚠️ 비공식 (web API 리버스) | Manage |
| **App** | `~/DEV/plaud-note-manager/` | cURL 캡쳐 → `.env` (`uv run plaud onboard`) | Python CLI · SwiftUI macOS 앱 · agent | ⚠️ 비공식 (web API 리버스) | Manage |

App 구조 요약:

```
plaud-note-manager/
├── core/    # 공유 Python 라이브러리 (API 클라이언트·SQLite·메타데이터)
├── cli/     # typer CLI: uv run plaud <command>
├── skill/   # Claude Code skill (얇은 래퍼)
├── agent/   # 자율 에이전트 (주기 sync + 후처리)
├── app/     # SwiftUI macOS 앱 (GRDB)
├── web/     # 공개 랜딩 (plaud.cmdspace.work · Vercel)
├── tests/   # pytest 스위트
└── data/    # SQLite, 다운로드, 메타 캐시
```

핵심 원칙: 인증·API 호출·메타 저장은 `core` 한 곳에만. skill/agent/app 모두 core 경유.

## 2. Capture 능력 (Desktop 전용)

| 기능 | Web | Desktop | MCP | Skill | App |
|---|---|---|---|---|---|
| Zoom / Meet / Teams 자동 감지 + 녹음 | ❌ | ✅ | ❌ | ❌ | ❌ |
| 시스템 오디오 캡쳐 (봇 참여 없이) | ❌ | ✅ | ❌ | ❌ | ❌ |
| 헤드폰 착용 상태에서도 녹음 | ❌ | ✅ | ❌ | ❌ | ❌ |
| 녹음 중 오디오 하이라이트 (AI 우선순위 마킹) | ❌ | ✅ | ❌ | ❌ | ❌ |
| 녹음 중 스크린샷 (슬라이드·다이어그램) | ❌ | ✅ | ❌ | ❌ | ❌ |
| 녹음 중 텍스트 노트 입력 (AI 컨텍스트) | ❌ | ✅ | ❌ | ❌ | ❌ |
| AutoFlow (전사+요약+전달 자동 파이프라인) | ❌ | ✅ | ❌ | ❌ | ❌ |
| 외부 오디오 파일 import | ❌ | ✅ | ❌ | ❌ | ❌ |
| 자체 STT 재전사 (ElevenLabs Scribe 등) | ❌ | ❌ | ❌ | ❌ | ✅ |

## 3. 읽기 능력 비교

| 데이터 | Web | Desktop | MCP | Skill | App |
|---|---|---|---|---|---|
| 파일 리스트 (id/name/duration/date) | ✅ | ✅ | ✅ `list_files` | ✅ `list-files.sh` | ✅ `plaud sync` (+ SQLite 캐시) |
| `is_trans` / `is_summary` / `is_markmemo` 플래그 | △ UI 표시 | △ UI 표시 | ❌ | ✅ | △ 캐시 후 derived (raw flag 미노출) |
| 트랜스크립트 (`transaction`) — 112개 언어 + 스피커 라벨 | ✅ | ✅ | ✅ `get_transcript` | ✅ `transcript.sh` | ✅ `plaud transcript` |
| AI 요약 (`auto_sum_note`) | ✅ | ✅ | ✅ `get_note` | ✅ `summary.sh` | ✅ `plaud summary` |
| 다중 요약 (`sum_multi_note`) — Multidimensional | ✅ | ✅ | ❌ | ✅ | ✅ |
| Outline | ✅ | ✅ | △ raw 포함 | ✅ | ✅ `plaud outline` |
| Highlight (`high_light`) | ✅ | ✅ | ❌ | ✅ | ✅ (`plaud contents`) |
| MarkMemo / Consumer Note (사용자 메모) | ✅ | ✅ | ❌ | ✅ | ✅ |
| 오디오 MP3 | ✅ | ✅ + 로컬 캐시 | △ 24h presigned URL | ✅ `download-file.sh` | ✅ `plaud download` |
| 스크린샷 (Desktop 녹음 시 첨부된 이미지) | ✅ | ✅ | ❌ | ❌ | ❌ |
| **Ask Plaud** (자연어 Q&A, 인용 포함) | ✅ | ✅ | △ MCP 도구 조합으로 유사 구현 | ❌ | △ `plaud cmds-summarize` 등으로 우회 |
| 폴더 트리 (`/filetag/`) | ✅ | ✅ | ❌ | ❌ | ✅ `plaud folders` |
| 인증 사용자 정보 | ✅ | ✅ | ✅ `get_current_user` | ❌ | △ `.env` 기반 |

## 4. 쓰기 / 편집 능력

| 작업 | Web | Desktop | MCP | Skill | App |
|---|---|---|---|---|---|
| **세션 제목 변경** | ✅ | ✅ | ❌ | ❌ | ✅ `plaud rename` · 우클릭 "Rename…" (2026-05-20 추가) |
| **폴더 생성** | ✅ | ✅ | ❌ | ❌ | ✅ `plaud folder-create` → `POST /filetag/` |
| **폴더 이름변경 / 색 / 아이콘** | ✅ | ✅ | ❌ | ❌ | ✅ `plaud folder-rename` → `PATCH /filetag/{id}` |
| **폴더 삭제** | ✅ | ✅ | ❌ | ❌ | ✅ `plaud folder-delete` → `DELETE /filetag/{id}` |
| **파일을 폴더에 할당 / 이동** | ✅ | ✅ | ❌ | ❌ | ✅ `plaud move` → `PATCH /file/{id}` (`filetag_id_list`) |
| **AI 기반 자동 분류 → 폴더 배치** | ❌ | ❌ | ❌ | ❌ | ✅ `plaud classify --apply` (14개 카테고리 SSOT) |
| **폴더 배치 계획 미리보기** | — | — | ❌ | ❌ | ✅ `plaud folder-plan` |
| 스피커 라벨 편집 (Plaud 서버) | ✅ | ✅ | ❌ | ❌ | ❌ |
| 트랜스크립트 텍스트 수정 (Plaud 서버) | ✅ | ✅ | ❌ | ❌ | ❌ |
| 세션 삭제 / 휴지통 | ✅ | ✅ | ❌ | ❌ | ❌ |
| 공유링크 생성 | ✅ | ✅ | ❌ | ❌ | ❌ |
| **녹음 중 실시간 라벨 / 하이라이트** | ❌ | ✅ | ❌ | ❌ | ❌ |
| **AutoFlow 정책 설정** (회의 종료 후 자동 송출) | △ | ✅ | ❌ | ❌ | ❌ |

## 5. 통합 / 내보내기

| 항목 | Web | Desktop | MCP | Skill | App |
|---|---|---|---|---|---|
| Export 형식 가짓수 | 여러 종 | **27+ 형식** (Plaud 마케팅 표기) | — (필요 시 LLM 변환) | △ 트랜스크립트 JSON · 요약 MD | △ 트랜스크립트 / 요약 / 오디오 + Obsidian 송출 |
| Zapier | ❌ | ✅ | ❌ (대신 MCP→다른 MCP 체인) | ❌ | ❌ |
| Notion / Slack / 이메일 직결 | △ 수동 | ❌ (Zapier 경유) | ✅ `plaud-export` 체인 | ❌ | ✅ `plaud meeting-note` · `plaud obsidian` (Obsidian 직결) |
| 자체 LLM 후처리 (Grok/Claude/Codex/Gemini) | ❌ | ❌ | △ 사용자 컨텍스트 내 | ❌ | ✅ `plaud metadata-generate` · `cmds-summarize` |
| 팀 공유 | ✅ | ✅ | ❌ | ❌ | ❌ |
| 커스텀 어휘 (Custom Vocabulary) | △ | ✅ | ❌ | ❌ | ❌ |

## 6. App 전용 로컬 메타 (Plaud 서버에는 미동기화)

App은 Plaud (및 Desktop) 이 지원하지 않는 사용자 워크플로우용 메타를 로컬 SQLite에 별도 보존한다.

| 항목 | App 명령 |
|---|---|
| Obsidian-style 태그 | `plaud tag-add` / `plaud tag-remove` |
| `usage_status` (unused / metadata-ready / vault-linked …) | `plaud usage-status` |
| 자체 스피커 재라벨 (ElevenLabs Scribe + saved speakers) | `plaud cmds-relabel` |
| 자동 메타데이터 생성 (Grok / Claude / Codex / Gemini) | `plaud metadata-generate` |
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
| 빠른 GUI 브라우징 + 인앱 Q&A (Ask Plaud) | **Desktop** 또는 **Web** |
| 오디오 일괄 백업 (다수 파일) | **Skill** 또는 **App** |
| `is_summary=true` 인 것만 일괄 처리 | **Skill** |
| 사용자 markMemo · highlight 접근 (API) | **Skill** 또는 **App** |
| 노션 / 이메일 / Slack 등 다운스트림 푸시 | **MCP** → `plaud-export` skill 체인 (자동) · 또는 **Desktop** → Zapier |
| **폴더 만들기 · 옮기기** | **Web** · **Desktop** · **App** (셋 다 가능) |
| **AI 기반 자동 폴더 분류** | **App** (유일 경로 — 14 카테고리 SSOT) |
| **세션 제목 변경** | **Web** · **Desktop** · **App** |
| Plaud 서버에 스피커 라벨 반영 | **Web** 또는 **Desktop** (자동화 불가) |
| Obsidian 회의록 / 강의록 생성 | **App** (`plaud meeting-note` · `plaud obsidian`) |
| 자체 STT 재전사 · 자체 스피커 라벨 (로컬 보존) | **App** (`cmds-transcribe` · `cmds-relabel`) |
| 세션 삭제 / 휴지통 | **Web** 또는 **Desktop** |
| 27+ 형식 export | **Desktop** |

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
- `note_list[*].data_type`: `auto_sum_note` · 잠재적으로 `sum_multi_note` · `mark_memo` · `consumer_note` · `high_light` (세션마다 가변)

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
| MCP | `~/.plaud/tokens-mcp.json` (자동 refresh) |
| Plaud CLI (`plfetch` 외부 도구) | `~/.plaud/tokens.json` |
| Skill `plaud-cloud-tools` | `~/.claude/skills/plaud-cloud-tools/.env` |
| App `plaud-note-manager` | `<repo>/.env` |

401 발생 시:
- MCP → `login` 도구 호출
- Skill / App → web.plaud.ai 에서 cURL 재캡쳐 후 `onboard` 재실행
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
3. **App** `plaud sync` 후 `plaud classify --apply` — 자동 폴더 분류
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

- 본 저장소: `STATUS.md` (개발 현황), `docs/STORAGE.md` (SQLite 스키마), `docs/REQUESTS.md` (인증·요청 패턴), `docs/DISCLOSURE.md` (공시 / 정책)
- 위성 위키 볼트: `20. Wiki/22. Entities/Plaud MCP Server.md`
- Obsidian 볼트 요약본: `<your-obsidian-vault>/70. Outputs/74. Projects/Plaud Note Manager/2026-05-20-plaud-access-layers.md`
- 외부:
  - <https://www.plaud.ai/pages/plaud-desktop> — Desktop 공식 페이지
  - <https://docs.plaud.ai/mcp> — MCP 문서
  - <https://github.com/johnfkoo951/plfetch> — 외부 CLI fork
