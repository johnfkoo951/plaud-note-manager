# Plaud Note Manager — Development Status

마지막 업데이트: 2026-06-12

Plaud Cloud 녹음을 native macOS 앱 + Python CLI 한 쌍으로 관리하고,
CMDS 자체 전사(ElevenLabs Scribe) + 다중 모델(Claude/Codex/Gemini/Grok)
요약을 결합해 Obsidian으로 보내는 통합 워크스페이스.

---

## 0. Current Handoff (Multi-Session)

여러 Codex/Claude 세션에서 같은 작업을 이어갈 수 있으므로, 새 세션은 먼저
이 섹션과 Git 상태를 확인한다.

### 현재 GitHub / 로컬 상태
- 기본 브랜치: `main`
- 원격: `origin = https://github.com/johnfkoo951/plaud-note-manager.git`
- 현재 HEAD는 새 세션에서 `git log --oneline -5`로 확인한다.
- main에 머지 완료된 PR (역사용 — 자세한 내역은 `docs/REQUESTS.md` 참고):
  - PR #1 `Harden integrated summaries` → `21d6234`
  - PR #2 `Update SwiftUI onChange handlers` → `47d0891`
  - PR #3 `Document multi-session handoff state` → `5cb26fa`
  - PR #4 `Package macOS app bundle` → `6a0e241`
  - PR #5 `Fix record detail layout` → `5ab4a2d`
  - PR #6 `Remove uv env fallback` → `0fcb279`
  - PR #7 `Disable app state restoration` → `518b2d3`
  - PR #8 `Add stable note metadata and tags` → `3dc46f5`
  - PR #10 `Add recording taxonomy and work sidebar` → `e4fb47e`
    (녹음 taxonomy + Work sidebar + classification SSOT,
    AI summary 모델 프리셋 `e78adcd` 포함하여 main에 머지 완료)
- 현재 main은 위 작업을 모두 포함한다. taxonomy/모델 프리셋 작업은 더 이상 대기
  브랜치가 아니라 머지된 상태이며, 새 작업은 main에서 분기한다.
- 새 세션 시작 체크:
  ```bash
  cd ~/DEV/plaud-note-manager
  git status --short --branch
  git log --oneline -10
  ```

### 현재 실행 상태
- SwiftPM debug 앱 실행 파일:
  `~/DEV/plaud-note-manager/app/.build/arm64-apple-macosx/debug/PlaudNoteApp`
- 패키징된 앱 배포 위치:
  `/Applications/Plaud Note Manager.app`
- 기본 앱 아이콘 소스:
  `app/Resources/AppIcon.png`
- 앱 패키징/배포 명령:
  ```bash
  scripts/package-macos-app.sh
  open "/Applications/Plaud Note Manager.app"
  ```
- PID는 세션/재실행마다 바뀌므로 새 세션에서는 반드시 확인:
  ```bash
  pgrep -fl PlaudNoteApp || true
  open -n "/Applications/Plaud Note Manager.app"
  ```

### 품질 게이트
현재 기준으로 모두 통과:
```bash
uv run pytest
uv run ruff check core cli tests
uv run ruff format --check core cli tests
swift build --package-path app
```

### 다음 세션에서 바로 이어갈 일
1. `codex/web-login-auth` 브랜치 — Plaud Web Login 인증 기능 구현 + 하드닝 완료
   (앱 Auth 버튼 → "Authenticate with Plaud" 시트, CLI `auth` / `refresh-auth` /
   `web-auth`, validate-before-write, `.env` 0600). 다음 작업: main으로 머지.
2. taxonomy/모델 프리셋이 main에 머지된 상태이므로:
   - `core/classification.py` taxonomy를 실데이터에 한 번 더 돌려 라우팅 정합성 확인
     (`plaud folder-plan` → `plaud classify --apply`).
   - 앱 Metadata bar에서 `usage_status` 변경이 즉시 DB와 UI에 반영되는지 한 번
     end-to-end 확인.
3. `improve/v0.3-ux` 브랜치 — status 재정의 + P0 UX 4종 완료. 리뷰 후
   codex/web-login-auth(또는 main)로 머지.
4. agent 자동 루프 — 선행 조건이었던 진행률 데이터(`plaud status --json`)가
   준비됨. launchd/cron 루프 설계 가능.

### 세션 충돌 방지 규칙
- 작업 전 항상 `git status --short --branch`와 `git pull --ff-only` 확인.
- 다른 세션이 앱을 띄웠을 수 있으므로 앱 재실행 전 `pgrep -fl PlaudNoteApp` 확인.
- `data/`, `.env`, `.venv`, `.build`는 Git ignore 대상이다. 실데이터를 커밋하지 않는다.
- 앱을 수정하거나 새로 만들면 `scripts/package-macos-app.sh`로 `.app` 번들을 만들고
  `/Applications`에 배포한다.
