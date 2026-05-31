# Progressive Disclosure + Vault Integration

Plaud 노트를 **점진적으로 깊이 펼치는** 쿼리 API와, 옵시디언 볼트와 자동 연결되는
keyword/wikilink 인덱스를 한 묶음으로 정리.

---

## 1. 왜

- Plaud는 평면 파일 모델 — keyword 텍스트만 있고 wikilink/cross-reference 개념 없음.
- 우리는 13K+ 노트가 있는 CMDS 볼트들과 Plaud 녹음을 한 그래프로 묶고 싶음.
- 단순히 모든 데이터를 한 번에 던지면 컨텍스트 폭발 → **layer별 점진적 조회**가
  꼭 필요.

---

## 2. 데이터 모델

```
files                     1,076 rows  Plaud 파일 메타
file_content              1,006 rows  Plaud 측 transcript/summary 캐시
cmds_transcripts              7 rows  ElevenLabs Scribe 결과
note_metadata / note_tags        ?    수동/자동 태그
keywords                  ~수천      정규화된 keyword 어휘
file_keywords             ~수만      file_id ↔ keyword
vault_notes              13,058      모든 옵시디언 노트 (7개 vault)
vault_links              18,721      file_id ↔ vault_note (자동 매칭)
```

### vault_notes
- 설정된 여러 Obsidian vault에서 인덱싱 (볼트 목록은 사용자 환경에 따라 다름)
- frontmatter 파싱: aliases, tags, type, description
- mtime 기반 증분 — 안 바뀐 파일은 skip

### vault_links (자동 매칭 우선순위)
| match_kind | 의미 | confidence |
|---|---|---|
| `title`  | keyword === vault note 파일명 (대소문자 무시) | 1.0 |
| `alias`  | keyword가 vault note의 aliases 배열에 있음 | 0.9 |
| `tag`    | keyword가 vault note의 tags 배열에 있음 | 0.7 |

현재 통계: title 725 / alias 591 / tag 17,405. **Plaud 파일 277개가 vault 노트와
연결됨.**

---

## 3. Progressive Disclosure 4 Layer

| Layer | 명령 | 비용 | 무엇이 들어있나 |
|---|---|---|---|
| **L0 peek** | `plaud peek <id>` | ≈0 | filename, duration, folders, cache 표시 |
| **L1 brief** | `plaud brief <id>` | 1 query | + title, keywords, tags, vault links 수, speakers |
| **L2 outline** | `plaud outline-of <id>` | 디스크 1회 | + Plaud 자동 요약/outline 프리뷰, integrated summary 프리뷰 (400~600 chars) |
| **L3 deep** | `plaud deep <id>` | 디스크 다회 | + 전체 transcript/summary/integrated body + vault link 상세 |

각 layer는 strictly superset. agent가 L0/L1에서 promising한 것 보면 L2로 진행, 진짜
필요하면 L3.

```bash
# 단계별
plaud peek 42b0f9230ac0...                         # 메타 한 줄
plaud brief 42b0f9230ac0...                        # + keyword/vault 카운트
plaud outline-of 42b0f9230ac0...                   # + summary preview
plaud deep 42b0f9230ac0... --section integrated-summary
plaud deep 42b0f9230ac0... --section vault         # 연결된 vault note 목록

# JSON
plaud brief 42b0f9230ac0... --json | jq .
```

---

## 4. Vault 인덱싱

```bash
# 첫 인덱싱 (모든 vault 풀스캔)
plaud vault-index --full

# 일상 사용: 변경된 노트만
plaud vault-index

# 단일 vault만
plaud vault-index --vault <your-obsidian-vault>

# Plaud keywords → 우리 keywords/file_keywords 동기화 + vault_links 자동 매칭
plaud vault-link
```

권장: 옵시디언에서 큰 작업한 날 `plaud vault-index && plaud vault-link` 한 번.

---

## 5. 검색

```bash
# keyword 한 단어
plaud query --keyword "옵시디언" -n 10

# 태그
plaud query --tag "회의록"

# 폴더
plaud query --folder "10. Meetings"

# 특정 vault 노트와 연결된 Plaud 파일들
plaud query --vault-note "Claude Code"

# 결합 (AND)
plaud query --folder "10. Meetings" --keyword "LLM"

# JSON pipe to embedder / RAG
plaud query --keyword "PKM" --json | jq '.[].file_id'
```

---

## 6. 컨텍스트 자동 구성 패턴

agent가 "어제 했던 PKM 회의 알려줘"라고 받으면:

```bash
# 1) 검색
plaud query --keyword "PKM" --json -n 5 \
  | jq -r '.[].file_id' > candidates.txt

# 2) 각 후보 L2 미리보기로 promising 한 것 선택
for fid in $(cat candidates.txt); do
  plaud outline-of $fid --json
done

# 3) 최종 선택된 1~2개만 L3로 전체 컨텍스트 로드
plaud deep <selected_id> --section integrated-summary
plaud deep <selected_id> --section vault    # 연결된 vault 노트 따라가기

# 4) vault 노트도 컨텍스트에 합치고 싶으면 cat
cat "<your-obsidian-vault>/.../Claude Code.md"
```

---

## 7. 한계 / 추후

- **fuzzy matching 미구현**: "Claude Code" vs "claude-code" 같은 변형은 아직 못
  연결. tokenizer + Levenshtein 추가 여지.
- **양방향 매칭**: 지금은 Plaud keyword → vault note. 반대 방향 (vault note에서
  관련 Plaud 녹음 찾기)도 SQL JOIN 한 번으로 가능 — UI/CLI에서 노출 추가 가능.
- **automatic re-link**: 통합 요약(integrated) 본문에 등장한 [[wikilink]]를
  파싱해서 vault_links에 추가하면 keyword 한도를 넘는 깊은 매칭 가능.
- **tag namespace**: 현재 tag match (17K) 가 너무 많음. CMDS-meta tag(`CMDS`,
  `inProgress`)는 의미 부족. blacklist 추가 권장.
