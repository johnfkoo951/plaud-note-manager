# Plaud Note Manager — 기술 사양서 (SPEC)

마지막 업데이트: 2026-06-15 · 버전 v0.5.x

Plaud Cloud 녹음을 관리·가공하는 로컬 우선(local-first) 워크스페이스. 하나의
Python core를 CLI · skill · agent · SwiftUI macOS 앱이 공유한다.

---

## 1. 저장소(Storage) — 예, 단일 SQLite다

| 항목 | 값 |
|---|---|
| 엔진 | **SQLite 3** (단일 파일 `data/plaud.db`) |
| 저널 모드 | **WAL** (`journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`) |
| Python 접근 | `core/storage.py` — 표준 `sqlite3` (row_factory=Row) |
| Swift 접근 | `app/.../Database.swift` — **GRDB** read/write pool |
| 동시 접근 | WAL 덕에 Python writer와 Swift reader/writer가 서로 락 없이 공존 |
| 스키마 버전 | `PRAGMA user_version` (`SCHEMA_VERSION`, 현재 2) — 증분 마이그레이션 게이트 |
| 전문 검색 | **FTS5 (trigram)** 가상 테이블 `recording_fts` (한국어 부분 일치) |
| 디스크 부산물 | `plaud.db-wal`, `plaud.db-shm` (gitignored) |

> 디자인 원칙: **AI 결과물(전사/요약/통합)은 DB blob이 아니라 markdown 파일로**
> `data/{transcripts,summaries,integrated}/{file_id}/`에 저장한다. CLI·Obsidian·
> git에서 직접 grep/diff 가능. SQLite는 메타데이터 + 캐시 + 인덱스 역할.

WAL 모드에서 Swift(GRDB)와 Python(sqlite3)이 **같은 파일을 양방향으로** 읽고 쓴다.
앱은 낙관적(optimistic) 쓰기 — 사용자 액션 → 즉시 SQLite 반영 + 리로드 →
백그라운드로 Python CLI가 Plaud 서버에 동기화.

---

## 2. 스키마 — 테이블

### 2.1 코어 (core/storage.py)

**files** — Plaud 파일 메타데이터(클라우드 미러 + 로컬 전용 상태)
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | Plaud immutable file_id |
| filename | TEXT | 표시 이름 |
| filesize | INTEGER | 바이트 |
| duration | REAL | **밀리초** |
| edit_time | INTEGER | 수정 시각(초) |
| start_time | INTEGER | 녹음 시작(밀리초) |
| is_trash | INTEGER | 휴지통 여부 |
| status | TEXT | 레거시(대부분 'new' — 진행률은 파생, §5) |
| local_path | TEXT | 다운로드된 오디오 경로 |
| **seen_at** | INTEGER | 로컬 전용 — 처음 연 시각(NULL=안 읽음). sync에도 보존 |
| **starred** | INTEGER | 로컬 전용 — 즐겨찾기 |
| synced_at / updated_at | INTEGER | 동기화/갱신 시각 |

**folders** — Plaud 폴더(filetag) `(id PK, name, icon, color, synced_at)`
**file_folders** — 파일↔폴더 매핑 `(file_id, folder_id)` PK 복합.
  ⚠️ Plaud 웹은 **파일당 폴더 1개만** 지원(복수 시 웹 UI 깨짐) — client에서 강제.

**file_content** — Plaud 상세 캐시(S3 dereference 결과)
| 컬럼 | 설명 |
|---|---|
| file_id PK | |
| title | aiContentHeader.headline |
| transcript | JSON 세그먼트 배열 |
| outline | JSON |
| summary_md | auto_sum_note 본문(Plaud AI 노트) |
| summary_extra | sum_multi_note 등 JSON |
| keywords | JSON |
| fetched_at | |

  ⚠️ **빈 결과는 저장하지 않음**(`FileContent.is_empty`) — Plaud 처리 완료 전
  캐시되어 '내용 없음'으로 굳는 버그 방지(v0.5.1).