- 통합 결과물 파싱은 `core.integrate.split_integrated`가 SSOT다. CLI에서 별도 파싱을
  다시 만들지 않는다.
- Swift 앱은 Python CLI를 subprocess로 호출한다. CLI 실패는 `lastCommandError`
  alert로 표면화되어야 한다.
- AI Summary 모델 프리셋을 수정하거나 앱을 업데이트할 때는 항상 Obsidian 볼트의
  `<your-obsidian-vault>/40. Docs/49. API Information`
  문서를 먼저 확인한다 (볼트 경로는 `PLAUD_OBSIDIAN_VAULT`로 설정).
  `core/model_registry.py`와 Swift `Database.loadModelPresets()`
  모두 이 경로를 SOTA 모델 source of truth로 사용한다.
- 녹음 폴더 분류 기준은 `core/classification.py`가 SSOT다. Obsidian 볼트의
  분류 체계 문맥을 기준으로 Plaud 폴더와
  `usage_status`를 함께 관리한다.

---

## 1. Architecture

```
plaud-note-manager/
├── core/                     공유 Python 라이브러리
│   ├── client.py             Plaud Cloud HTTP 클라이언트 (auth, files, folders, content, audio)
│   ├── transcribe.py         ElevenLabs Scribe STT 파이프라인 (diarize + segment grouping)
│   ├── summarize.py          Multi-model 추론 (CLI / API 두 모드)
│   ├── model_registry.py     CMDS API Information 기반 SOTA model preset 로더
│   ├── classification.py     녹음 분류 taxonomy + Plaud folder routing 규칙
│   ├── metadata.py           stable Plaud id 기반 note metadata, tags, vault meeting 생성
│   ├── tags.py               Obsidian-style tag normalization (# 제거, 공백 hyphen)
│   ├── disclosure.py         progressive-disclosure 쿼리 API (L0 peek → L3 deep, 각 층 superset)
│   ├── locator.py            로컬 Plaud 데이터 canonical resource locator (plaud:// URI enumerate/read)
│   ├── vault_index.py        Obsidian vault 인덱싱 — 키워드를 실제 노트로 resolve (incremental mtime scan)
│   ├── templates.py          프롬프트 템플릿 로딩 (frontmatter + placeholder render)
│   ├── slots.py              요약 슬롯 (name + provider + model_id + template)
│   ├── app_config.py         data/config.json (backends · model ids · path overrides)
│   ├── paths.py              표준 경로 + override 처리
│   ├── storage.py            SQLite 메타/캐시 (WAL 모드, files/folders/content/cmds_transcripts/speakers/note_metadata/note_tags/note_references)
│   ├── models.py             Pydantic 도메인 모델
│   ├── auth_status.py        .env JWT 검사 — 오프라인 만료 카운트다운 + 선택적 live 검증 (tri-state)
│   ├── refresh_auth.py       클립보드/stdin의 Plaud cURL 파싱 → .env 갱신 (0600, PLAUD_ENV_FILE 존중)
│   ├── web_auth.py           앱 Web Login 캡쳐 JSON import — live 검증 후 .env 기록 (validate-before-write)
│   └── config.py             .env 로딩 (Plaud API credentials)
├── cli/main.py               typer CLI: ~70개 명령어
├── skill/SKILL.md            Claude Code skill (CLI 호출 래퍼)
├── agent/AGENT.md            자율 에이전트 정의 (배치 처리용)
├── templates/                프롬프트 템플릿 (default / meeting / lecture / integrated / metadata / vault-meeting-note / cmds-meeting / cmds-lecture / cmds-coaching)
├── data/
│   ├── plaud.db              SQLite (WAL)
│   ├── config.json           백엔드 + 경로 설정
│   ├── slots.json            요약 슬롯 리스트
│   ├── transcripts/{id}/     전사본 (plaud.* / cmds.*)
│   ├── summaries/{id}/       단일-소스 요약 ({model}__{template}.md)
│   └── integrated/{id}/      통합 결과 ({model}__{template}.{,.summary,.transcript}.md)
└── app/                      SwiftUI macOS 14+ executable (SwiftPM + GRDB)
    └── Sources/PlaudNoteApp/
        ├── PlaudNoteApp.swift    @main, NSApplication 활성화
        ├── ContentView.swift     NavigationSplitView (Sidebar / List / Detail) + Settings sheet
        ├── Database.swift        GRDB read/write pool, view models, 파일 system helpers
        ├── FileStore.swift       @MainActor ObservableObject, CLI bridge, 30s polling
        ├── FileStore+Auth.swift  Web Login 캡쳐 → `plaud web-auth --stdin` CLI 브리지
        ├── PlaudAuthSheet.swift  "Authenticate with Plaud" 시트 (browser cURL import 기본 · embedded fallback)
        ├── PlaudWebLoginView.swift    WKWebView embedded Web Login (fallback 경로)
        ├── PlaudWebLoginScript.swift  로그인 페이지 주입 JS — api-apne1.plaud.ai 요청 헤더 캡쳐
        └── PlaudWebAuthCapture.swift  캡쳐 페이로드 모델 (web-auth JSON 계약과 1:1)
```

