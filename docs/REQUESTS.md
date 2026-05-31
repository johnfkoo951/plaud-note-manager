# User Request Log

개발 기간 동안 사용자가 요청한 기능과 결정을 시간 순서대로 모은 로그.
각 항목은 (1) 원문 의도 (2) 어떻게 처리되었는지 짝으로 기록.

---

## Phase 1 — 프로젝트 부트스트랩

### R1. 프로젝트 골격 생성
> "plaud note 관리하는 스킬, 에이전트, 앱 만들거야. 우선 프로젝트 폴더 만들어줘.
> 기본적인 내용은 plaud skill 현재 버전 참고해줘."

- ✅ `~/DEV/plaud-note-manager/` 생성
- ✅ `core/`, `cli/`, `skill/`, `agent/`, `app/`, `tests/` 5-surface 구조
- ✅ 기존 `~/.claude/skills/plaud-cloud-tools/` 의 cURL 추출 로직과 list/download
  bash 스크립트를 새 구조에 마이그레이션 베이스로 차용

### R2. 스택 결정
> "스위프트. 배시스크립트를 파이썬으로 마이그레이션 할게. 빨리 돌아가고
> 관리하기 좋은쪽으로 해줘."

- ✅ App: Swift + SwiftUI (macOS 14+)
- ✅ Core/CLI: Python 3.11 + `uv` + `httpx` + `pydantic` + `typer`
- ✅ Storage: SQLite (`stdlib sqlite3` + GRDB on Swift side, WAL mode)

---

## Phase 2 — 첫 실행 + 라이브 동기화

### R3. "앱 실행해줘"
- ✅ `swift build` → `nohup .build/debug/PlaudNoteApp`
- ⚠️ 빈 윈도우 이슈 (DB 비어있음) → 자동 sync 트리거 추가

### R4. "웹 데이터를 끌어오는건데 계속 라이브 싱크가 안되는건가?"
- ⚙️ 동기화 층위 정리: ① Cloud→DB ② DB→UI

### R5. 옵션 B — 풀세트 라이브
> "b" (Cloud↔DB + DB↔UI 모두 자동화)

- ✅ `DatabaseWatcher` — `.db`/`-wal`/`-shm` mtime + size 1초 폴링
- ✅ FileStore: NotificationCenter 구독, 30초 cloud polling
- ✅ `NSApplication.didBecomeActive` 시 자동 sync
- ✅ `setActivationPolicy(.regular)` + `activate(ignoringOtherApps:)`

---

## Phase 3 — 공식 웹앱 기능 망라

### R6. 공식 웹앱 전체 기능 + Obsidian 송출
> "공식 웹앱에 있는 기능들은 모두 구현해줘 ... 내 로컬 옵시디언에 보내는
> 기능을 넣을거야. 클로드 코드 라인 실행하는 프롬프트로 던지는 것이 좋을듯 해."

- ✅ `/filetag/` 폴더 17개 동기화
- ✅ `content_list`의 S3 링크에서 transcript / outline / auto_sum_note /
      sum_multi_note 자동 dereference
- ✅ 다중 summary 변형 (Adaptive Summary, Meeting Minutes 등) 모두 캡처
- ✅ 사이드바: All files / Unfiled / Trash + 폴더 17개 (색상 + 카운트)
- ✅ 디테일 뷰: Transcript / Summary / Outline 탭
- ✅ Send to Obsidian — `plaud obsidian <id>` →
      transcript + 모든 summary 변형 묶어 프롬프트 생성 →
      AppleScript로 새 Terminal에서 `claude < prompt.txt`

### R7. 웹 임베드 시도 (속도 개선 시도)
> "너무 느려 ... web.plaud.ai 그대로 따오고 여기에서 개선. 폴더 edit/delete,
> web 들어가도 동기화, summary 외 다른 메뉴, 메뉴 순서 점검, 웹 기능 모두 담아줘."