**cmds_transcripts** — CMDS 자체 전사(ElevenLabs Scribe) `(file_id, model, language, text, segments, fetched_at)` PK(file_id,model).
**speakers** — 저장된 화자 `(id PK, name UNIQUE, is_self, notes, created_at)`.

**note_metadata** — file_id 기준 로컬 메타데이터 SSOT
| 컬럼 | 설명 |
|---|---|
| file_id PK | |
| title, description, note_type | |
| status | unread/inProgress/completed/… |
| usage_status | unused / metadata-ready / vault-linked / used-elsewhere / archived (위계 컬러) |
| category, folder_id, folder_name | 분류 결과 |
| vault_path, draft_path, final_note_path | Obsidian 연동 경로 |
| metadata_json | 원본 분류 페이로드 |
| generated_at, updated_at | |

**note_tags** — Obsidian식 태그 `(file_id, tag, source, created_at)` PK(file_id,tag). source: manual / ai / auto.
**note_references** — 외부 참조 `(file_id, path, kind, title, updated_at)`.

### 2.2 전문 검색 (FTS5)

**recording_fts** — `USING fts5(file_id UNINDEXED, title, body, tokenize='trigram')`
  - body = 전사본 텍스트 + Plaud 요약. `save_content`가 자동 동기화.
  - **trigram** 토크나이저로 한국어 3글자+ 부분 일치. 2글자 이하는 LIKE 폴백.
  - 섀도 테이블: `recording_fts_{config,content,data,docsize,idx}` (FTS5 내부).

### 2.3 Obsidian 볼트 인덱스 (core/vault_index.py — 같은 plaud.db)

**vault_notes** — 볼트 `.md` 인덱스(증분 mtime 스캔) `(id PK, vault, path UNIQUE, rel_path, title, aliases, tags, description, type, mtime, indexed_at)`. (현재 ~13,000 노트)
**keywords** — 정규화된 키워드 사전 `(id PK, term UNIQUE)`.
**file_keywords** — 파일↔키워드 `(file_id, keyword_id, source)`.
**vault_links** — 녹음↔볼트 노트 해소 결과 `(file_id, vault_note_id, match_kind, keyword, confidence, created_at)`.

### 2.4 인덱스

```
files_status_idx, files_edit_time_idx(DESC), files_trash_idx,
note_tags_tag_idx, note_refs_file_idx, file_folders_folder_idx,
vault_notes_title_idx, vault_notes_vault_idx, file_keywords_kid_idx,
vault_links_file_idx, vault_links_vault_idx
```

---

## 3. 아키텍처 / 데이터 흐름

```
Plaud Cloud (api-apne1.plaud.ai, JWT in .env)
      │  core/client.py (httpx)
      ▼
   Python core ───────────────►  SQLite  data/plaud.db (WAL)
   (CLI · skill · agent)              ▲ │
      │                              양방향 │
      ▼                                 │ ▼
   markdown 파일                   Swift 앱 (GRDB)
   transcripts/ summaries/ integrated/
```

- **인증**: `.env`의 cURL 헤더(JWT bearer + x-device-id + x-pld-user [+ cookie]).
  앱 Auth 시트(browser import 우선·embedded WKWebView fallback) 또는
  `plaud refresh-auth`(클립보드 cURL) / `plaud web-auth`(앱 브리지). `.env`는 0600.
- **모델 백엔드**: claude/codex/gemini/grok × cli(구독 OAuth)/api(키). grok은 Grok
  Build CLI로 SuperGrok 구독 사용(API 비용 0). 자동분류 모델은 config로 선택.
- **자동분류**: `core/classification.py` taxonomy(키워드 결정적). 앱은 미리보기
  시트 → 선택 적용(`classify --apply --only`) → 매니페스트 기반 undo.

---

## 4. 파일 레이아웃