### 데이터 흐름

```
Plaud Cloud
    │ (api-apne1.plaud.ai, JWT auth in .env)
    ▼
Python core ─── SQLite (data/plaud.db, WAL)
    │              ▲
    │              │ 양방향 read/write
    ▼              │
markdown files   Swift app (GRDB)
(transcripts/, summaries/, integrated/)
```

- Swift는 SQLite로 사이드바·리스트·캐시된 컨텐츠를 읽고, 변경 시 즉시 reload.
- 모든 쓰기는 Swift가 즉시 SQLite에 반영(낙관적) + 백그라운드 Python CLI 호출로 서버 동기화.
- AI 결과물(전사/요약)은 markdown 파일로 디스크에 저장 — CLI/Obsidian/Claude Code에서 직접 접근 가능한 single source of truth.

---

## 2. 완료된 기능

### 2026-06-12 v0.3 업그레이드 — 파생 진행률 + P0 UX (branch improve/v0.3-ux)
- [x] **`plaud status` 재정의** — 정적 `files.status`(대부분 'new') 대신
      아티팩트에서 파생: `new`(메타만) → `cached`(detail 캐시) →
      `transcribed`(CMDS 전사) → `integrated`(통합 결과 on disk).
      `core/progress.py` SSOT, CLI `plaud status [--json] [--stage S] [--limit N]`.
      실데이터 검증: 1182개 = new 142 / cached 1025 / transcribed 7 / integrated 8.
      agent 자동 루프의 선행 조건 해소.
- [x] **Progress 타일** — 디테일 Metadata bar의 Cache 타일을 파생 단계 표시로
      교체 (Integrated/Transcribed/Cached/New, 아이콘+색상, 선택 파일만 계산).
- [x] **키워드 chip 클릭화** — 디테일 헤더 키워드 → 캡슐 버튼, 클릭 시 검색 필터.
- [x] **슬롯 결과 확대 sheet** — 슬롯 카드에 expand 버튼 → 720×560 sheet에서
      동일 렌더링 + Copy, Integrated 모드는 All/Transcript/Summary picker 공유.
- [x] **Sidebar drag-and-drop** — 파일 행 드래그 → 폴더/Unfiled 드롭
      (단일-폴더 교체 의미론, 라디오 메뉴와 같은 쓰기 경로, 타겟 하이라이트).
- [x] **시간 포맷 통일** — CMDS transcript/sections의 수기 HH:MM:SS를
      `formatMinSec`(M:SS, ≥1h H:MM:SS)으로 일원화.

### 2026-06-11 v0.2.0 업그레이드 — 폴더 단일화 · Grok CLI · 스피커/제목 편집 · 성능
- [x] **폴더 단일화** — Plaud 웹은 파일당 폴더 1개만 지원 (복수 지정 시 웹 UI 깨짐).
      `client.set_file_folders`가 2개 이상이면 ValueError, `plaud move <id> [folder]`는
      단일 폴더만 받고 기존 연결을 대체. 앱 폴더 메뉴는 체크박스 → **라디오**
      (현재 폴더 재클릭/Unfiled 선택 = 해제). `plaud folder-doctor [--apply]`로
      기존 다중-폴더 파일 진단·복구 (2026-06-11 실데이터 4건 복구 완료).
- [x] **Grok CLI 백엔드** — xAI 공식 Grok Build CLI (`~/.grok/bin/grok`) 연동.
      `grok -p <prompt>` 단일턴 호출, 프롬프트는 argv (stdin 미지원, 700KB 가드).
      서브프로세스 env에서 `XAI_API_KEY`를 제거해 **SuperGrok 구독 OAuth**로
      인증 — API 비용 없음. `data/config.json` grok backend = cli 기본.