- ⚠️ WKWebView로 web.plaud.ai 임베드 시도
- ❌ 실패: Plaud SPA가 `js-cookie` 기반 인증 → fetch 헤더 인젝션만으로 로그인
  우회 불가 (localStorage·sessionStorage 미사용 확인)
- ⚙️ Native UI로 회귀 결정

### R8. cURL 키만으로 웹앱 띄우기 시도
> "로그인 인증을 그대로 쓸게 아니야 cURL 해킹한 키값으로 실행할거야."

- ❌ Plaud SPA는 cookie-based auth → JWT 헤더 인젝션으로는 라우터 통과 못함
- ⚙️ "기존 plaud-cloud-tools에 정보 있잖아"로 가이드 받아 native + 캐시 방향 확정

### R9. 옵션 B — Native + Deep Sync
> "b"

- ✅ Web embed 코드 제거 → native 3-pane 복귀
- ✅ `plaud sync-content` 추가 (ThreadPoolExecutor parallel=6 백필)
- ✅ 사이드바 각 파일 row 옆 녹색 점 = 캐시 상태 표시

---

## Phase 4 — UI 다듬기

### R10. 폴더 색상 + 아이콘 + 인앱 매핑 + 1000개 리밋
> "웹에 컬러와 아이콘도 있는데 ... 폴더 연결은 너무 느리네 ... 앱 내에서 폴더
> 연결도 가능하게 ... unfiled에서 사라지겠지 ... all files 지금 웹에는 1024개야.
> 1000개 리밋 걸려있는거라면 풀어줘."

- ✅ Folder color hex → `Color(hex:)` 변환
- ✅ Plaud `iconfont_folder_*` → SF Symbol 25종 매핑 (`waveform`,
      `person.2.fill`, `book.fill`, `stethoscope`, `music.note` 등)
- ✅ `PATCH /file/{id}` body `{filetag_id_list}` 발견 → 파일 우클릭
      "Move to folder" 메뉴
- ✅ 낙관적 UI: Swift가 즉시 SQLite 쓰고 reload → CLI는 백그라운드
- ✅ `limit=2000` (이전 1000) → 1024개 모두 로드

### R11. 정렬 + duration 표시 버그
> "파일 정렬 순서를 Date created 로해주고. 길이가 153h 이런식으로 잘못
> 체크되고 있는 것 같아. 시간 계산 다시 봐봐. 기본 소팅은 생성일 기준으로."

- ✅ `start_time` 컬럼 추가 + migration
- ✅ 단위 발견: `start_time` = ms, `edit_time` = sec
- ✅ `formatDurationMs(ms / 1000)` 적용 (9m 12s 정상 표시)
- ✅ ORDER BY `COALESCE(start_time, edit_time * 1000)` DESC

---

## Phase 5 — CMDS Transcription (ElevenLabs Scribe)

### R12. 종합 요청
> "폴더 클릭하고 이동하는 것이 너무 느리다 ... 앱 내에서는 캐시해서 빠르게 ...
> 백단에서 서버와 싱크 ... 폴더 아이콘 누락 ... 별도 elevenlabs scribe v2 사용
> 해서 전사 ... plaud made 전사와 cmds made 전사 둘 다 디스플레이 ... 오디오
> 파일은 임시파일로 다운받아서 전사 완료 후 삭제 ... 오디오 들어보기는 웹으로
> 재생 ... 화자 분리도 필요 ... elevenlabs 키는 zshrc 에 있어."

- ✅ Optimistic write 패턴 (`Database.writeFileFolders` 즉시 + 백그라운드 API)
- ✅ WAL 모드 + writer/reader 단일 pool로 Swift↔Python 동시 접근
- ✅ Folder icon SF Symbol 매핑 추가
- ✅ `core/transcribe.py` — Plaud temp_url → mp3 tempfile → ElevenLabs
      `/v1/speech-to-text` (`diarize=true`) → 화자/공백 segment 그룹화 →
      `cmds_transcripts` 테이블 저장 → tempfile 삭제
