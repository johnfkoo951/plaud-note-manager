# Plaud Note Manager

Plaud Cloud의 녹음 파일을 동기화 · 관리 · 가공하는 통합 워크스페이스.
하나의 Python core를 CLI / skill / SwiftUI app이 공유하며, agent는 향후 자동화
surface로 남아 있습니다.

> **참고용 공개본입니다.**
> 이 저장소는 개인 개발판의 소스를 읽을 수 있게 공개한 것으로, 제작자의 로컬 환경
> (Obsidian 볼트 경로, 개인 인증 경로, 구독 기반 모델 CLI)을 전제로 동작합니다.
> 패키징된 산출물이나 설치 안내는 제공하지 않으며, 코드와 설계를 참고하는 용도입니다.
>
> **바로 실행할 수 있는 릴리즈가 필요하시면
> [Plaud Note Manager Community](https://github.com/johnfkoo951/plaud-note-manager-community)**
> 를 이용하세요. macOS(arm64 · x86_64)와 Windows용 빌드를
> [Releases](https://github.com/johnfkoo951/plaud-note-manager-community/releases)에서
> 받을 수 있고, 개인 볼트·개인 인증 경로 없이 본인 계정과 본인 API key로만 동작하는
> 제한판입니다.

## Architecture

```
plaud-note-manager/
├── core/           # 공유 Python 라이브러리 (API 클라이언트, 모델, SQLite 저장소)
├── cli/            # typer 기반 CLI (sync / classify / metadata / meeting-note / cmds-* / query / export)
├── skill/          # Claude Code skill (core/cli를 호출하는 얇은 래퍼)
├── agent/          # 향후 자율 자동화를 위한 운영 레시피 (실행기·scheduler 미구현)
├── app/            # SwiftUI 네이티브 macOS 앱
├── data/           # SQLite, 다운로드, 메타 캐시 (gitignored)
├── web/            # 공개 랜딩 (plaud.cmdspace.work · Vercel)
├── docs/           # 현재 기능·설계 정본 + 접근 비교·저장·운영 문서
└── tests/
```

## Public landing

- <https://plaud.cmdspace.work> — Plaud 접근 6축 가이드 (Web · Desktop · MCP · CLI · Skill · App)
- 소스: `web/` (cmdspace-web-builder v4.3 Landing 템플릿)
- 재배포: `cd web && vercel deploy --prod --yes`
- 콘텐츠 SSOT: `docs/PLAUD-ACCESS-LAYERS.md`

핵심 원칙: **인증 · API 호출 · 메타데이터 저장은 core 한 곳에만 존재.**
현재 CLI/skill/app은 core를 통해 Plaud에 접근하고, 향후 agent도 같은 계약을 따릅니다.

현재 앱에 실제로 연결된 기능, 각 기능의 목적과 설계 원리, 데이터·보안 경계,
부분 구현·미구현 항목은
[`docs/APP-FEATURES-AND-DESIGN.md`](docs/APP-FEATURES-AND-DESIGN.md)를 기준으로 봅니다.
공개 접근 방식 비교는 `docs/PLAUD-ACCESS-LAYERS.md`가 정본입니다. `STATUS.md`,
`docs/SPEC.md`, PLAN/REQUESTS 문서는 시점별 기록이며 현재 구현 상태의 정본이 아닙니다.

## 0.8.3 로그인 창·Google 팝업 수정

- 설정창의 420pt 임베디드 로그인 화면을 별도 크기 조절 창으로 이동했습니다. 기본 폭 1120pt, 높이는 최대 900pt로 현재 화면 안에 맞추며 사용자가 조절한 크기를 기억합니다.
- Google 로그인 팝업을 별도 WebKit 창으로 열어 원래 Plaud 페이지와 로그인 완료 전달 경로를 유지합니다.
- 이전 접속 헤더가 새 로그인 캡처를 막지 않게 하고, 현재 workspace access token과 일치하는 갱신 정보만 연결합니다.
- Google 패스키 오류를 상태에 표시합니다. 같은 Google 계정에서 ‘다른 방법 시도’를 사용하거나 기본 브라우저에서 로그인 후 cURL을 가져올 수 있습니다. 창 크기 변경만으로 Google 패스키 지원을 보장하지 않습니다.
- 수정과 실제 검증 범위: [`docs/REVIEW-2026-09-07-LOGIN-WINDOW.md`](docs/REVIEW-2026-09-07-LOGIN-WINDOW.md).

## 0.8.2 로그인·기본 폭 개선

- 현재 접속과 자동 갱신 상태를 구분하고, 통신·저장소 장애에 반복 로그인을 요구하지 않습니다.
- 전사·요약이 없는 녹음은 1시간, 실패는 5분 뒤 재요청합니다. 백그라운드 부분 실패가 모달 오류창을 반복하지 않습니다. 수동 `sync-content --force`는 즉시 재시도합니다.
- 인증 후보 검증 후 Keychain 교체, 요청 세대별 거절 처리, 오프라인 상태 조회로 세션 회귀와 불필요한 네트워크 대기를 줄였습니다.
- 기본 창 1608×854pt, 사이드바 280pt / 목록 420pt / 작업 패널 350pt. 이후 사용자가 조절한 폭은 저장합니다.
- 인증 화면의 ‘공식 CLI · MCP 읽기 연결’에서 공식 CLI 연결을 확인하고 선택한 녹음의 요약·전사를 읽을 수 있습니다.
- `uv run plaud official-status --json --live`, `uv run plaud official-read <id> --kind summary --json`을 추가했습니다. 설치된 공식 `@plaud-ai/cli`를 절대경로로 호출하므로 같은 이름의 자체 `plaud` 명령과 충돌하지 않습니다.
- API 쓰기/동기화, 공식 CLI 읽기, MCP의 AI 도구 조회 역할과 실제 검증 범위는 [`docs/REVIEW-2026-09-06-AUTH-ACCESS.md`](docs/REVIEW-2026-09-06-AUTH-ACCESS.md)에 정리합니다.

## 0.8.1 개인용 앱 개선

- 수천 개 태그로 실행이 멈추던 사이드바를 전체 태그 검색·80행씩 표시로 개선했습니다.
- 준비된 Python 환경을 직접 사용하고, CLI 출력 교착과 무제한 대기를 막는 실행기를 공용화했습니다.
- 중복 DB reload와 긴 본문·슬롯의 화면 렌더 중 파일 조회를 줄이고, custom integrated 출력 경로를 연결했습니다.
- Cloud 목록 pagination·batch 저장, 기존 사용 상태 보존, 분류 snapshot Undo와 실패 재시도를 보강했습니다.
- 앱 설치는 검증한 staging bundle로 교체하고 이전 bundle과 build provenance를 보존합니다.

수정 전 문제와 전체 기능 검토, 남은 구조 개선 제안은
[`docs/REVIEW-2026-09-05.md`](docs/REVIEW-2026-09-05.md)에 정리했습니다.
외부 계정의 sync·유료 생성·Cloud 변경·Vault 송출은 로컬 실행 확인과 구분해 검증합니다.

## Quick Start

```bash
# 1. 의존성 설치 (uv 권장)
uv sync

# 2. 자격증명 설정
#    macOS 앱: 툴바 Auth 버튼 → Authenticate with Plaud
#    Plaud Web Login에서 workspace refresh pair까지 정상 캡처되면 자동 갱신이 활성화됨.
#    cURL import는 현재 읽기·쓰기 접속을 연결합니다. Plaud가 검증한 뒤
#    Keychain에 저장합니다. 자동 갱신은 앱 로그인으로 별도 연결하며,
#    Chrome의 인증 자료 연결은 인증 화면의 명시적인 버튼으로만 실행합니다.
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
uv run plaud metadata-generate <file-id> --no-auto-folder  # 먼저 로컬 metadata만
uv run plaud tag-add <file-id> "회의록"
uv run plaud meeting-note <file-id>
uv run plaud models
uv run plaud folder-plan                 # taxonomy + CMDS(📚)/index(🏷) 매핑
uv run plaud classify                    # dry run: 규칙 → 약한 판정만 LLM 중재
# 결과를 확인한 뒤 Cloud 폴더를 실제 변경할 때만:
uv run plaud classify --apply
uv run plaud llm-auth                    # Claude/ChatGPT/Gemini/Grok OAuth 로그인 상태
```

모델 프리셋은 Obsidian 볼트의
`<your-obsidian-vault>/40. Docs/49. API Information`
frontmatter에서 읽습니다 (볼트 경로는 `PLAUD_OBSIDIAN_VAULT`로 설정).
앱/CLI의 AI Summary를 수정할 때는 이 경로를 먼저 확인합니다.
Grok도 기본은 vendor CLI의 구독 OAuth backend이며, 사용자가 API backend를
명시적으로 선택한 경우에만 xAI API(`XAI_API_KEY`)를 사용합니다.
`metadata-generate`는 녹음 제목/요약/볼트 맥락을 기준으로 로컬 메타데이터,
Obsidian-style tags, `usage_status`, Plaud 폴더 분류를 함께 갱신합니다.
`--no-auto-folder`를 빼면 confidence 기준을 넘는 경우 Plaud Cloud 폴더도 실제로
이동할 수 있습니다.

**토큰 갱신 — 자동 갱신이 기본입니다.** 워크스페이스 refresh token을
한 번 부트스트랩하면 (앱 Plaud Web Login 1회, 또는 `plaud ws-bootstrap`)
이후 24h 토큰은 모든 CLI 명령 실행 시 자동으로 갱신됩니다. Plaud가 회전
체인을 조기 폐기하면 macOS 앱이 영속 WebKit 계정 세션으로 새 workspace
token 쌍을 조용히 발급하며, 계정 세션까지 만료됐을 때만 로그인을 요청합니다.
수동 강제 갱신은 `uv run plaud ws-refresh`. refresh token은 사용할 때마다
로테이션되며 access token·workspace 정보와 함께 **macOS Keychain의 단일
원자적 항목**으로 저장됩니다. 기존 `.env` 평문 인증 정보는 첫 실행 시 Keychain
read-back 검증 후 자동 삭제됩니다. HttpOnly 계정 refresh cookie는 WebKit 밖으로
내보내지 않습니다. `PLAUD_AUTO_REFRESH=0`으로 자동 갱신을 끌 수 있습니다.

부트스트랩 전이라면 기존 경로도 그대로 동작합니다:
`uv run plaud refresh-auth` (클립보드의 Plaud cURL 자동 파싱) 또는 앱 Auth 버튼.
앱의 cURL 폴백은 Plaud 실서버 검증 전에 Keychain을 교체하지 않으며, access
token 저장과 자동 갱신 준비 상태를 따로 표시합니다. Chrome에서 장기 갱신
정보를 가져오지 못해도 현재 access token은 만료 전까지 사용할 수 있지만,
이 경우에는 자동 갱신이 준비됐다고 표시하지 않습니다.
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
#    앱이 namespaced workspace token 쌍을 검증해 Keychain과 WebKit에 같은 세대로 저장.
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
      (namespaced Plaud Web Login + HttpOnly 계정 세션 기반 무인 재발급,
      cURL은 수동 fallback) +
      `plaud auth` / `refresh-auth` / `web-auth` / `ws-refresh` / `ws-bootstrap`,
      live 검증 + validate-before-write, legacy `.env` 인증 자동 이관
- [x] cli: sync, content backfill, folder CRUD, download/export, Obsidian 송출
- [x] metadata: Plaud file_id 기준 local metadata DB, Obsidian-style tags,
      usage status, auto folder routing, main-vault meeting note generation
- [x] taxonomy: `core/classification.py` SSOT 기반의 설정 가능한 카테고리 분류 +
      `folder-plan` / `classify --apply` CLI, 앱 사이드바 Work 섹션
- [x] v0.8: 규칙이 약할 때만 LLM 중재하는 자동 폴더링(`core/auto_folder.py`),
      taxonomy에 `CMDS:`/`index:` 매핑, deterministic direct/dual 볼트 경로에 CMDS
      frontmatter builder(`core/frontmatter.py`) 적용 + `related:` 실존 노트 위키링크,
      `plaud llm-auth`로 4사 CLI OAuth 상태 확인 (`docs/PLAN-v0.8.md`)
- [x] CMDS: ElevenLabs Scribe 전사, speaker relabel, saved speakers
- [x] AI: Claude/Codex/Gemini/Grok — 기본은 각 CLI의 구독 OAuth(API 키 0), API 모드 선택 가능, CMDS 볼트
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