- [x] **Plaud 서버 전사본 스피커 변경** — `plaud plaud-relabel <id> OLD=NEW …`
      (web parity: `PATCH /file/{id}` body `trans_result` + `support_mul_summ`,
      `original_speaker`는 최초 1회만 백필·보존). 앱 Plaud 탭 person.2 버튼 →
      스피커 일괄 rename 시트. 보이스프린트 roster 변경은 기존
      `speaker-rename-server` 그대로.
- [x] **제목 인라인 편집** — 디테일 헤더 제목 클릭(또는 hover 연필) → TextField,
      Enter 커밋 = 낙관적 SQLite 기록 + 백그라운드 `plaud rename`.
- [x] **Summary 슬롯 UI 정리** — 모드 세그먼트에 아이콘+색상 (Summary=파랑,
      Integrated=초록) + 모드 설명 캡션, 템플릿을 칩으로 노출.
- [x] **볼트 기반 템플릿 3종 추가** — `cmds-meeting` / `cmds-lecture` /
      `cmds-coaching`. CMDS Guide v2.7 frontmatter 규칙(따옴표 위키링크,
      영어 description), ~함체, `>[!info]` 콜아웃, Discussion/Next Steps 구조로
      볼트에 바로 저장 가능한 .md를 출력.
- [x] **성능** — `file_folders(folder_id)` 인덱스 추가, 사이드바 폴더 카운트
      N+1 → grouped JOIN 1쿼리, 카테고리 카운트 3쿼리 → 1쿼리(NOT EXISTS),
      DB 워처 알림 300ms 디바운스, masterFiles 폴더 서브쿼리 → grouped JOIN
      (실DB로 신·구 결과 동치 검증).

### 2026-06-11 업그레이드 — Plaud Web Login 인증
- [x] 앱 툴바 Auth 버튼 → **"Authenticate with Plaud"** 시트
      (`PlaudAuthSheet.swift`). Browser login (Open Plaud → Import Copied cURL)이
      기본 경로, embedded Web Login (WKWebView 캡쳐)은 fallback —
      WKWebView 안의 Google 로그인이 passkey/Bluetooth 오류를 낼 수 있어
      browser import를 기본으로 승격 (`1dba731`). 최후 수단은
      `pbpaste | uv run plaud onboard`.
- [x] CLI `plaud refresh-auth` — macOS 클립보드의 Plaud cURL을 파싱해 `.env` 갱신
      (`--stdin` 지원, `PLAUD_ENV_FILE` 존중).
- [x] CLI `plaud web-auth --stdin` — WKWebView 캡쳐 JSON을 받는 앱 내부 브리지.
      **validate-before-write**: live API 검증을 통과해야 `.env`에 쓴다.
      실제 거부(401/403)·이미 만료된 토큰이면 `.env`를 건드리지 않고
      `live_auth_failed`, 네트워크 불통이면 저장 후 `live_check_unavailable`
      (tri-state — 거부 vs 불통 구분). JSON 모드는 항상 exit 0, 호출자는
      `status` 필드로 판정.
- [x] CLI `plaud auth` — JWT exp 기반 오프라인 만료 카운트다운, `--live`로
      실제 토큰 검증.
- [x] 보안 계약: `.env`는 0600 권한으로 기록, 기존 `.env.bak-<epoch>` 백업은
      완전 제거 (디스크에 자격증명 사본을 남기지 않음). WKWebView 캡쳐 핸들러는
      `*.plaud.ai` origin으로 제한.

### Progressive disclosure + vault graph
- [x] `core/disclosure.py` 도입 — 4단계 progressive-disclosure 쿼리 API
      (L0 `peek` → L1 `brief` → L2 `outline` → L3 `deep`, 각 층이 이전 층의 superset).
      CLI `peek` / `brief` / `outline-of` / `deep` / `query`가 공유. 자세한 계약은
      `docs/DISCLOSURE.md` 참고.
- [x] `core/locator.py` 도입 — 로컬 Plaud 데이터의 canonical resource locator.
      `plaud://{kind}/{file_id}` 형태 stable URI로 외부 파이프라인(embedding/RAG/
      Obsidian sync)이 내부 디렉터리 구조를 몰라도 enumerate/read 가능. CLI
      `resources` / `show`로 노출.
- [x] `core/vault_index.py` 도입 — Obsidian vault `.md`를 incremental(mtime) 스캔해
      frontmatter(aliases/tags/type)를 인덱싱, Plaud 키워드를 실제 노트로 resolve.
      CLI `vault-index`(인덱스 빌드) / `vault-link`(노트 연결)로 노출. 저장 스키마는
      `docs/STORAGE.md` 참고.

