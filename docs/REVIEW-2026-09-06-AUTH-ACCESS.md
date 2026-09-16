# Plaud 인증·접근 경로 재검토 — 2026-09-06

이 앱의 읽기 자동화는 공식 OAuth 경로를 추가하는 편이 낫다. cURL은 브라우저 요청과 인증 헤더를 전달하는 입력 형식이다. 그 자체가 오래 유지되는 인증이나 별도의 API는 아니다. 현재 앱의 비공개 Web API는 폴더·제목·서버 화자 편집에 계속 필요하지만, 녹음 읽기까지 이 인증 하나에 묶을 이유는 없다.

이번 검토는 현재 공식 문서, npm 배포물, 설치된 패키지의 정적 검사와 저장소 코드를 대조했다. 새 어댑터를 구현하고 단위 검증했다. 계정 상태의 실측은 아래에서 별도 표시하며, 문서에 적힌 기능·패키지 구현·실제 계정 호출 성공을 구분한다.

## 1. 이번 세션에서 확인한 상태

| 대상 | 확인 결과 | 의미와 한계 |
|---|---|---|
| 이 앱의 Web 인증 | 메인 작업의 9월 6일 재검증에서 live 요청 성공, access 잔여 약 23시간 35분, `auto_refresh=not_bootstrapped`, `refresh_expires_at=null` | 지금 요청 가능한 상태와 장기 자동 갱신 가능 상태는 다르다. 이 수치는 해당 시점의 잔여시간이다. |
| 공식 CLI | 설치된 `@plaud-ai/cli` **0.3.11**, 전용 Node **v22.22.1** 경로 확인 | 설치 코드와 npm 배포 코드의 SHA256가 같았다. |
| CLI 토큰·실제 인증 | 초기 제한된 CLI 실행은 `needs_login`; 최종 설치 앱의 연결 확인은 **valid**, 동일 녹음 요약·전사 preview 모두 성공 | 토큰 값은 직접 읽지 않았다. 초기 환경의 실패를 현재 앱의 미인증 상태로 일반화하지 않는다. |
| 공식 MCP | 현재 세션에 7개 도구가 이미 연결됨. 메인 작업이 `get_current_user` 성공을 확인 | 새 플러그인 설치가 필요하지 않았다. 계정 신원이나 토큰을 보고서에 보존하지 않았다. |
| npm MCP 패키지 | 설치 캐시에서 `@plaud-ai/mcp` **0.3.10** 확인 | 캐시 버전과 현재 연결된 서버의 실제 실행 버전을 동일시하지 않는다. |

공식 CLI 설치 경로는 `~/.nvm/versions/node/<version>/lib/node_modules/@plaud-ai/cli`, Node는 같은 prefix의 `bin/node`다. 프로젝트의 `.venv/bin/python -m cli.main`이 실행하는 Typer CLI와 **서로 다른 프로그램**이다. 양쪽 실행 이름이 `plaud`이므로 앱에서 PATH의 `plaud`를 호출하면 안 된다.

## 2. 실제 작업별로 선택할 경로

| 하고 싶은 작업 | 우선 경로 | 구현·운영 조건 |
|---|---|---|
| 선택한 녹음의 전사·정제 전사·Outline 확인 | 공식 CLI의 ID 지정 export 또는 MCP `get_transcript` | GUI 미리보기는 CLI 파일 출력을 읽는다. 구조화된 캐시 수집은 MCP의 마지막 `next_cursor`까지 모은 뒤 완료 표시한다. |
| 여러 요약 탭을 보존해서 읽기 | MCP `get_note`; CLI `summary --json`은 inline 본문이 있는 경우 | CLI JSON의 `data_link`만 있는 탭은 본문 수집 완료로 취급하지 않는다. |
| 주간 회의 정리·이전 강의 찾기 | 로컬 SQLite 날짜·FTS 검색 → 필요한 ID의 공식 본문 읽기 → 로컬 산출물 | 공식 이름 검색만으로 전사·요약 전체를 검색한 것으로 간주하지 않는다. 외부 모델 생성과 게시·송출은 별도 동작이다. |
| 1,489개 라이브러리 전체를 새로 수집 | 필터 없는 MCP `list_files(page, page_size)` 반복 → 로컬 필터 | CLI 사람용 표를 파싱하지 않는다. MCP `query`/날짜 필터를 함께 쓰면 최근 500개 검색 제한이 생긴다. |
| 알려진 ID 여러 개의 전사 파일 정기 추출 | 공식 CLI 어댑터 → 파일별 결과·재시도 기록 | timeout, auth 실패, 본문 없음, 부분 수집을 구분한다. 스케줄러 자체는 이번에 추가하지 않았다. |
| 파일 제목·폴더 생성·이동·서버 화자 수정 | 기존 앱의 Web API client | 공식 CLI/MCP에 해당 recording write 도구가 없다. 현재 적용·Undo·충돌 검사를 유지한다. |
| 태그·핀·읽음·활용 상태·자체 STT·CMDS 요약 | 기존 로컬 core | 공식 서비스가 로컬 메타를 제공하거나 동기화해 주는 것은 아니다. |

