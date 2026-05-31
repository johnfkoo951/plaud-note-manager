# Plaud Note Manager

Plaud Cloud의 녹음 파일을 동기화 · 관리 · 가공하는 통합 워크스페이스.
하나의 Python core를 skill / agent / SwiftUI app 세 surface가 공유합니다.

## Architecture

```
plaud-note-manager/
├── core/           # 공유 Python 라이브러리 (API 클라이언트, 모델, SQLite 저장소)
├── cli/            # typer 기반 CLI, 59개 명령 (sync / classify / metadata / meeting-note / cmds-* / query / export)
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

# 2. 자격증명 설정 (web.plaud.ai에서 cURL 복사 후)
pbpaste | uv run plaud onboard

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

## Setup on a new machine

처음 클론하는 사용자는 다음 순서로 셋업합니다.

```bash
# 1. 클론 후 진입
git clone <repo-url> plaud-note-manager
cd plaud-note-manager

# 2. 의존성 설치
uv sync

# 3. web.plaud.ai에서 본인 cURL을 복사 (DevTools → 요청 우클릭 → Copy as cURL)한 뒤
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