### 2026-05-11 업그레이드
- [x] `core/classification.py` SSOT 도입 — Obsidian 볼트 분류 체계 문맥 기반
      14개 카테고리 taxonomy. `metadata-generate`가
      `folder_name` · `category` · `usage_status`를 동시 갱신하고 Plaud 폴더로 이동.
- [x] 앱 사이드바에 **Work** 섹션 추가 — taxonomy 카테고리별 카운트와 빠른 필터.
      `core/storage.py`에 카테고리 group-by 쿼리 + Swift `Database` view model 확장.
- [x] CLI `folder-plan`, `classify --apply` 추가 (taxonomy dry-run / 일괄 적용).
- [x] `core/model_registry.py` 도입 — Obsidian 볼트
      `40. Docs/49. API Information` frontmatter에서 Claude/Codex/Gemini/Grok SOTA
      모델 preset을 로드해 CLI `plaud models`와 앱 AI Inspector 슬롯이 공유.
- [x] Settings 시트 레이아웃 재구성 + 사이드바 토글 동작 수정. 툴바 아이콘 사이즈
      통일 (`ee51c36`).
- [x] Metadata bar의 `usage_status`가 심볼 + 라벨 picker로 정리됨
      (`unused`/`metadata-ready`/`vault-linked`/`used-elsewhere`/`archived`).
- [x] 앱 UI 컨트롤 폴리시 — Detail 헤더, AI Inspector 슬롯 카드, tag chip 간격
      정리 (`2e522b5`).
- [x] Plaud 네트워크 실패 처리 정제 — `core/client.py`에서 메시지 표준화,
      Swift `FileStore`가 traceback 대신 사용자용 alert로 표면화. 회귀 테스트
      추가 (`tests/test_client.py`).

### 2026-04-30 업그레이드
- [x] `cmds-integrate` 중복 CLI 정의 제거 — 단일 구현이 `core.integrate`
      파이프라인을 호출.
- [x] 기록 선택 시 좁은 창에서 디테일/AI 패널이 사이드바와 목록을 밀어내던
      SwiftUI 레이아웃 문제 수정 — AI Inspector는 폭이 부족하면 toolbar sheet로 표시.
- [x] `/Applications`에서 실행한 패키징 앱이 GUI PATH 차이로 `uv`를 못 찾던 문제 수정.
      Swift 앱의 `/usr/bin/env uv` fallback을 제거하고 절대 `uv` 후보 경로만 사용.
- [x] 이전 실행의 modal alert/window state가 되살아나는 것을 막기 위해
      macOS state restoration 저장/복원 비활성화.
- [x] Integrated output 파서가 새 delimiter
      (`===FINAL_TRANSCRIPT_BEGIN===` / `===SUMMARY_BEGIN===`)와 기존
      `===TRANSCRIPT===` legacy template을 모두 지원.
- [x] delimiter 누락 시 raw model output을 summary에 보존하고 transcript에는
      `(no transcript section)`을 명시해 조용한 데이터 유실 방지.
- [x] `pytest` / `ruff` dev dependency 추가 및 최소 회귀 테스트 5개 추가.
- [x] Swift 앱에서 CLI 실패 exit code + output을 alert로 표시.
- [x] Plaud immutable `file_id` 기준 로컬 note metadata 레이어 추가:
      `note_metadata`, `note_tags`, `note_references`.
- [x] Obsidian-style tag normalization 추가 — `#` 제거, 공백은 hyphen 처리,
      frontmatter용 plain tag 보존.
- [x] CLI 추가: `metadata`, `tags`, `tag-add`, `tag-remove`,
      `metadata-generate`, `meeting-note`.
- [x] 앱 디테일 헤더에 tag chips, 수동 tag 입력, auto metadata 생성,
      CMDS 회의록 작성 버튼 추가.
- [x] Obsidian 볼트 system files/meeting template/Claude Code skills를 참고하는
      `metadata.md`, `vault-meeting-note.md` 템플릿 추가.
- [x] Obsidian 볼트 분류 체계 기반 녹음 taxonomy 추가:
      `10. Meetings`, `11. AI 강의`, `12. 지식관리`, `13. Consulting & AX`,
      `14. Product & Engineering`, `15. Partnerships & Pipeline`,
      `21. Personal & Family`, `22. Spirituality`, `23. Health & Biohacking`,
      `24. Contracts & Finance`, `30. Jazz & Music`, `32. Media & Interviews`.
      (generic 기본값 — `data/classification.json`으로 사용자 오버라이드 가능)