- ✅ Detail 탭: `Transcript (Plaud)` + `Transcript (CMDS)` 분리
- ✅ AVPlayer (signed temp_url, 다운로드 없이 streaming)
- ✅ ELEVENLABS_API_KEY: env → `~/.zshrc` regex fallback

### R13. 오디오 크래시 수정
> "오디오 로드 하니까 튕겼어"

- 🔧 SwiftUI `VideoPlayer`가 SwiftPM executable에서 demangling 크래시 확인
- ✅ `NSViewRepresentable`로 `AVPlayerView` 직접 wrap → 회피

### R14. ElevenLabs 전사 버그
> "일레븐랩스 전사가 안된다. 고쳐"

- 🔧 `group_words_into_segments`에서 `current["end"]` 키 오류 (실제는 `end_ms`)
- ✅ gap 계산을 `start - end_ms/1000.0`으로 단위 일치

### R15. 화자 분리 출처
> "화자분리 기능은 elevenlabs 기능이야?"

- 📖 ElevenLabs Scribe API 본인 기능. `diarize: true` 한 줄로 자동.

---

## Phase 6 — Speaker Labels & Sections

### R16. Saved speakers + num_speakers
> "내가 보통 들어가는 경우가 많은데 자주 쓰는 화자들 리스트업해두고
> 골라쓸 수 있나? num_speakers 기능도 넣어줘"

- ✅ `speakers` 테이블 (id, name, is_self)
- ✅ Manage Speakers sheet (추가/삭제/self 토글, ★ 표시)
- ✅ `num_speakers` 파라미터 추가 (Auto=0)
- ✅ Stepper 0~10 (`0 = "Auto"` 라벨)

### R17. 후처리 라벨링
> "스피커 레이블링 기능은 전사 전단계에서는 못하는거지? 전사 후 후처리
> 프로세스 넣어줘"

- ✅ Relabel UI: 발견된 raw speaker_X마다 드롭다운 → saved speaker 선택
- ✅ CLI: `plaud cmds-relabel <id> speaker_0=Speaker A speaker_1=Speaker B`
- ✅ Apply 시 SQLite의 segments JSON에서 라벨만 swap

### R18. 비용 확인
> "9분짜리 전사할 때 elevenlabs 토큰, 비용 얼마나 소모했어?"

- 📊 측정: 9m 12s = 5,081 chars 소진 (월 300K limit의 1.69%)
- 📊 Creator $22/월로 약 9시간 분량 STT 가능

### R19. Conversation section 분할
> "이 대화는 사실 위와 아래 대화가 별개인거야 ... [00:01:22] Speaker A: 네 ...
> 그래서 후반부 레이블링은 잘못되었어. Speaker B -> Speaker C여야 맞는거지."

- ✅ Silence-gap heuristic: 3~60초 임계 조정 가능
- ✅ `cmdsSections(gapSec:)` — segments를 conversation block으로 자동 분할
- ✅ Section별 별도 relabel 매핑 행 + Apply / Apply all
- ✅ CLI: `cmds-relabel --start <sec> --end <sec>` 범위 모드

---

## Phase 7 — Multi-model Summarization

### R20. 데이터 저장 구조 + 멀티 모델
> "plaud cmds 패널 체인지는 최상단에서 하자 ... cmds의 경우 데이터를 로컬에 저장
> ... 앱 데이터 저장할 공간 체크 ... 생성된 대본, 요약본 정리 ... 나중에 cli에서
> 경로 참조할거니 명확하게 구조 ... 다양한 모델 사용해서 템플릿 적용해서 요약 ...
> claude code cli, codex, gemini cli 사용해서 최상위 추론 모델들 ... 템플릿은
> 기본 템플릿 쓰는 공간 ... 슬롯으로 바꿀수 있게 ... 저장할 수 있도록 폴더"

- ✅ 디렉토리 표준화:
  - `data/transcripts/{file_id}/` — plaud.* / cmds.* markdown
  - `data/summaries/{file_id}/` — `{model}__{template}.md`
  - `data/integrated/{file_id}/` — `{model}__{template}.{,.summary,.transcript}.md`
  - `data/slots.json` — 사용자 슬롯 리스트
  - `templates/` — default / meeting / lecture / integrated
