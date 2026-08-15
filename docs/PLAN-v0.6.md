# Plaud Note Manager v0.6 고도화 계획서

- 작성: 2026-08-14
- 기준 버전: v0.5.1 (main)
- 관련 문서: `STATUS.md` · `docs/PLAUD-ACCESS-LAYERS.md` · `docs/SPEC.md`

## 0. 요약 (TL;DR)

| # | 기능 | 핵심 | 상태 |
|---|---|---|---|
| F1 | 기본 창 크기 = 현재 작업 창 | `defaultSize 1608×854` (14" MBP 실측) | ✅ 반영 완료 |
| F2 | Plaud 웹 인증 완전 자동화 | 3-tier 복구: ws-refresh(있음) → 브라우저 세션 재캡쳐(cmux CLI / aside는 agent-side) → 사용자 개입 | ✅ 구현 완료 (2026-08-15, `plaud auth-recover`) |
| F3 | 메타데이터 상시 자동 생성 | sync 후 조건 충족 파일에 `metadata-generate` 자동 실행 (agent + 앱) | ✅ 구현 완료 (2026-08-15, `plaud metadata-auto` + sync 훅) |
| F4 | 메타데이터 모델 = GPT (Codex 구독) | `metadata_model: "codex"` + backend `cli` (codex exec, 구독 인증) — 설정으로 교체 가능 | ✅ 구현 완료 (2026-08-15) |

> 구현 시 확정된 결정: 자동 생성 기본 범위는 **최근 7일 내 캐시된 파일**
> (백필은 `plaud metadata-auto --backfill` 옵트인) · 자동 경로에서 폴더는
> **제안만** (이동은 기존 `classify --apply` 경로 유지) · Tier-1 CLI 드라이버는
> **cmux** (aside 등 MCP 브라우저는 agent-side에서 `CAPTURE_JS` 실행 →
> `ws-bootstrap --stdin` 파이프).

---

## F1. 기본 창 크기 (완료)

- 스크린샷 실측: @2x 3216×1708 px → **1608×854 pt** (비율 ~1.88:1).
- `PlaudNoteApp.swift`의 `.defaultSize(width: 1920, height: 1180)` → `1608×854`로 변경 완료.
- `minWidth 1280 / minHeight 800`은 유지 (기본값과 충돌 없음).
- macOS는 한 번 사용자가 창을 조절하면 frame autosave가 우선하므로, 기존 사용자에게 강제 적용하려면 `defaults delete <bundle-id> "NSWindow Frame ..."` 또는 앱 재설치가 필요 — 신규 실행부터는 이 크기로 뜬다.

## F2. Plaud 웹 인증 완전 자동화

### 2.1 현황 — 무엇이 이미 되고 무엇이 남았나

이미 됨 (2026-07 ws_refresh 출하):
- Plaud 웹 인증은 2-tier OAuth. `POST /user-app/auth/workspace/refresh/{wid}`가 24h 앱 토큰을 재발급.
- 워크스페이스 refresh token을 1회 부트스트랩하면 (Embedded Web Login 자동 캡쳐 또는 `plaud ws-bootstrap`) 모든 CLI 명령이 만료 6h 전 **헤드리스 자동 갱신**. 로테이션·flock 직렬화 포함.

남은 갭 = **부트스트랩 체인이 끊겼을 때** (refresh token 자체가 만료/무효화, Plaud 서버 측 세션 폐기, `.env` 유실):
- 현재는 "재인증 필요 (브라우저 로그인, 자동화 불가)"로 종료 → 사용자가 앱 Auth 시트나 cURL 캡쳐를 수동 수행.

### 2.2 설계 — 3-tier 자동 복구 사다리

```
Tier 0  ws-refresh (헤드리스, 이미 출하)           — 평시 100%
Tier 1  브라우저 세션 재캡쳐 (aside / cmux 자동)    — refresh 체인 단절 시
Tier 2  사용자 개입 (앱 Auth 시트 / Embedded Login) — 브라우저 세션도 죽었을 때만
```

**Tier 1 핵심 아이디어**: 비밀번호를 입력하는 로그인 자동화가 아니라, **이미 로그인돼 있는 브라우저 프로필의 살아있는 세션을 재수확**하는 것. Chrome(aside)나 cmux 브라우저가 web.plaud.ai에 로그인 상태라면:

1. 브라우저 컨트롤(aside `repl` 또는 `cmux-browser` CLI)로 `https://web.plaud.ai` 접속
2. 페이지 JS 실행: `localStorage.getItem("workspaceList")` → 워크스페이스 refresh token 획득 (요청 헤더에 절대 안 나오는 값 — 기존 Embedded Login이 캡쳐하는 바로 그것)
3. 결과 JSON을 `plaud ws-bootstrap --stdin`으로 파이프 → `.env` 갱신 → Tier 0 체인 재가동

비밀번호 입력 자동화는 하지 않는다 (보안 원칙 + 브라우저 자동화 정책). 브라우저 세션마저 만료된 경우만 Tier 2로 넘어가 사용자가 1회 로그인.

### 2.3 구현 항목

| 항목 | 위치 | 내용 |
|---|---|---|
| `plaud auth-recover` | `cli/main.py` + `core/auth_recover.py` (신규) | `--browser aside\|cmux\|auto` 선택. 브라우저 컨트롤로 workspaceList 캡쳐 → `ws-bootstrap` 재사용. 성공/실패를 `WebAuthResult`와 같은 tri-state로 보고 |
| aside 드라이버 | `core/auth_recover.py` | Claude Code 세션 안에서는 `mcp__aside__repl`이 직접 실행; CLI 단독 실행 시엔 cmux-browser 경로 사용 |
| cmux 드라이버 | 〃 | `cmux` CLI 존재 시: navigate → eval JS → JSON stdout |
| agent 게이트 완화 | `agent/AGENT.md` | `state=expired`일 때 즉시 종료하지 말고 `plaud auth-recover --browser auto` 1회 시도 → 성공 시 계속, 실패 시 기존대로 보고 후 종료 |
| 앱 연동 | `FileStore+Auth.swift` | 401 감지 시 "Auto-recover via browser session" 버튼 (내부적으로 CLI 호출) — Auth 시트 앞단에 배치 |

### 2.4 "언제 되려나"에 대한 답

- 평시 무개입 갱신은 **이미 완성** (ws_refresh).
- Tier 1은 신규 외부 API 리버싱이 필요 없음 (기존 캡쳐 로직 재사용 + 브라우저 컨트롤 배관만) — **1~2 세션 분량**의 작업. 리스크는 aside/cmux 환경 감지 정도.

## F3. 메타데이터 상시 자동 생성

### 3.1 원칙

"수동 Generate 버튼"에서 "**조건이 갖춰지면 알아서 생성, 사용자는 결과만 검수**"로 전환. 스크린샷의 `Metadata Ready` 배지가 기본 상태가 되도록.

### 3.2 트리거 파이프라인

```
plaud sync / sync-content
  → 신규·갱신 파일 중 조건 검사:
      transcript 또는 summary 캐시 존재
      AND note_metadata 없음 (또는 소스가 갱신됨)
  → plaud metadata-generate <id>  (F4 모델로)
  → classification_confidence ≥ 임계치면 폴더 배치 제안까지 채움 (적용은 기존 정책대로 preview)
```

실행 주체:
- **agent 루프**: sync 직후 배치 처리 (주 경로). `agent/AGENT.md`에 단계 추가.
- **앱**: sync 완료 콜백에서 백그라운드 큐로 동일 CLI 호출 — 앱만 쓰는 날도 커버.
- **CLI**: `plaud sync --auto-metadata` 플래그 (config가 켜져 있으면 기본 동작).

### 3.3 설정 (app_config.py)

```jsonc
{
  "auto_metadata": true,          // 신규 — 상시 자동 생성 on/off
  "metadata_model": "codex",      // 신규 — F4. 어느 프로바이더로 생성할지
  "classify_model": "claude"      // 기존 유지 (자동분류는 별도 선택 가능)
}
```

- 비용/속도 가드: 한 실행당 처리 상한 (기본 20개), 실패 파일은 재시도 백오프 기록 (`note_metadata.last_attempt_at`).
- 멱등성: 동일 소스 해시면 재생성 스킵 — 트랜스크립트/요약 해시를 metadata_json에 함께 저장.

## F4. 메타데이터 모델 — GPT via Codex 구독 인증

### 4.1 현황이 이미 절반을 해결

`core/summarize.py`는 이미 backend 2종을 지원:
- `cli`: `codex exec --skip-git-repo-check` 셸아웃 — **Codex CLI의 ChatGPT 구독 로그인을 그대로 사용** (API 키 불필요). 기본 config에서 `backends.codex = "cli"`가 이미 디폴트.
- `api`: `OPENAI_API_KEY` 직결.

즉 "codex 구독 인증으로 GPT 사용"은 새 인증 구현이 아니라 **메타데이터 경로의 기본 모델을 codex로 지정**하는 설정 문제.

### 4.2 변경 사항

| 항목 | 내용 |
|---|---|
| `app_config.py` | `metadata_model: "codex"` 키 추가 (기본값 codex). `generate_note_metadata` 호출부가 `classify_model` 대신 이 키를 읽도록 |
| `cli/main.py metadata-generate` | `--model` 미지정 시 config의 `metadata_model` 사용 (현재 하드코딩/classify_model 의존 제거) |
| 앱 Settings 시트 | "Metadata Model" 피커 추가 — provider(claude/codex/gemini/grok) × backend(cli 구독/api 키) × model preset (`model_registry` 프리셋 재사용). Work Sidebar 슬롯 UI와 동일한 어휘 |
| CLI 설정 명령 | `plaud config-metadata-model codex [--backend cli] [--id gpt-5.5]` |
| 프리플라이트 | codex CLI 부재/미로그인 시 명확한 에러 (`model_available` 확장: `codex login status` 체크) + 폴백 순서 옵션 (`metadata_model_fallback: ["claude"]`) |

### 4.3 나중에 바꾸는 방법 (사용자 관점)

- 앱: Settings → Metadata Model 피커.
- CLI: `plaud config-metadata-model claude` 한 줄.
- 슬롯별 오버라이드는 기존 Work Sidebar 슬롯 시스템이 이미 담당 (Default/codex power/GemGem) — F4는 "슬롯 미지정 시의 전역 기본값"만 정한다.

---

## 5. 실행 순서 (마일스톤)

1. **M1 — F4 설정 배관** (반나절): `metadata_model` 키 + CLI/호출부 연결 + 테스트. F3의 전제.
2. **M2 — F3 자동 생성** (1일): sync 후크 + agent 단계 + 상한/멱등 가드 + 앱 백그라운드 큐.
3. **M3 — F2 Tier 1 복구** (1~2일): `auth_recover` 모듈 + cmux/aside 드라이버 + agent 게이트 완화 + 앱 버튼.
4. **M4 — 문서/랜딩 반영**: `PLAUD-ACCESS-LAYERS.md` §10 갱신, STATUS.md, 랜딩.

## 6. 테스트 계획

- `tests/test_metadata_auto.py`: 조건 검사(트랜스크립트 유무 × 기존 메타 유무 × 해시 동일), 상한, 백오프.
- `tests/test_app_config.py` 확장: 새 키 머지/기본값.
- `tests/test_auth_recover.py`: 드라이버 모킹 — workspaceList 캡쳐 성공/브라우저 세션 만료/CLI 부재 3분기. 기존 `test_ws_refresh.py` 픽스처 재사용.
- 수동: codex CLI 로그아웃 상태에서 metadata-generate → 폴백 메시지 확인.

## 7. 리스크 & 열린 결정

| 리스크 | 대응 |
|---|---|
| codex exec 호출 지연 (구독 경로는 API보다 느릴 수 있음) | 배치 상한 + 백그라운드 실행이라 UX 영향 없음. 느리면 `metadata_model_fallback` |
| Plaud가 workspaceList 저장 방식 변경 | 캡쳐 실패 시 Tier 2로 자연 강등 — 기능 파손 아님 |
| 자동 생성이 낮은 품질 메타를 대량 생산 | `classification_confidence` 저장 + 앱에서 저신뢰 배지 · 재생성 버튼 유지 |

열린 결정 (구현 전 확인하고 싶은 것):
1. **자동 생성 범위** — 신규 파일만 vs 기존 424개 Unfiled 백필까지? (백필이면 codex 구독 rate 고려해 야간 배치 권장)
2. **classify까지 자동 적용?** — 현재는 preview 원칙. auto_metadata가 폴더 이동까지 자동 적용할지, 제안만 채울지. (제안만이 안전한 기본이라 판단)
3. **Tier 1 기본 브라우저** — aside(Chrome 확장, 세션 공유 큼) vs cmux(독립 프로필). 평소 Chrome으로 web.plaud.ai 쓰신다면 aside가 성공률 높음.