- [x] `metadata-generate` 실행 시 Plaud immutable `file_id` 기준으로
      `folder_name`, `category`, `usage_status`를 저장하고 해당 Plaud 폴더로 이동.
- [x] 앱 Metadata bar에 folder/usage status 표시와 usage status 메뉴 추가.

### Plaud 통합
- [x] cURL 기반 인증 (`.env`에 4개 헤더, `plaud onboard`로 자동 추출)
- [x] file list 동기화 (limit 2000, ~1000개 풀로딩)
- [x] folder CRUD (create/rename/delete, 7색 팔레트 + iconfont→SF Symbol 매핑)
- [x] file → folder 이동 (`PATCH /file/{id}` body `{filetag_id_list}`)
- [x] 전사/요약/outline content_list S3 자동 dereference
- [x] 다중 summary 변형 (auto_sum_note + sum_multi_note × N) 모두 캡처
- [x] AVPlayer 오디오 스트리밍 (signed temp_url, 다운로드 없음)
- [x] 다운로드 (audio export)
- [x] 휴지통(trash) 동기화
- [x] 30초 폴링 + DB watcher (.db / .db-wal / .db-shm mtime+size)
- [x] 디테일 백그라운드 lazy fetch + 캐시
- [x] Backfill 버튼: 1024개 파일 전사/요약 일괄 캐시 (parallel=6)

### CMDS (ElevenLabs Scribe)
- [x] 임시 mp3 다운로드 → 업로드 → 화자분리 전사 → 임시파일 삭제
- [x] Auto / 1-10명 화자 수 옵션 (`num_speakers`)
- [x] word-level 결과를 화자/공백 기준 segment로 그룹화
- [x] 침묵 기반 conversation section 자동 분할 (3-60초 조정 가능)
- [x] Section별 별도 화자 라벨 매핑 sheet
- [x] Saved speakers DB (self 마크 + 드롭다운에 ★ 표시)
- [x] CLI: `cmds-transcribe` / `cmds-relabel --start --end` / `cmds-integrate`

### AI Summary (우측 패널)
- [x] 슬롯 시스템 (name + provider + model_id + template, slots.json)
- [x] Summary / Integrated 두 모드 picker
- [x] **Integrated 파이프라인**: Plaud 전사 + 요약 + CMDS 전사를 한 번에 모델에 던져
      strict marker 또는 legacy `===TRANSCRIPT===` 마커로 통합 요약 +
      정리된 최종 transcript 두 결과물 분리 저장
- [x] 슬롯 카드: Generate / Re-generate / Show / Hide / Copy / Delete
- [x] Integrated 모드 view picker: All / Transcript / Summary
- [x] Copy 메뉴: Summary 모드 단순 / Integrated 모드 3단 분리 복사
- [x] 슬롯 추가 sheet (provider · SOTA preset/custom model id · template)
- [x] 폴더 reveal 버튼 (`data/integrated/{file_id}` Finder 열기)

### 모델 백엔드
- [x] CLI 모드: `claude --print` / `codex exec` / `gemini --prompt-interactive=false`
      (각 CLI의 OAuth/구독 자동 활용)
- [x] API 모드: 직접 HTTP
      - Anthropic: `/v1/messages` + `ANTHROPIC_API_KEY`
      - OpenAI: `/v1/chat/completions` + `OPENAI_API_KEY`
      - Gemini: `generativelanguage.googleapis.com/.../generateContent` + `GEMINI_API_KEY`
      - xAI/Grok: `api.x.ai/v1/chat/completions` + `XAI_API_KEY`
- [x] 모델별 backend 개별 선택 (config-backend claude cli)
- [x] API 모드 model id 핀 가능 (claude-opus-4-7 등)
- [x] CMDS API Information 기반 SOTA 모델 프리셋 목록 (`plaud models`) +
      AI Summary 슬롯별 custom model id 선택
- [x] `~/.zshrc` API 키 fallback 파싱

### Settings 시트 (툴바 gear)
- [x] 모델별 cli / api segmented picker
- [x] API model id 인풋
- [x] Output path override (transcripts / summaries / integrated) + folder picker
- [x] env var hint 표시 (api 모드일 때)

### Templates
- [x] frontmatter 기반 (name, description)
- [x] Placeholder: `{transcript}` `{title}` `{keywords}` `{speakers}`
      `{cmds_transcript}` `{plaud_transcript}` `{plaud_summaries}`
- [x] 기본 4개 제공: default · meeting · lecture · integrated
- [x] CLI: list / show / save / delete
- [x] Finder에서 templates 폴더 열기 버튼

