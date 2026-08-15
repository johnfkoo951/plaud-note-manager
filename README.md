# Plaud Note Manager

Plaud Cloud의 녹음 파일을 동기화 · 관리 · 가공하는 통합 워크스페이스.
하나의 Python core를 skill / agent / SwiftUI app 세 surface가 공유합니다.

## Architecture

```
plaud-note-manager/
├── core/           # 공유 Python 라이브러리 (API 클라이언트, 모델, SQLite 저장소)
├── cli/            # typer 기반 CLI (sync / classify / metadata / meeting-note / cmds-* / query / export)
├── skill/          # Claude Code skill (core/cli를 호출하는 얇은 래퍼)
├── agent/          # 자율 에이전트 (주기 동기화 + 후처리)
├── app/            # SwiftUI 네이티브 macOS 앱
├── data/           # SQLite, 다운로드, 메타 캐시 (gitignored)
├── web/            # 공개 랜딩 (plaud.cmdspace.work · Vercel)
├── docs/           # PLAUD-ACCESS-LAYERS.md (5축 비교 SSOT) + STORAGE/REQUESTS/DISCLOSURE
└── tests/
```

## Public landing

- <https://plaud.cmdspace.work> — Plaud 접근 5축 가이드 (Web · Desktop · MCP · Skill · App)
- 소스: `web/` (cmdspace-web-builder v4.3 Landing 템플릿)
- 재배포: `cd web && vercel deploy --prod --yes`
- 콘텐츠 SSOT: `docs/PLAUD-ACCESS-LAYERS.md`

핵심 원칙: **인증 · API 호출 · 메타데이터 저장은 core 한 곳에만 존재.**
skill/agent/app은 모두 core를 통해 Plaud에 접근합니다.

## Quick Start

```bash
# 1. 의존성 설치 (uv 권장)
uv sync

# 2. 자격증명 설정
#    macOS 앱: 툴바 Auth 버튼 → Authenticate with Plaud
#    Plaud Web Login으로 1회 로그인하면 자동 갱신까지 검증·활성화됨.
#    cURL import는 수동 비상 경로로만 지원
#    CLI: web.plaud.ai에서 cURL 복사 후
pbpaste | uv run plaud onboard
#    + 자동 갱신 활성화(1회): devtools Application > Local Storage에서
#      현재 `pld_<account>:workspaceList` 값을 복사한 후
uv run plaud ws-bootstrap

# 3. 파일 목록 동기화
uv run plaud sync

# 4. 다운로드
uv run plaud download <file-id>

# 5. stable Plaud id 기준 로컬 metadata/tag 생성
uv run plaud metadata-generate <file-id>
uv run plaud tag-add <file-id> "회의록"
uv run plaud meeting-note <file-id>
uv run plaud models
uv run plaud folder-plan
uv run plaud classify --apply
```

모델 프리셋은 Obsidian 볼트의
`<your-obsidian-vault>/40. Docs/49. API Information`
frontmatter에서 읽습니다 (볼트 경로는 `PLAUD_OBSIDIAN_VAULT`로 설정).
앱/CLI의 AI Summary를 수정할 때는 이 경로를 먼저 확인합니다.
Grok은 xAI API(`XAI_API_KEY`) backend로 사용합니다.
`metadata-generate`는 녹음 제목/요약/볼트 맥락을 기준으로 로컬 메타데이터,
Obsidian-style tags, `usage_status`, Plaud 폴더 분류를 함께 갱신합니다.

**토큰 갱신 — 자동 갱신이 기본입니다.** 워크스페이스 refresh token을
한 번 부트스트랩하면 (앱 Plaud Web Login 1회, 또는 `plaud ws-bootstrap`)
이후 24h 토큰은 모든 CLI 명령 실행 시 자동으로 갱신됩니다 — 브라우저 불필요.
수동 강제 갱신은 `uv run plaud ws-refresh`. refresh token은 사용할 때마다
로테이션되며 access token·cookie·workspace 정보와 함께 **macOS Keychain의 단일
원자적 항목**으로 저장됩니다. 기존 `.env` 평문 인증 정보는 첫 실행 시 Keychain
read-back 검증 후 자동 삭제됩니다. `PLAUD_AUTO_REFRESH=0`으로 자동 갱신을 끌 수 있습니다.

부트스트랩 전이라면 기존 경로도 그대로 동작합니다:
`uv run plaud refresh-auth` (클립보드의 Plaud cURL 자동 파싱) 또는 앱 Auth 버튼.
자격증명 상태 확인은 `uv run plaud auth` (만료 카운트다운 + auto-refresh 상태,
`--live`로 실 토큰 검증).

**옵시디언 볼트 송출 — `plaud vault-send <id>`.** 통합본(Integrated) 우선으로
CMDS frontmatter 노트를 만들어 메인 볼트 `00. Inbox`에 넣습니다 (통합본이
없으면 슬롯 요약 → Plaud 요약 순 폴백).

