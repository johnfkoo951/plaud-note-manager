# Plaud Note Manager v0.7 — Dual Transcribe 원클릭 파이프라인 + Reuse 메타데이터

- 작성: 2026-08-15 · **S1–S5 전체 구현 완료 (같은 날)** — 실 녹음 E2E 검증
  (34초 협의 녹음: ElevenLabs 전사 → "커맨드스페이스 구요한입니다" 자기소개에서
  실명 100% 제안 → 교차분석 → 볼트 08-1. Plaud 레인 착륙)
- 기준: v0.6 (auto metadata · auth-recover) 위에 얹음
- 확정된 결정: 별도 Dual 마크(버튼 시작) · 폴더 제안만 · 서버 화자 반영 off ·
  reuse 채널 = newsletter/lecture/shorts/sns/consulting/research
- 후속 후보: 사이드바 Reuse 스마트 필터 · 마킹 시 자동 시작 옵션 · reuse 인용(quotes) UI

## 0. 목표 한 줄

> 중요한 녹음에 마킹 → 버튼 하나 → ElevenLabs 듀얼 전사 → 화자 오류 교정 →
> 실명 매핑(사용자 확인 1회) → Plaud×ElevenLabs 교차분석 최종 전사본 →
> 메타데이터 → 메인 볼트 규약대로 착륙. 이후 콘텐츠 재사용 체크까지 한 화면에서.

## 1. 이미 있는 것 / 새로 만드는 것

| 단계 | 기존 블록 | v0.7에서 추가 |
|---|---|---|
| ElevenLabs 전사 | `plaud cmds-transcribe` (Scribe, diarize) | 파이프라인 오케스트레이션에 편입 |
| 화자 실명 입력 | `plaud cmds-relabel speaker_0=이름` · saved speakers 테이블 | **실명 자동 제안** (아래 §3) + 확인 UI |
| 교차분석 최종본 | `plaud cmds-integrate` (Plaud×CMDS 융합, FINAL_TRANSCRIPT 마커) | Transcription Context 주입으로 오류 교정 강화 |
| 메타데이터 | v0.6 auto-metadata (codex) | reuse 필드 확장 (§5) |
| 볼트 송출 | `core/vault_send.py` | **볼트 전사본 규약 착륙** (§4) |
| 상태 추적 | `usage_status` | **파이프라인 상태 머신** (§2) |

## 2. 파이프라인 상태 머신 (`dual_status`)

`note_metadata.metadata_json["dual"]`에 저장. 원클릭이지만 **사람 확인 지점에서 일시정지**하는 세미-자동:

```
marked → transcribing → relabel-pending ⏸ → integrating → vault-ready ⏸ → vault-sent
                          (실명 확인)                        (착륙 미리보기 확인, 옵션)
```

- **marked**: 사용자가 "Dual Transcribe" 실행 (또는 중요 마킹 + 자동 시작 설정)
- **transcribing**: `cmds-transcribe` 실행 중 (백그라운드)
- **relabel-pending**: 화자 매핑 제안이 준비됨 — 앱이 speaker_n → 실명 후보를 보여주고 사용자가 승인/수정 (유일한 필수 개입)
- **integrating**: relabel 적용 → `cmds-integrate` (교차분석 최종본)
- **vault-ready**: 최종본 + 메타데이터 완성 — 볼트 노트 미리보기
- **vault-sent**: `00. Inbox/08. Transcripts/08-1. Plaud/`에 착륙, `usage_status → vault-linked`

CLI: `plaud dual <file-id> [--auto-approve] [--to-vault]` — 상태를 읽어 다음 단계부터 재개 (멱등 재실행 안전). 앱: 상세 헤더 "⚡ Dual Transcribe" 버튼 + 상태 배지.

## 3. 화자 실명 자동 제안 (relabel-pending 단계의 핵심)

입력 3종을 교차해 speaker_n → 실명 후보를 만든다:

1. **Plaud 전사본** — Plaud 쪽 화자 라벨(사용자가 웹에서 고친 `speaker ≠ original_speaker` 이력 포함)
2. **ElevenLabs 세그먼트** — 발화 내용 (자기소개·호명 패턴: "저는 ~입니다", "~님이")
3. **볼트 Transcription Context** — `50. Assets/53. Transcription Context/transcription-context-compact.md`
   - 실명 로스터 (구요한·홍길동·…) + **흔한 STT 오류 매핑** ("홍길둥"→홍길동, "준건"→(실명))
   - 코드블록 단위(~190tok)라 관련 블록만 선택 주입 — 파일 폴더/태그로 블록 선택 (예: LG 폴더면 §2 주입)

LLM(metadata_model 재사용)이 `{speaker_0: {name, confidence, evidence}}` JSON 제안 → 앱 확인 시트에서 수정/승인 → `cmds-relabel` 적용. 승인된 매핑은 saved speakers 테이블에 축적되어 다음 제안 정확도 상승. 옵션: `plaud plaud-relabel`로 Plaud 서버에도 반영 (기본 off — 로컬 우선).