### Obsidian 송출
- [x] `plaud obsidian <id>` — transcript + 모든 summary 변형 묶어 Claude Code 프롬프트 생성
- [x] AppleScript로 새 Terminal에서 `claude < prompt.txt` 자동 실행
- [x] CMDS-vault 경로 자동 주입 + 옵시디언-스킬 활용

### CLI (총 ~70개 명령)
```
peek / brief / outline-of / deep / query / resources / show / vault-index / vault-link /
list / sync / sync-content / contents / detail / transcript / summary / outline /
download / status / dashboard / metadata / usage-status / tags / tag-add / tag-remove /
metadata-generate / meeting-note / note-edit / move / rename /
folders / folder-create / folder-rename / folder-delete /
cmds-transcribe / cmds-relabel / cmds-summarize / cmds-integrate /
templates / template-show / template-save / template-delete / models / folder-plan / classify / folder-doctor /
slots / slot-add / slot-delete /
speakers / speaker-add / speaker-delete / server-speakers / speaker-rename-server / plaud-relabel /
config / config-vault / config-author / config-backend / config-model / config-path / paths /
audio-url / export / obsidian / web / onboard / auth / refresh-auth / web-auth
```

---

## 3. 알려진 제한

- **Plaud SPA 임베드 제한 (앱 UI)**: web.plaud.ai의 SPA가 js-cookie 기반 인증이라
  fetch 헤더 인젝션만으로는 앱 UI 임베드 불가 — 앱 UI는 native 유지. 단,
  **로그인 페이지 임베드는 자격증명 캡쳐 용도로는 동작**한다 (Auth 시트의
  embedded Web Login fallback). WKWebView 안의 Google 로그인이 passkey/Bluetooth
  오류를 낼 수 있어 browser cURL import가 기본 경로.
- **CLI 인증은 각 CLI 책임**: claude/codex/gemini를 셸에서 미리 로그인해야
  CLI 모드 사용 가능. 앱이 토큰을 다루지 않음 (의도적).
- **Gemini CLI 미설치 환경**: 현재 시스템에 `gemini` 미설치 → API 모드로
  전환하거나 `npm install -g @google/gemini-cli`.
- **WKWebView SwiftPM 한계**: SwiftUI `VideoPlayer`가 unbundled executable에서
  demangling 크래시. AVKit `AVPlayerView`를 NSViewRepresentable로 감싸 우회.
- **Web에서 변경한 file→folder 매핑**: 사이드바 카운트는 30초 안에 반영되지만
  완전한 매핑 동기화는 detail fetch (Backfill / 파일 클릭) 후에야 적용.
- **ElevenLabs 비용**: 9분 ≈ 5,081 chars 소진. Creator $22 plan = 약 9시간 STT.
  TTS와 quota 공유.
- **화자 식별 ≠ 변별**: STT는 음향 기반 클러스터링만 가능 — "이 목소리=특정 화자"는
  사후 라벨링으로 해야 함. Section-aware relabel UI로 해결.

---

## 4. 우선순위 To-do

### P0 (UX 다듬기)
(2026-06-12 v0.3에서 전부 완료 — §2 참고)

### P1 (기능 확장)
- [ ] **Audio playback synced with transcript** — 현재 시간 위치 highlight,
      transcript 클릭 → 해당 지점으로 seek
- [ ] **Speaker identification heuristic**: 첫 30초의 발화 빈도/길이 패턴으로
      "self일 가능성 높음" 자동 추정 + 1-click 확인
- [ ] **Topic-shift 자동 감지**: 침묵 기반 분할 외에 LLM에 transcript 던져
      "주제 전환 지점 timestamp 추출" 모드 (옵션)
- [ ] **배치 transcribe**: 폴더 단위 또는 검색 결과 단위로 ElevenLabs Scribe
      일괄 실행 (parallel=N)
- [ ] **Saved searches**: 자주 쓰는 필터(특정 폴더 + 키워드 + 미캐시)를 사이드바에
      저장
- [ ] **Slot 결과 비교 뷰**: 같은 파일에서 Claude vs Codex vs Gemini 결과를
      side-by-side 표시
- [ ] **Whisper 백엔드 옵션**: ElevenLabs 외 OpenAI Whisper API 추가
      (시간당 $0.36, 화자분리는 pyannote 별도)
- [ ] **Mind map export** (Plaud 웹에 있는 기능): file detail에서 추가 데이터타입
      찾아 import

