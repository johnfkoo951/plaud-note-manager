---
name: plaud-note-agent
description: Autonomous agent that periodically syncs Plaud Cloud, downloads new recordings, and triggers downstream processing (transcription, summarization). Reads/writes the shared SQLite store under plaud-note-manager.
---

# Plaud Note Agent

## Responsibility

1. `plaud sync` — pull latest file list, upsert into local DB
2. For files missing `file_content`: `plaud sync-content` or targeted `plaud detail <id>`
3. Optional CMDS path: `plaud cmds-transcribe <id>` → `plaud cmds-integrate <id>`
4. Report a brief summary of what changed since last run.
   - `plaud status`는 현재 status 컬럼별 파일 수(coarse count)일 뿐 실제 진행률이
     아니다 (STATUS.md §0 참고 — 대부분 `new`라 캐시/전사/통합 진행을 반영 못함).
   - 변경분 보고는 `plaud query`(tag/keyword로 L1 brief) 또는 `plaud brief <id>`로
     새로 들어온 녹음을 짚거나, 직전 실행 mtime 기준 `plaud resources --since <unix>`
     diff으로 새/갱신된 로컬 리소스만 추려 보고한다.

## Invocation

자율 모드 (cron/loop)로 돌아가는 것을 가정. 한 번 실행 시:

```bash
cd ~/DEV/plaud-note-manager
uv run plaud auth --json                       # 1) 자격증명 상태 게이트 (아래 규칙)
uv run plaud sync
uv run plaud sync-content     # 끝에서 auto-metadata 훅이 자동 실행됨 (config `auto_metadata`, 기본 on)
# 백로그가 남았다고 보고되면 (remaining N): uv run plaud metadata-auto  # 다음 배치
# 과거 파일 일괄 백필이 필요할 때만: uv run plaud metadata-auto --backfill --limit 50
# 진행 보고: status(coarse count) 대신 변경분 위주로
uv run plaud query -n 10                       # 최근 들어온 녹음 L1 brief
uv run plaud resources --since "$LAST_RUN_MTIME" --json   # 직전 실행 이후 갱신 리소스 diff
```

- `auth --json`의 `state`가 `expired`면 먼저 Tier-1 자동 복구를 1회 시도:
  `uv run plaud auth-recover --json` (cmux 브라우저의 살아있는 web.plaud.ai
  세션에서 workspaceList 재수확 — 비밀번호 입력 없음). `status:"ok"`면 계속 진행.
  - aside 등 MCP 브라우저 툴이 있는 Claude 세션이라면 대안: web.plaud.ai 탭에서
    `core/auth_recover.py`의 `CAPTURE_JS`를 실행해 값을 얻고
    `uv run plaud ws-bootstrap --stdin`에 파이프.
  - 둘 다 실패(`no_session` 등)하거나 `state`가 `unconfigured`면 API 명령을
    시도하지 말고 "재인증 필요 (사용자 1회 브라우저 로그인 — 앱 Auth 시트)"를
    보고하고 종료한다.
- `expiring`이면 계속 진행하되 남은 만료 시간(`remaining_human`)을 보고에 포함한다.

## Future Work

- `agent/run.py` — DB를 polling하며 단계별 작업 dispatch
- 폴더/검색 단위 CMDS 전사 batch queue
- Obsidian vault으로 결과 노트 자동 생성 (markdown-formatter 스킬 연계)
