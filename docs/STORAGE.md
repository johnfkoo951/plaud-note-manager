# 로컬 저장소 가이드

Plaud Note Manager는 **모든 노트 / 전사 / 요약**을 로컬 디스크에 사람이 읽을 수
있는 markdown / json 파일로 저장합니다. 외부 임베딩 / RAG / Obsidian 동기화
파이프라인이 우리 내부 구조를 몰라도 인덱싱할 수 있도록, 안정적인 URI 스킴과
manifest를 함께 제공합니다.

---

## 1. 디렉토리 레이아웃

```
~/DEV/plaud-note-manager/
├── data/
│   ├── plaud.db                              SQLite (WAL) — 메타데이터/캐시
│   ├── plaud.db-wal / plaud.db-shm           WAL 부속 파일
│   ├── config.json                           backends · model ids · 경로 override
│   ├── slots.json                            요약 슬롯 (name + model + template)
│   ├── manifest.json                         (생성됨) 임베딩 파이프라인용 인덱스
│   ├── transcripts/{file_id}/
│   │   ├── plaud.transcript.md               Plaud 자체 전사
│   │   ├── plaud.summary.md                  Plaud 자체 요약
│   │   ├── plaud.outline.md                  Plaud 자체 outline
│   │   ├── cmds.transcript.md                ElevenLabs Scribe 전사 (markdown)
│   │   └── cmds.transcript.json              원본 segments (start_ms/speaker)
│   ├── summaries/{file_id}/
│   │   └── {model}__{template}.md            단일 소스 AI 요약
│   └── integrated/{file_id}/
│       ├── {model}__{template}.md            통합 raw output
│       ├── {model}__{template}.summary.md    통합 최종 요약
│       └── {model}__{template}.transcript.md 통합 최종 transcript (정제됨)
└── templates/                                프롬프트 템플릿 (default/meeting/lecture/integrated/...)
```

- `{file_id}` = Plaud immutable id (예: `d13947adcbd48befa56a5b7decb37f4a`).
  파일명/제목이 바뀌어도 file_id는 영구 — 임베딩 키로 안정적.
- 경로 override는 `data/config.json`의 `paths.{transcripts,summaries,integrated}`로
  바꿀 수 있고, `core.paths`가 항상 그 override를 honor합니다.

---

## 2. URI 스킴

모든 로컬 리소스는 다음 형식의 안정적인 URI를 가집니다:

```
plaud://{kind}/{file_id}[/{model}/{template}]
```

| kind                  | 추가 경로            | 파일                                |
|-----------------------|---------------------|-------------------------------------|
| `plaud-transcript`    | —                   | `transcripts/{id}/plaud.transcript.md` |
| `plaud-summary`       | —                   | `transcripts/{id}/plaud.summary.md`    |
| `plaud-outline`       | —                   | `transcripts/{id}/plaud.outline.md`    |
| `cmds-transcript`     | —                   | `transcripts/{id}/cmds.transcript.md`  |
| `summary`             | `/{model}/{template}` | `summaries/{id}/{model}__{template}.md` |
| `integrated-all`      | `/{model}/{template}` | `integrated/{id}/{model}__{template}.md` |
| `integrated-summary`  | `/{model}/{template}` | `integrated/{id}/{model}__{template}.summary.md` |
| `integrated-transcript` | `/{model}/{template}` | `integrated/{id}/{model}__{template}.transcript.md` |

예시:
```
plaud://cmds-transcript/d13947adcbd48befa56a5b7decb37f4a
plaud://integrated-summary/3a84393f6e4425959bc5e08794087e64/claude/default
plaud://summary/3a84393f6e4425959bc5e08794087e64/codex/meeting
```

URI는 `core.locator.make_uri / parse_uri`로 빌드/파싱.

---

## 3. CLI

```bash
# 전체 리소스 테이블로 보기
plaud resources

# 한 파일의 모든 산출물
plaud resources <file_id>

# 증분 임베딩 (특정 mtime 이후만)
plaud resources --since 1714000000

# 외부 파이프라인용 JSON (rich color 없이 pure stdout)
plaud resources --json > resources.json
plaud resources --json | jq '.[] | select(.kind == "integrated-summary")'

# 한 번에 전체 manifest 작성 (metadata 포함)
plaud resources --manifest          # → data/manifest.json

# URI 또는 절대경로로 내용 출력
plaud show "plaud://integrated-summary/3a84393f.../claude/default"
plaud show /absolute/path/to/file.md
```