### P2 (자동화)
- [ ] **Agent 자율 루프**: cron 또는 launchd로 매시간 sync → 새 파일 detail 페치 →
      자동 transcribe → 자동 integrated summary → 자동 Obsidian
- [ ] **Hook into Obsidian on save**: 통합 요약 생성 즉시 옵시디언 vault에
      .md 파일 자동 생성 (현재는 `plaud obsidian` 수동)
- [ ] **Webhook-style notification**: 새 녹음 도착 시 macOS 알림
- [ ] **Apple Silicon용 .app 번들**: 현재 SwiftPM debug binary →
      [SwiftBundler](https://github.com/stackotter/swift-bundler)로 Info.plist
      포함 .app 패키징 (그래야 SwiftUI VideoPlayer 정상 사용 가능)

### P3 (장기)
- [ ] **Voice fingerprinting**: 자주 만나는 화자별 voiceprint 등록 → Scribe
      결과의 speaker_X를 자동 매핑 (서드파티 라이브러리 필요)
- [ ] **Multi-vault support**: 회사용/개인용 옵시디언 vault 분기
- [ ] **iOS companion app**: 같은 Plaud 데이터에 폰에서 접근
      (SwiftData 동기화 필요)
- [ ] **Local LLM 옵션**: Ollama로 통합 요약 (privacy 민감 케이스)

---

## 5. 검증된 워크플로우

```
1. 앱 Auth 버튼 → "Authenticate with Plaud"   (최초 1회 + 토큰 만료 시.
   CLI는 cURL 복사 후 plaud refresh-auth — 클립보드 자동.
   fallback: pbpaste | plaud onboard. 상태 확인: plaud auth)
2. plaud sync             (1024 파일 메타데이터 캐시)
3. (선택) plaud sync-content   (전사/요약 일괄 백필, 8-10분)
4. 앱 실행 → 파일 클릭     (캐시 없으면 자동 detail 페치)
5. CMDS 탭 → Transcribe   (ElevenLabs 화자분리)
6. Section relabel        (speaker_0=Speaker A 등)
7. AI Inspector 슬롯 → Generate (Integrated 모드)
8. 결과 확인 → Send to Obsidian or Copy
```

---

## 6. 주요 결정 기록

- **SwiftUI executable** vs Xcode app: 빠른 iteration 우선 → SwiftPM debug
  binary. `.app` 번들은 P2에서 처리.
- **GRDB** vs sqlite3 직접: read/write 풀 + StatementArguments + FetchableRecord로
  타입 세이프. 의존성 1개로 가치 충분.
- **Optimistic write 패턴**: 사용자 액션 → Swift가 즉시 SQLite 쓰고 reload →
  Python CLI는 백그라운드. 사용자가 네트워크 대기를 안 함.
- **Markdown 결과물 디스크 저장**: SQLite blob에 안 넣음. CLI/Obsidian/git에서
  직접 접근 가능 + diff/grep 자유.
- **CLI 모드 우선**: 사용자가 claude/codex 구독 보유, OAuth 활용 시 사실상 무료.
  API는 fallback.
- **Web embed 시도 실패 후 native 회귀**: 1시간 가량 try했지만 SPA의 cookie
  기반 인증이 fetch 인젝션으로 우회 안 됨. native가 폴더 edit/delete + 모든
  Plaud 기능 재구현해야 했지만 결국 더 빠른 UX + 양방향 sync 자동.
  (2026-06-11 부분 번복: 앱 UI 임베드는 여전히 기각이지만, **로그인 자격증명
  캡쳐용 WKWebView**는 Auth 시트의 fallback으로 부활 — 기본은 browser cURL
  import. §3 참고.)
- **인증 보안 계약 (2026-06-11)**: `.env`는 0600으로 기록하고
  `.env.bak-<epoch>` 온디스크 백업은 제거 — 디스크에 자격증명 사본을 남기지
  않는다. `web-auth`는 live 검증을 통과해야 `.env`를 쓴다
  (validate-before-write); 네트워크 불통이면 저장 후 `live_check_unavailable`
  경고로만 알린다.

---

## 7. 환경

- **macOS**: 14+ (Sonoma)
- **Python**: 3.11+ (`uv` 권장)
- **Swift**: 5.9+
- **CLIs (옵션)**: `claude` (Anthropic), `codex` (OpenAI), `gemini` (Google)
- **API keys (옵션)**: `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` /
  `XAI_API_KEY` / `ELEVENLABS_API_KEY` (필수, ~/.zshrc fallback OK)
- **Obsidian Vault**: `<your-obsidian-vault>` (선택)
  (env: `PLAUD_OBSIDIAN_VAULT`로 지정)