교차분석(`cmds-integrate`) 프롬프트에도 같은 Context 블록 + 확정 실명을 주입 —
"홍길둥"류 오기가 최종본에서 실명으로 교정된다.

## 4. 볼트 착륙 — 기존 규약 그대로 (인풋 방식 캐치 결과)

볼트에 이미 문서화된 규약을 따른다 (`Transcripts Inbox Guide` / `Archive Guide`):

- **착륙 지점**: `00. Inbox/08. Transcripts/08-1. Plaud/` (Plaud 유입 레인 — 파이프라인 자동 착륙지)
- **파일명**: `YYYYMMDD_제목.transcript.md` (타입 접미사 컨벤션)
- **본문 구조** (기존 아카이브 샘플 형식 준수):
  ```markdown
  ---  (CMDS 7-field frontmatter + plaud_id · plaud_url · dual: true · speakers 실명 · session-link)
  # MM-DD 제목
  > 날짜/장소/참석자(실명)
  ## 개요          ← integrate 요약
  ## 주요 논의     ← integrate 요약 상세
  ## 최종 전사본   ← 교차분석 FINAL_TRANSCRIPT (실명 라벨)
  ```
- 이후 처리(회의록화 → `63. Meetings`, 아카이브 이동)는 **볼트 쪽 CMDS Process 관할** — 앱은 인박스 레인에 내려놓는 것까지만. "인박스 비어있음 = 건강" 원칙 존중.

## 5. Reuse 메타데이터 — 후속 작업 체크 체계

`note_metadata.metadata_json["reuse"]` + 전용 컬럼 최소화(쿼리용 `reuse_state`만):

```jsonc
"reuse": {
  "state": "none | flagged | drafted | published",   // 최고 진행 단계 (리스트 배지용)
  "targets": [                                        // 채널별 체크
    {"channel": "newsletter", "status": "flagged",   "note": "게임 지식확장 사례로"},
    {"channel": "lecture",    "status": "drafted",   "note": "창발 벨뷰 슬라이드 12"}
  ],
  "quotes": [ {"ts": "12:41", "text": "…"} ]          // 인용 후보 (선택)
}
```

- **채널 어휘** (thebetter/강의/쇼츠 등 실제 출력 채널과 1:1): `newsletter · lecture · shorts · sns · consulting · essay · research · other`
- CLI: `plaud reuse <id> newsletter --note "…"` / `plaud reuse <id> --list` / `plaud reuse-query newsletter --status flagged` (콘텐츠 만들 때 "이번 뉴스레터 재료 뭐 있지" 한 방 조회)
- 앱: 상세에 채널 체크칩 (클릭=flagged, 재클릭=상태 순환), 사이드바 "Reuse" 스마트 필터
- 볼트 노트 frontmatter에도 `reuse-channels` 반영 → Obsidian 쪽 Bases/검색과 연동
- 기존 `usage_status`는 유지 (수명주기), reuse는 직교하는 활용 축

## 6. 스테이지 분할 (지시 단위)

| # | 스테이지 | 내용 | 규모 |
|---|---|---|---|
| S1 | Reuse 메타데이터 | 스키마 + CLI(reuse/reuse-query) + 앱 체크칩/필터 + 볼트 frontmatter 연동 | 반나절 |
| S2 | 파이프라인 코어 | `core/dual_pipeline.py` 상태 머신 + `plaud dual` (transcribe→integrate 자동 연결, relabel은 CLI 매핑 인자로) | 1일 |
| S3 | 화자 실명 제안 | Context 블록 선택 주입 + LLM 제안 + saved speakers 학습 + CLI `plaud dual-speakers` | 1일 |
| S4 | 볼트 착륙 | transcript.md 빌더 (규약 §4) + `plaud dual --to-vault` + frontmatter session-link | 반나절 |
| S5 | 앱 UI | Dual Transcribe 버튼/상태 배지, 실명 확인 시트, vault 미리보기 | 1일 |

S1은 독립적이라 먼저 해도 됨. S2→S3→S4→S5는 순서 의존.

## 7. 열린 결정 (스테이지 시작 전 확인)

1. **중요 마킹** — 기존 ⭐ Starred를 트리거로 쓸지, 별도 "Dual" 마크를 둘지. (권장: 별도 — Starred는 이미 다른 의미로 사용 중)
2. **마킹 시 자동 시작** — 마킹만으로 transcribing까지 자동 진행할지, 항상 버튼 클릭으로 시작할지. (권장: 버튼 시작, 설정으로 자동화 옵트인)
3. **Plaud 서버 화자 반영** — 확정 실명을 서버(`speaker/sync`)에도 쓸지. (권장: 기본 off)
4. **reuse 채널 어휘** — §5 목록 확정 (사용자 실제 채널 기준 가감)