- ✅ `core/summarize.py` — `run_model(model, prompt)` 단일 진입점
- ✅ `core/templates.py` — frontmatter + `{placeholder}` 렌더
- ✅ `core/slots.py` — Slot dataclass (name + model + template)
- ✅ CLI: `cmds-summarize`, `templates`, `template-{show,save,delete}`,
      `slots`, `slot-{add,delete}`, `paths`
- ✅ 디테일 영역 최상단 segmented `[Plaud] [CMDS]` picker

### R21. Speakers Auto 가능
> "스피커 수 정할 수도 있지만 미정으로도 둘 수 있어야해."

- ✅ Stepper 0~10, `0 = "Auto"` 라벨, `0`이면 `num_speakers` 헤더 누락

### R22. AI Summary 우측 패널 + Integrated 파이프라인
> "ai summary 기능은 우측 패널로 ... 모델, cli들 선택 ... cmds 전사 내용이
> 완전치 않다 ... 통합 서머리로 플라우드의 전사, 서머리도 참고하면서 부족한
> 부분을 채워서 온전하게 ... 최종 transcript 대본도 생성 ... 복사 기능
> 알지? all, trans, sum"

- ✅ HSplitView: 좌측 Plaud/CMDS, 우측 AI Inspector
- ✅ Slot mode picker: Summary / Integrated
- ✅ `cmds-integrate` CLI: Plaud transcript + summaries + CMDS transcript를
      한 번에 모델에 던져 통합 요약 + 정리된 transcript 생성
- ✅ `===TRANSCRIPT===` 마커로 두 결과 분리 → 3개 파일 저장 (`.md`, `.summary.md`,
      `.transcript.md`)
- ✅ View picker: All / Transcript / Summary
- ✅ Copy menu: Summary 모드 단순 / Integrated 모드 3단 분리

### R23. UIUX 정돈
> "초는 필요없을듯. 시간이 자꾸 나와 쓸데없어. uiux 전반적으로 신경써줘"

- ✅ `Database.formatMinSec()` — 1시간 미만은 `MM:SS`, 이상은 `H:MM:SS`
- ✅ 모든 transcript / section / outline에 적용

---

## Phase 8 — Backend Selection

### R24. CLI / API / OAuth 확인
> "claude, codex, gemini 모두 api 쓰는거 아니고 oauth나 cli 인증 그대로
> 쓰는거지?"

- 📖 정답: CLI 모드 = 각 CLI 본인의 OAuth/구독 활용. API 키 안 다룸.
- 📖 실용적 분류: CLI / API 두 갈래 (OAuth는 CLI 내장)

### R25. plfetch 참고 + 폴더 지정 + Backend 선택
> "https://github.com/johnfkoo951/plfetch 이거 참고해서 내 앱에 없는 기능들
> 모두 구현 ... 요약본, 전사본을 저장할 폴더를 지정 ... cluade, openai, gemini를
> cli로 쓸지. api로 쓸지, oauth 로 쓸지 선택 ... 나는 주로 cli로 쓸거야."

- 🔍 plfetch 분석: 90% 이미 우리 보유. 추가 가치 = output dir config + 표준화
- ✅ `core/app_config.py` + `data/config.json` (backends + model ids + paths)
- ✅ `core/summarize.py` 분기:
      - CLI: `subprocess.run(["claude", "--print"], input=prompt, ...)`
      - API: 직접 HTTP (Anthropic / OpenAI / Gemini / Grok)
- ✅ CLI: `config`, `config-backend`, `config-model`, `config-path`,
      `contents` (plfetch 호환)
- ✅ Settings sheet (툴바 gear 버튼):
      - 모델별 cli / api segmented picker
      - API model id 인풋 + env var hint
      - Output 폴더 override + NSOpenPanel
- ✅ API key fallback: env → `~/.zshrc` regex 파싱

---

## Phase 9 — 문서 / 배포