```
plaud-note-manager/
├── core/        공유 Python (client, storage, summarize, classification,
│                metadata, transcribe, progress, web_auth, vault_index …)
├── cli/main.py  typer CLI (~75 명령)
├── app/         SwiftUI macOS 14+ (SwiftPM + GRDB)
├── templates/   프롬프트 템플릿 (.md, frontmatter + placeholder)
├── data/        plaud.db(WAL) · config.json · slots.json ·
│                transcripts/ summaries/ integrated/{file_id}/*.md   (gitignored)
├── docs/        SPEC.md(이 문서) · STORAGE.md · PLAUD-ACCESS-LAYERS.md …
└── tests/       pytest (188+)
```

---

## 5. 파생 진행률 (status가 아니라 아티팩트 기반)

`files.status`는 대부분 'new'라 무의미 → `core/progress.py`가 실존 아티팩트에서 파생:

```
new (메타만) → cached (file_content 존재) → transcribed (cmds_transcripts 존재)
            → integrated (data/integrated/{id}/*.md 존재)
```

앱 리스트의 **왼쪽 점**은 단계+리뷰 상태를 색으로 표현:
🔴 데이터 미수신(전송만) · 🟢 전사 완료·안읽음 · ⚪ 읽음 · ⭐ 즐겨찾기.

---

## 6. 마이그레이션

`Storage.__init__` → `executescript(SCHEMA_TABLES)` → `_migrate(conn)` →
`executescript(SCHEMA_INDEXES)` → `_ensure_search_index(conn)`.
`_migrate`는 `PRAGMA user_version < SCHEMA_VERSION`일 때만 `ALTER TABLE`(멱등).
모든 CREATE는 `IF NOT EXISTS`라 신규 클론도 동일 경로로 생성.

---

## 7. 품질 게이트

```bash
uv run pytest                              # 188+ tests
uv run ruff check core cli tests
uv run ruff format --check core cli tests
swift build --package-path app
scripts/package-macos-app.sh               # → /Applications, 버전=pyproject + build=git commit수
```

---

## 8. DB 들여다보기 (탐색 방법)

`data/plaud.db`를 직접 열어 스키마·데이터를 확인하는 세 가지 방법.
앱이 켜져 있어도 **읽기는 안전**(WAL 동시 읽기). 안전하게 하려면 read-only로 연다.

### 8.1 `sqlite3` — macOS 기본 내장(설치 불필요), 가장 빠름

```bash
cd ~/DEV/plaud-note-manager
sqlite3 "file:data/plaud.db?mode=ro"      # read-only로 안전하게
```
자주 쓰는 명령:
```sql
.tables                 -- 테이블 목록
.schema files           -- 특정 테이블 구축문(CREATE) — ALTER 이력까지 보임
.schema                 -- 전체 스키마 한 번에
.mode column
.headers on
SELECT filename, duration, starred FROM files LIMIT 10;
SELECT tag, COUNT(*) n FROM note_tags GROUP BY tag ORDER BY n DESC LIMIT 10;
PRAGMA journal_mode;    -- wal
PRAGMA user_version;    -- 스키마 버전 (SCHEMA_VERSION)
.quit
```
전체 스키마를 파일로 덤프:
```bash
sqlite3 data/plaud.db .schema > /tmp/plaud-schema.sql
```

### 8.2 GUI로 클릭하며 보기

- **DB Browser for SQLite** (무료): `brew install --cask db-browser-for-sqlite`
  → 앱에서 `data/plaud.db`를 **Open Database Read-Only**로 열면 앱과 충돌 없음.
- **TablePlus** / **DBeaver**도 동일하게 열린다.

### 8.3 Datasette — 브라우저 웹 UI(검색·필터·패싯·JSON), 탐색에 가장 좋음

```bash
uv tool install datasette
datasette ~/DEV/plaud-note-manager/data/plaud.db --open
# → http://localhost:8001 에 모든 테이블이 클릭 가능한 웹 UI로 뜸
```

> 참고: AI 결과물 본문(전사/요약/통합)은 DB가 아니라 `data/{transcripts,
> summaries,integrated}/{file_id}/*.md` 파일에 있다(§1). DB에는 캐시/메타/인덱스만.
