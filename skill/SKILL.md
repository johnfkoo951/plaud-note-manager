---
name: plaud-note-manager
description: Manage Plaud Cloud recordings — sync metadata, download audio, track processing status. Calls into the local plaud-note-manager Python core.
---

# Plaud Note Manager Skill

이 스킬은 `$PLAUD_HOME` (plaud-note-manager 저장소 루트)의 Python core/CLI를 호출합니다.
기존 `plaud-cloud-tools`와 달리 **로컬 SQLite 메타데이터 저장소**를 함께 관리하므로,
"새로 들어온 파일만", "아직 처리 안 된 파일만" 같은 상태 기반 질의가 가능합니다.

## When to Use

- 사용자가 Plaud Cloud의 최신 녹음 목록을 원할 때
- 특정 fileId를 다운로드하라고 할 때
- 처리 진행 상태(다운로드됨/전사됨/완료) 를 묻거나 업데이트할 때
- 새 파일을 자동으로 동기화하고 싶을 때

## Quick Commands

```bash
cd "$PLAUD_HOME"

uv run plaud sync                   # Plaud Cloud -> 로컬 DB 동기화
uv run plaud list --limit 20        # 최근 20개 조회
uv run plaud contents <fileId>      # 전사/요약/outline 캐시 + md 저장
uv run plaud cmds-transcribe <id>   # ElevenLabs Scribe 전사
uv run plaud cmds-integrate <id>    # Plaud + CMDS 통합 요약/전사 생성
uv run plaud models                 # 메인 볼트 API Information 기반 SOTA 모델 목록
uv run plaud folder-plan            # Plaud 녹음 폴더 taxonomy 확인
uv run plaud classify --apply       # 미분류 녹음 자동 폴더 분류/이동
uv run plaud metadata-generate <id> # stable id 기준 로컬 metadata + auto tags
uv run plaud tag-add <id> TAG       # Obsidian-style local tag 수동 추가
uv run plaud meeting-note <id>      # 메인 볼트 회의록 생성/갱신
uv run plaud download <fileId>      # 한 개 다운로드 + 상태 업데이트
uv run plaud status                 # status 컬럼별 파일 수
uv run plaud query --tag 회의록 -n 10  # tag/keyword/folder/vault-note로 L1 brief 검색
uv run plaud brief <id>             # 한 녹음의 L1 brief (제목/폴더/키워드 미리보기)
uv run plaud resources --json       # 로컬 리소스(전사/요약/통합)를 URI+경로 JSON으로
```

점진적 공개(cheap-to-deep) 읽기 경로: `peek` (L0 메타) → `brief` (L1 요약) →
`outline-of` (L2 Plaud auto-summary/outline 미리보기) → `deep` (L3 전체 내용) 순으로
필요한 만큼만 토큰을 쓰며 내려간다. 검색은 `query`로 L1 brief을 먼저 받고,
임베딩/증분 동기화는 `resources --since <unix-mtime>`로 변경분만 추린다.

## Credential Setup

기존 plaud-cloud-tools와 동일한 cURL 복사 방식. `.env`를 프로젝트 루트에 두고,
`pbpaste | uv run plaud onboard`를 실행하거나 `.env.example`을 참고해 직접 작성.

## Model Presets

AI Summary / Integrated 요약 모델을 바꿀 때는 먼저
`<your-obsidian-vault>/40. Docs/49. API Information`
을 확인한다 (볼트 경로는 `PLAUD_OBSIDIAN_VAULT`로 설정). 앱과 CLI는 이 폴더의
모델 frontmatter에서 Claude/Codex/Gemini/Grok SOTA preset을 읽고, 필요하면
`--model-id`로 직접 override한다.

## Recording Taxonomy

녹음 폴더 분류는 `core/classification.py`가 기준이다. `metadata-generate`는
metadata/tags 생성과 함께 `folder_name`, `category`, `usage_status`를 저장하고
Plaud 파일을 해당 폴더로 이동한다. `usage_status`는 `unused`,
`metadata-ready`, `vault-linked`, `used-elsewhere`, `archived` 중 하나다.