```bash
uv run plaud vault-send <id>                    # 통합본 → 메인 볼트 00. Inbox (즉시)
uv run plaud vault-send <id> --dest meetings    # → 60. Collections/63. Meetings
uv run plaud vault-send <id> --to wiki          # → CMDS_LLM_Wiki 위성 볼트
uv run plaud vault-send <id> --via claude       # claude -p 가 CMDS 컨벤션으로 정형화 후 저장
uv run plaud vault-send <id> --via claude-window  # Terminal의 Claude Code가 스킬로 직접 파일링
```

`--via direct`(기본)는 AI 없이 즉시 조립, `--via claude`는 headless 정형화
(`--ai-model codex|gemini|grok` 선택 가능), `--with-transcript`로 전사본 포함,
`--open`으로 저장 즉시 Obsidian에서 열기. wiki 볼트 경로는 메인 볼트 옆
`CMDS_LLM_Wiki`를 자동 인식하며 `plaud config-wiki-vault`로 바꿀 수 있습니다.
앱에서는 Work Sidebar의 **Send to Vault** 메뉴(모드/목적지 선택), 각 슬롯
카드의 ✈ 버튼(그 결과물만 전송), ⌘K 커맨드 팔레트에서 사용합니다.

## Setup on a new machine

처음 클론하는 사용자는 다음 순서로 셋업합니다.

```bash
# 1. 클론 후 진입
git clone <repo-url> plaud-note-manager
cd plaud-note-manager

# 2. 의존성 설치
uv sync

# 3. 자격증명 설정 — macOS 앱 툴바 Auth → Plaud Web Login (권장, 1회)
#    앱이 namespaced workspace refresh token을 즉시 검증·회전해 Keychain에 저장.
#    headless/CLI라면 web.plaud.ai에서 본인 cURL을 복사
#    (DevTools → 요청 우클릭 → Copy as cURL)한 뒤
pbpaste | uv run plaud onboard

# 4. 파일 목록 동기화
uv run plaud sync
```

Obsidian 볼트 연동은 선택 사항입니다. 회의록 송출 · 모델 프리셋 로딩 등
볼트 기반 기능을 쓰려면 환경변수 `PLAUD_OBSIDIAN_VAULT`로 본인 볼트 경로를
지정하세요 (미설정 시 볼트 의존 기능만 비활성화되고 나머지는 그대로 동작).

```bash
export PLAUD_OBSIDIAN_VAULT="<your-obsidian-vault>"
```

## Development Status

현재는 초기 셋업을 넘어 Plaud 관리용 Python CLI와 SwiftUI macOS 앱이
실사용 가능한 단계입니다.

- [x] core: Plaud API 클라이언트, SQLite 메타/컨텐츠 캐시, 네트워크 실패 메시지 정제
- [x] auth: 자동 갱신(`ws-bootstrap` 1회 → 모든 명령이 24h 토큰 자동
      리프레시, 전역 flock 직렬화 + Keychain 원자적 로테이션 영속화) + 앱 Auth 시트
      (namespaced Plaud Web Login이 자동 갱신을 즉시 검증, cURL은 수동 fallback) +
      `plaud auth` / `refresh-auth` / `web-auth` / `ws-refresh` / `ws-bootstrap`,
      live 검증 + validate-before-write, legacy `.env` 인증 자동 이관
- [x] cli: sync, content backfill, folder CRUD, download/export, Obsidian 송출
- [x] metadata: Plaud file_id 기준 local metadata DB, Obsidian-style tags,
      usage status, auto folder routing, main-vault meeting note generation
- [x] taxonomy: `core/classification.py` SSOT 기반 14개 카테고리 분류 +
      `folder-plan` / `classify --apply` CLI, 앱 사이드바 Work 섹션
- [x] CMDS: ElevenLabs Scribe 전사, speaker relabel, saved speakers
- [x] AI: Claude/Codex/Gemini CLI/API + Grok API backend, CMDS 볼트
      API Information frontmatter 기반 SOTA preset + custom model id,
      template/slot 기반 요약
- [x] app: SwiftUI + GRDB 파일 브라우저, 오디오 스트리밍, AI inspector,
      usage status picker, 정돈된 Settings/사이드바/툴바
- [x] v0.2.0: 폴더 단일화(+folder-doctor), Grok Build CLI 백엔드(SuperGrok 구독
      OAuth, API 비용 0), Plaud 서버 전사본 스피커 변경(plaud-relabel), 제목
      인라인 편집, 볼트 기반 템플릿 3종(cmds-meeting/lecture/coaching), DB 인덱스·쿼리 성능 개선
- [x] quality gate: `pytest`, `ruff check`, `ruff format --check`,
      `swift build --package-path app`
- [ ] agent: launchd/cron 기반 완전 자동 루프 (선행 작업: `plaud status` 의미 재정의)

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check core cli tests
uv run ruff format --check core cli tests

cd app
swift build
```

## macOS App Packaging

macOS에서 실제 앱처럼 사용하려면 `.app` 번들을 만들어 `/Applications`에 배포합니다.
Codex가 앱을 만들거나 수정한 뒤에는 이 배포 경로를 기본으로 사용합니다.

```bash
scripts/package-macos-app.sh
open "/Applications/Plaud Note Manager.app"
```

기본 아이콘 소스는 `app/Resources/AppIcon.png`입니다.