---

## 4. Python API

```python
from core.locator import (
    iter_resources,         # 전체 walker
    resources_for,          # 한 file_id의 모든 산출물
    file_metadata,          # 임베딩 메타데이터 (제목/키워드/폴더/태그)
    build_manifest,         # JSON 인덱스
    parse_uri,
    make_uri,
)

# 모든 리소스를 임베딩
for r in iter_resources():
    text = r.read_text()
    meta = {
        "uri": r.uri,
        "file_id": r.file_id,
        "kind": r.kind,
        "mtime": r.mtime,
        **file_metadata(r.file_id),   # title, keywords, folders, tags
    }
    embed(text, metadata=meta)

# 증분 업데이트
import time
last_run = load_last_embed_timestamp()
for r in iter_resources(since_mtime=last_run):
    reembed(r)
save_last_embed_timestamp(time.time())
```

---

## 5. manifest.json 구조

```json
{
  "data_dir": "~/DEV/plaud-note-manager/data",
  "uri_prefix": "plaud://",
  "count": 247,
  "items": [
    {
      "uri": "plaud://integrated-summary/3a84393f.../claude/default",
      "path": "~/DEV/plaud-note-manager/data/integrated/3a84393f.../claude__default.summary.md",
      "kind": "integrated-summary",
      "file_id": "3a84393f6e4425959bc5e08794087e64",
      "model": "claude",
      "template": "default",
      "mtime": 1714123456.789,
      "size": 8842,
      "metadata": {
        "file_id": "3a84393f...",
        "filename": "04-29 옵시디언 코호트...",
        "title": "...",
        "keywords": ["옵시디언", "PKM", ...],
        "folders": ["10. Meetings"],
        "tags": ["회의록", "옵시디언"],
        "duration": 552080.0,
        "start_time": 1777452858000,
        "edit_time": 1777463716,
        "is_trash": 0
      }
    },
    ...
  ]
}
```

각 항목이 임베딩 chunk의 단위가 되며, `metadata`는 vector store의 metadata
필드에 그대로 흘려보내기 좋게 평탄화되어 있습니다.

---

## 6. 임베딩 권장 패턴

### 6.1 chunk-by-resource
1 리소스 = 1 chunk. transcript처럼 긴 파일은 자체적으로 추가 분할 필요.
```python
for r in iter_resources():
    if r.size > 50_000:
        for chunk in split_by_paragraph(r.read_text()):
            embed(chunk, metadata={"uri": r.uri, ...})
    else:
        embed(r.read_text(), metadata={"uri": r.uri, ...})
```

### 6.2 우선순위
- `integrated-summary` (Claude/Codex 등이 정제한 통합 요약) — RAG 검색 우선
- `plaud-summary` + `summary` — 두 번째 우선
- `cmds-transcript` / `plaud-transcript` — 인용/quote 추출용
- `plaud-outline` — TOC / 토픽 탐색용

### 6.3 file_id가 동일한 리소스를 그룹화
같은 녹음에서 나온 chunk들은 검색 결과에서 한 묶음으로 묶어 보여주면 좋음.
`file_id`가 그 group key.

### 6.4 증분 임베딩
`plaud resources --since <last_run_mtime> --json`로 변경된 것만 다시 인덱싱.
`mtime`은 파일 시스템 mtime이라 신뢰 가능.

---

## 7. 추후 확장 여지

- `transcripts/{id}/cmds.transcript.json`을 segment 단위로 끊어 각 발화에
  speaker + 타임스탬프 metadata 붙여 embedding하면, "X가 무슨 말 했어?" 같은
  speaker-aware 검색이 가능. (현재 manifest에는 markdown만 포함.)
- `data/manifest.json`을 cron으로 매시간 재생성하면 외부 vector store가 polling만
  하면 자동 동기화.
- Obsidian vault 안에 wikilink `[[plaud-note-{file_id}]]` 형태로 심볼릭하게 박아두고,
  vault 내부에서 검색해도 곧장 이 디렉토리의 파일로 점프 가능.