### R26. 문서화
> "지금 개발 진행내용, 앞으로 할 내용 문서로 작성"

- ✅ `STATUS.md` — Architecture / 완료 기능 / 제한 / P0~P3 to-do /
      검증 워크플로우 / 결정 기록 / 환경

### R27. GitHub 푸시
> "깃허브에 올려줘"

- ✅ `git init -b main` + initial commit
- ✅ `gh repo create johnfkoo951/plaud-note-manager --private --push`

### R28. Secret 제외 확인
> "아 내 curl 값 등 중요한 것들 제외하고 올려야해"

- ✅ `.gitignore` 검증: `.env`, `data/`, `*.db`, `.build/` 모두 제외 확인
- ✅ 실제 committed files: `.env.example` (빈 템플릿)만 포함, secret 0건

### R29. 진행상황 체크
> "진행상황 체크"

- 📊 8개 카테고리 35+ 기능 확인, 미완 부분 (시간 포맷, Gemini CLI 등) 표시

### R30. 문서 재작성
> "지금 개발 진행내용, 앞으로 할 내용 문서로 작성"

- ✅ `STATUS.md` 보강 (이미 존재했지만 multi-session handoff 섹션 추가)

### R31. 요청 로그 (현재)
> "내가 개발 시 요청한 기능들과 항목들 모두 다 로그로 정리해줘."

- ✅ `docs/REQUESTS.md` (이 파일) — 모든 요청을 시간순 + 처리상태와 함께 정리

---

## 추후 (사용자 다른 세션에서 진행된 것으로 보이는 변경)

### S1. CLI 에러 표면화
- ✅ Swift `lastCommandError` alert로 CLI 실패 노출
- ✅ Python core 네트워크 오류 메시지 정제 (`Plaud network error for ...`)
- ✅ traceback에서 useful error line 추출 로직 (`isUsefulErrorLine`)

### S2. uv 경로 fallback
- ✅ `/usr/bin/env uv` 제거하고 `~/.local/bin/uv` / `/opt/homebrew/bin/uv` /
      `/usr/local/bin/uv` 후보만 사용 (Applications 번들 PATH 문제 해결)

### S3. App state 비활성화
- ✅ `applicationShouldSaveApplicationState` / `RestoreApplicationState` false
- ✅ `NSQuitAlwaysKeepsWindows` false

### S4. macOS .app 번들 패키징
- ✅ `scripts/package-macos-app.sh` 추가
- ✅ `/Applications/Plaud Note Manager.app` 배포
- ✅ `app/Resources/AppIcon.png`

### S5. Settings 단축키
- ✅ `⌘,` → openPlaudSettings notification

### S6. Note metadata layer
- ✅ `note_metadata`, `note_tags`, `note_references` 테이블
- ✅ Obsidian-style tag normalization (`tags.py`)
- ✅ CLI: `metadata`, `tags`, `tag-add`, `tag-remove`, `metadata-generate`,
      `meeting-note`
- ✅ 디테일 헤더에 tag chips + auto metadata + 회의록 생성 버튼
- ✅ 템플릿 추가: `metadata.md`, `vault-meeting-note.md`

### S7. 폴더 taxonomy + classification
- ✅ `core/classification.py` — 메인 볼트 CMDS Head Quarter 기반 분류 규칙
- ✅ CLI: `folder-plan`, `classify --apply`
- ✅ Plaud 폴더 자동 라우팅 + `usage_status` (`unused` / `metadata-ready` /
      `vault-linked` / `used-elsewhere` / `archived`)

### S8. Model registry from vault
- ✅ `core/model_registry.py` — `40. Docs/49. API Information` frontmatter에서
      SOTA 모델 preset 로드
- ✅ Grok backend 추가 (XAI_API_KEY, `https://api.x.ai/v1/chat/completions`)
- ✅ Slot에 `model_id` 필드 — 슬롯별 모델 버전 핀