공식 문서의 CLI는 녹음 조회·전사·요약·오디오 export를 제공하고, MCP는 인증 2개와 조회 5개 도구를 제공한다. 문서에 없는 추가 옵션과 수집 제한은 다음 절의 배포 코드로 검증했다. [공식 CLI 안내](https://docs.plaud.ai/plaud-mcp-cli/cli), [공식 MCP 안내](https://docs.plaud.ai/plaud-mcp-cli/mcp)

공식 developer 플랫폼의 Embedded·upload·transcription 제품은 별도의 통합 방식이다. 그 API에 쓰기 기능이 있다는 사실을 개인 계정 MCP/CLI가 기존 녹음의 폴더·제목을 수정할 수 있다는 근거로 쓰지 않는다. [Plaud 개발자 플랫폼 개요](https://docs.plaud.ai/overview)

## 3. 문서보다 배포 코드에서 더 확인된 것

| 항목 | 검증한 구현 | 앱에 미치는 영향 |
|---|---|---|
| CLI 0.3.11 전사 | `--block transaction / transaction_polish / outline / mark_memo`, `--polished`, `--highlights` | 기존 접근 문서의 정제 전사·Outline·하이라이트 CLI 불가 표기는 수정이 필요하다. |
| CLI 0.3.11 요약 | `--all`, `--json`, `-o`; JSON은 `note_list` 원형 | 탭 이름·제목을 보존할 수 있다. JSON 경로는 링크 본문을 가져오지 않는다. |
| CLI export 상태 | 전사 없음에도 종료 코드 0, 출력 파일 미생성 가능 | exit 0만 보고 캐시 완료로 표시하면 안 된다. |
| CLI 목록·검색 | `files`/`file`은 JSON 옵션 없음. 이름 표시는 잘림. `search`는 최근 500개 이름, `recent` 최대 300개, `today` 최대 50개 | 표 파싱이나 이 명령들의 결과를 전체 라이브러리로 간주하면 누락된다. |
| MCP 0.3.10 목록 | 필터 사용 시 최대 5페이지×100개. `truncated` 등 반환 | 전체 수집은 필터 없이 페이지 반복 후 로컬 검색한다. |
| MCP 0.3.10 본문 | `get_note`는 링크 본문 해석. 전사는 블록 선택·cursor·limit≤500 지원 | 정규 캐시 ingest에는 CLI 화면 출력을 파싱하는 것보다 적합하다. |

근거는 공식 배포물의 CLI `dist/index.js` 내 `src/commands/*`, MCP `dist/chunk-ZVOHKRNC.js` 내 도구 등록·`resolveNotes`·페이지 처리다. [CLI 0.3.11 배포물](https://registry.npmjs.org/@plaud-ai/cli/-/cli-0.3.11.tgz), [MCP 0.3.10 배포물](https://registry.npmjs.org/@plaud-ai/mcp/-/mcp-0.3.10.tgz)

CLI 요약의 `--all` 텍스트 경로도 주의가 필요하다. `auto_sum_note`가 없으면 다른 탭이 있어도 먼저 반환하며, 링크 해석 오류를 빈 본문으로 바꾸는 코드가 있다. 그래서 이번 어댑터는 이 출력의 안내 문구를 실제 노트로 저장하지 않고, JSON의 미완료 상태를 보존한다.

공식 changelog의 GA는 **2026-05-12**, 공식 소개 블로그의 게시일은 **2026-08-24**다. 둘을 구분하며 changelog의 `v1.0.0`도 배포된 npm 패키지 버전 `0.3.11`과 별도로 기록해야 한다. `docs/PLAUD-ACCESS-LAYERS.md`에는 이 구분과 CLI 지원 범위를 반영하고, §7.2 서버 화자 자동화 불가 문장을 실제 `plaud-relabel` 구현에 맞춰 고쳤다. 공개 랜딩은 배포하지 않았다. [공식 changelog](https://docs.plaud.ai/plaud-mcp-cli/changelog), [공식 소개 글](https://www.plaud.ai/blogs/news/introducing-plaud-mcp-and-cli)

## 4. 인증 수명과 재사용 경계

| 인증 저장소 | 소유자·갱신 | 다른 경로와의 관계 |
|---|---|---|
| 앱 Keychain의 Web workspace token pair | 이 앱의 Web API client | cURL에 access 헤더만 있으면 refresh pair가 생기지 않는다. workspace bootstrap 성공 여부를 따로 표시해야 한다. |
| `~/.plaud/tokens.json` | 공식 CLI OAuth | CLI 자체가 expiry를 확인하고 refresh한다. 이 앱의 토큰을 복사해 넣지 않는다. |
| `~/.plaud/tokens-mcp.json` | 로컬 공식 MCP OAuth | CLI와 저장 파일·기본 OAuth client가 다르다. 한쪽 성공을 다른 쪽 로그인 성공으로 전파하지 않는다. |

공식 배포 구현은 서버의 `expires_in`으로 access 만료시각을 계산하고, 만료 60초 전부터 저장된 refresh token으로 갱신한다. refresh 유효기간의 고정값이나 무기한 유지 보장은 확인되지 않았다. 만료 정보가 없거나 서버가 갑자기 401을 반환하는 상황도 있으므로 성공한 이전 요청이 향후 실행 성공을 보장하지 않는다. 오디오 URL의 문서상 **24시간**은 OAuth token 수명과 다르다. [공식 CLI 설정·오디오 설명](https://docs.plaud.ai/plaud-mcp-cli/cli), [CLI 인증 구현 배포물](https://registry.npmjs.org/@plaud-ai/cli/-/cli-0.3.11.tgz)

공식 두 패키지의 TokenStore는 자체 JSON 파일을 직접 쓰며 Keychain·명시적 0600·atomic rename을 구현하지 않는다. 이는 앱 Keychain 설계를 폐기하거나 공식 토큰을 앱 저장소로 복제할 이유가 아니다. 공식 CLI 호출은 같은 계정의 독립 연결로 취급하고, 다수 동시 프로세스의 token rotation 경합은 향후 직렬화 검증 대상으로 남긴다. 이번 작업은 해당 토큰 파일이나 공식 설정을 수정하지 않았다.

메인 작업이 시도한 `auth-recover --driver chrome-disk --force`의 live 실행은 자동 승인 검토가 거절했다. 기존 Chrome에서 외부 서비스 자격증명을 추출하는 동작에 대한 구체적인 사용자 동의가 없다는 이유였으며, 명령은 실행되지 않았고 우회하지 않았다. 앱의 cURL import 뒤 자동 Chrome 복구를 제거했고, 설명이 있는 "Chrome 연결" 버튼으로 사용자가 직접 선택하도록 했다. 일반 자동 갱신 경로는 앱 전용 WKWebView의 저장된 세션이다. Chrome 보안 설정을 변경하지 않았다.

## 5. 이번에 추가한 읽기 어댑터

`core/official_cli.py`가 설치된 공식 패키지를 찾아 실행하며 `tests/test_official_cli.py`가 동작 경계를 검증한다.

```python
status(live=False, timeout=15) -> dict
read_recording(file_id, kind="summary", block="transaction", timeout=30) -> dict
```

- 기본 진단은 package manifest와 토큰 파일 `stat`만 사용한다. 설치·버전·경로·지원 범위를 반환하고 인증은 `not_checked`로 남긴다.
- 명시적인 `live=True`만 `me`를 실행한다. upstream이 자신의 OAuth를 갱신할 수 있으나 계정 stdout/stderr는 반환하지 않는다. 로그인은 자동 실행하지 않는다.
- package name·manifest entrypoint·Node를 확인한다. NVM 버전은 숫자로 정렬하며 PATH의 `plaud`를 사용하지 않는다. 별도 설치는 절대 경로 `PLAUD_OFFICIAL_CLI_PACKAGE`와 필요 시 `PLAUD_OFFICIAL_NODE`로 선택한다.
- subprocess timeout은 120초 이하로 제한한다. 프로젝트 `.env` 로드와 Web 인증 환경변수 전달을 차단하고, 공식 CLI telemetry는 해당 호출에서 끈다.
- 요약은 탭별 `type/title/tab_name/content/linked`를 반환하며 presigned URL은 제외한다. `available`과 `complete`를 구분한다. 전사는 임시 export 파일 본문만 읽으며 segment JSON으로 변환한 척하지 않는다.
- Cloud 수정·로컬 DB 변경·토큰 복사·로그인을 제공하지 않는다. 동일 file ID의 읽기 preview·진단용이다.

로컬 Typer CLI의 새 진입점은 메인 작업이 `official-status [--json --live]`, `official-read FILE_ID --kind summary|transcript --block transaction [--json]`로 연결했다. 0.8.2 소스에는 Auth 시트의 공식 읽기 상태 확인·선택 녹음의 요약/전사 preview와 넓어진 기본 창(1608×854, 탐색 sidebar/목록/Work Sidebar 280/420/350pt)도 반영했다. 이 새 경로는 기존 sync·자동 분류의 provider를 자동 변경하지 않는다. 최종 앱 설치와 개인 녹음 읽기 증거는 §8에 기록한다. 공식 경로의 정규 캐시 ingest는 아직 구현하지 않았다.

이 검토자의 실행 검증:

```text
.venv/bin/pytest -q tests/test_official_cli.py
28 passed in 0.33s
.venv/bin/ruff check core/official_cli.py tests/test_official_cli.py
All checks passed!
```

단위 검증은 subprocess를 stub했고, 실제 공식 CLI `me`나 개인 녹음 본문 읽기를 대신하지 않는다. 비밀값 비노출·잘못된 package/경로·PATH 충돌·timeout·exit 0이지만 본문 없음·복수 탭·link-only 상태를 검증했다.

메인 작업의 패키징 전 전체 검증은 **Python 449개**(실제 JavaScript 소스 검증 16개 포함), **Swift 36개**, Ruff 81개 파일·diff 검사 통과다. 이 결과를 설치·로그인·개인 본문 수집 성공으로 확대하지 않는다.

## 6. 다음 구조 개선 순서 — 제안

1. **인증 상태 분리:** `local-library`, `official-read`, `web-write` 상태를 따로 보여 준다. Web access가 만료돼도 로컬 검색과 공식 읽기를 사용할 수 있어야 한다. 공식 CLI와 MCP 상태도 각각 확인한다.
2. **명시적인 읽기 선택:** 사용자가 선택한 녹음에서 공식 읽기를 실행하고 source와 완료 여부를 표시한다. 모든 오류를 다른 provider로 무조건 재시도하지 않는다. 권한·네트워크·본문 부재를 구별한다.
3. **정규 ingest 계약:** `provider`, `file_id`, `block`, `fetched_at`, `schema_version`, 페이지 완료 여부와 raw 내용 해시를 저장한다. 시간 단위·ISO 날짜·note tab 보존을 확인한 뒤 기존 cache model로 변환한다. 현재 adapter는 이 단계까지 구현한 것이 아니다.
4. **전체 수집과 자동화:** 필터 없는 목록 pagination과 transcript cursor checkpoint, ID별 재시도, 일정한 동시성 제한을 둔다. 이미 수집한 콘텐츠를 반복 내려받지 않는다. 기본 목록만으로 삭제·휴지통 상태를 추론하지 않는다.
5. **쓰기 경계 유지:** Web API의 폴더·제목·서버 화자 변경은 기존 preview→apply→snapshot undo 계약을 유지한다. 공식 개인 계정 API가 이를 제공하는지 버전별로 재검증한 후 교체한다.

## 7. 배포물 검증 기록

공식 npm registry의 latest metadata와 tarball을 읽고 SHA512 integrity를 확인했다. 내려받은 코드를 실행하거나 새 패키지를 설치하지 않았다. 정적 검사 자료는 `/tmp/plaud-official-access-2026-09-06`에 있다. 공식 문서가 연결하는 GitHub 조직에서는 CLI/MCP의 대응 공개 소스 저장소를 확인하지 못했으므로, 공개 Git 저장소를 감사했다고 주장하지 않는다.

| 배포물 | 버전 | tarball SHA256 |
|---|---|---|
| `@plaud-ai/cli` | 0.3.11 | `5bb752259295e8b8f51bc2e2c0913bedf6ac928c40f4281623efc505e18f0252` |
| `@plaud-ai/mcp` | 0.3.10 | `dbc9aa56e10d128f38045c18d055a33394c42d47f1ba5cd987c27d69eb557d9a` |

설치된 CLI `dist/index.js`와 검증한 tarball의 같은 파일은 모두 SHA256 `0b48dbe8f29401c0d4fc31b68094d829f995b4657bcbbb54a76b9a018756d9a8`였다. 어댑터의 매 실행 discovery는 manifest identity를 확인하며, 이 일회 SHA 검증을 향후 버전의 암호학 검증까지 보장하는 것으로 해석하면 안 된다.

## 8. 최종 개인용 앱 반영·검증

- `/Applications/Plaud Note Manager.app` **0.8.2 (34)** 설치·서명 검증 완료. 최종 build timestamp `2026-09-06T07:55:14Z`, source fingerprint `9e1aaa57cd28dd69f3bdd921fe4d9a5d442476e99845208e2323a0ffb33d52ff`, binary SHA256 `864995714e893d5879379dab104a03055165726d8ed594c6bb19d02ddc19eb53`.
- 직전 앱은 `/Applications/.plaud-install.irytR9/Previous Plaud Note Manager.app`에 보존했다. 여러 단계의 중간 설치도 각각 백업됐다.
- 최종 앱 PID 30835는 2026-09-06 16:56:36 KST 시작. CUA에서 1,500개 목록, 녹음 요약, 작업 패널 및 `22h · 갱신 미연결` 표시 확인. 기본 sidebar 280pt / list 약 420pt / detail 555pt / Work Sidebar 350pt를 재실행 후 확인했다. 제목은 여유 모드 15pt, 조밀 모드 14pt이며 날짜가 잘리지 않게 했다.
- 실제 구분선 드래그로 저장·복원하는 UI 검증은 완료하지 못했다. 드래그 시도를 했으나 폭 변화가 관찰되지 않았다. 폭 계산·저장된 값 처리의 단위 검증과 기본값 재실행만 확인됐다.
- 유휴 CPU는 `ps` 단일 관찰에서 0.0%, RSS 432,240KB. 이는 일반적인 지연 개선율이나 지속 부하 측정이 아니다.
- 최종 Python **449 tests passed**, Swift **36 tests passed**, Ruff check/format(81 files), `git diff --check`, release build와 `codesign --verify --deep --strict` 통과.

### 빈 본문 재요청 루프 추가 수정

첫 실행에서 100개 미캐시 파일을 반복 backfill하며 매번 99개 완료·1개 timeout이 나고, 실제 캐시는 1,400/1,500으로 그대로인 현상을 확인했다. `save_content`는 빈 결과를 캐시하지 않으므로 정상적인 빈 응답까지 30초마다 재요청되고 있었다. 실패 목록 중 한 건은 공식 MCP `get_file`에서도 source_list와 note_list가 비어 있었다.

SQLite schema v4에 `content_fetch_attempts`를 추가했다. 빈 응답은 1시간, 오류는 5분 뒤 다시 요청한다. `edit_time`이 달라지면 즉시 재시도하고, 정상 본문이 저장되면 대기 기록을 해제한다. `sync-content --force`는 수동 재시도이며 이미 캐시된 파일과 휴지통은 제외한다. CLI는 실제 캐시·빈 응답·실패를 따로 집계한다. 백그라운드 부분 실패는 모달 오류창을 띄우지 않고 툴바 도움말에 남긴다.

최종 앱의 실제 DB에서 **empty 97 / failed 3**, 당장 재요청 대상 **0개**를 읽기 전용 쿼리로 확인했다. 100개를 캐시 완료로 표시한 것이 아니며, 본문 준비 전 상태를 유지하면서 불필요한 반복 요청을 막은 결과다.

### 남은 실제 인증 연결

기존 Web API 접속의 live probe는 성공했다. 앱 전용 WebKit에는 로그인 세션이 없어 실제 로그인 페이지를 표시했다. 따라서 자동 갱신 성공이나 다음 날 무인 재연결까지 검증했다고 주장하지 않는다. 앱 안에서 최초 로그인을 완료하거나, 사용자가 질문에 명시적으로 허용한 뒤 Chrome 세션 연결을 진행하고 `auto_refresh=ready`를 확인해야 한다. 공식 CLI는 최종 앱 안의 연결 확인에서 valid였고, 선택한 5분 36초 녹음의 요약과 시간·화자가 포함된 전사 미리보기를 실제로 열었다. MCP 계정 조회도 성공했다. 초기 제한된 CLI 실행의 needs_login 결과는 설치 앱의 결과와 달랐다. 실행 권한/환경 차이의 가능성은 있으나 세부 원인은 확정하지 않았다.

self_docked: 공식 refresh token의 고정 수명·무기한 무인 운영은 검증하지 못했다. 공식 CLI/MCP 인증을 Web 쓰기 인증으로 재사용할 수 있다는 주장은 기각했다. 소스에 옵션이 있다는 사실을 모든 개인 녹음에서 해당 본문이 존재한다는 증거로 쓰지 않았다. MCP 사용자 조회와 최종 앱의 공식 CLI 요약·전사 읽기는 성공했다. Cloud 변경·유료 AI/STT·Vault 송출·플러그인 설치는 실행하지 않았다. Chrome 자격증명 추출 명령은 자동 승인 거절로 미실행이었다. 전체 ingest·예약 자동화·provider 전환은 제안 단계다.