### S9. Integrated delimiter 강화
- ✅ 새 delimiter (`===FINAL_TRANSCRIPT_BEGIN===` / `===SUMMARY_BEGIN===`) +
      기존 `===TRANSCRIPT===` legacy 모두 지원
- ✅ delimiter 누락 시 raw output을 summary에 보존, transcript는 명시적
      `(no transcript section)` 표시
- ✅ `core/integrate.py:split_integrated` SSOT

### S10. 품질 게이트
- ✅ `pytest`, `ruff` dev dependency
- ✅ 회귀 테스트 5개 + Swift `swift build` 통과

### S11. Multi-session handoff
- ✅ STATUS.md에 "Current Handoff" 섹션 — 여러 Codex/Claude 세션이
      충돌 없이 작업 이어가도록 git/PID/상태 확인 규칙 명시

---

## Phase 10 — Taxonomy & UI 폴리시 (2026-05)

`feature/recording-taxonomy-routing` 브랜치에서 진행된 변경. main 머지 대기.

### S12. 녹음 taxonomy SSOT + Work 사이드바
- ✅ `core/classification.py` — 사용자 설정 가능한 분류 체계 기반 카테고리
      (generic 기본값 + gitignored `data/classification.json` 오버라이드).
- ✅ `metadata-generate` 한 번에 `folder_name` · `category` · `usage_status` 저장 +
      Plaud 폴더로 이동.
- ✅ CLI: `folder-plan`(dry-run), `classify --apply`(일괄 적용).
- ✅ 앱 사이드바에 Work 섹션 + `core/storage.py` 카테고리 group-by 쿼리,
      Swift `Database` view model 확장.
- ✅ 회귀 테스트: `tests/test_classification.py`.

### S13. AI Summary 모델 프리셋 (CMDS 볼트 연동)
- ✅ `core/model_registry.py` — 메인 볼트
      `40. Docs/49. API Information` frontmatter에서 Claude/Codex/Gemini/Grok
      SOTA 모델 preset 로드.
- ✅ CLI `plaud models` + 앱 AI Inspector 슬롯이 같은 source of truth 공유.

### S14. usage_status picker
- ✅ Metadata bar의 `usage_status`를 심볼 + 라벨 picker로 정리
      (`unused`/`metadata-ready`/`vault-linked`/`used-elsewhere`/`archived`).

### S15. 앱 UI 정돈
- ✅ Settings 시트 레이아웃 재구성, 사이드바 토글 동작 수정.
- ✅ 툴바 아이콘 사이즈 통일, Detail 헤더 / AI Inspector 슬롯 카드 / tag chip
      간격 폴리시.

### S16. Plaud 네트워크 실패 정제
- ✅ `core/client.py` 에러 메시지 표준화.
- ✅ Swift `FileStore`가 traceback 대신 사용자용 alert로 표면화.
- ✅ 회귀 테스트 `tests/test_client.py` 추가.

---

## 통계

- **총 사용자 요청**: 31건 (R1~R31)
- **추가 자동 발전 항목**: 16건 (S1~S16, 다른 세션 작업으로 추정)
- **거부된/회귀된 기능**: 1건 (Phase 3 웹 임베드 — Plaud SPA 인증 한계로 native 복귀)
- **외부 의존성**:
  - Python: httpx · pydantic · typer · python-dotenv · rich
  - Swift: GRDB.swift
  - System CLIs: claude · codex · gemini (옵션)
  - API keys (옵션): ANTHROPIC / OPENAI / GEMINI / XAI / ELEVENLABS

---

## 자주 반복된 패턴

1. **속도 불만 → 캐시 + 낙관적 UI** (R7, R12)
2. **단위 mismatch 디버깅** (R11 duration ms, start_time ms vs edit_time sec)
3. **데이터 풀로딩** (R10 1000 → 2000 limit)
4. **결과물 디스크 저장** (R20 모든 transcript/summary가 markdown 파일로)
5. **외부 도구 인증은 외부 도구에게** (R24 OAuth/구독 활용)
6. **사용자 케이스로 한계 발견** (R19 대화 분리, R14 ElevenLabs 버그)
