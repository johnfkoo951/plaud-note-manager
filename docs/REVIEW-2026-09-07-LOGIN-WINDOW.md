# 2026-09-07 로그인 창 재수정 및 설치 검증

## 사용자 문제와 범위

0.8.2 설치 앱의 인증 설정에서 Google 패스키 오류가 발생했고, 설정 sheet 안의
420pt 웹뷰가 페이지 하단을 잘랐다. 이번 변경은 현재 `/Applications/Plaud Note Manager.app`
업데이트, 로그인 콜백·캡처 수정, 실제 화면 검증까지 포함한다. 이전 작업의 dirty 파일과
데이터를 보존했으며 commit/push는 실행하지 않았다.

## 확인한 원인과 수정

| 문제 | 수정 | 근거와 한계 |
|---|---|---|
| Google `ux_mode=popup` 로그인인데 `createWebViewWith`가 원래 Plaud 웹뷰에 Google 요청을 로드 | 제공된 WKWebViewConfiguration으로 child 웹뷰를 반환하고 별도 창 유지 | 기존 코드와 실제 Google URL의 popup 흐름 확인. 새 Google 창 진입을 실제 확인. 인증 완료 callback 성공은 본인 인증 뒤 확인 필요 |
| Google 오류 화면에서도 Plaud 헤더 대기로만 표시 | Google 로그인/패스키/오류/지원 제한 경로별 상태 표시 | URL의 host/path 및 알려진 오류 표지만 판정. Google DOM 변경·UA 위장 없음 |
| 이전 만료/비워크스페이스 헤더가 캡처 조립을 선점 | JWT workspace/만료 검사, 새로운 access가 이전 비동기 조립 세대를 무효화 | cookie callback·지연 검사·localStorage 결과 모두 epoch와 조립 세대 검사 |
| access와 다른 generation의 refresh가 결합되거나 12초 뒤 access-only 완료로 고정 | workspace ID 및 exact access token과 일치하는 refresh만 완료 처리 | pair 미완성이면 3초 backoff 후 다음 API 요청에서 재시도 가능 |
| 설정창의 작은 웹뷰·중첩 스크롤 | 별도 1120×900pt, resizable 로그인 창. 현재 화면 안에 제한하고 크기 저장 | 실제 설치 앱에서 페이지 입력란·하단 버튼·앱 footer 표시 확인 |
| NSHostingView가 페이지 이상적 높이를 창 크기로 적용 | `sizingOptions=[]`, 콘텐츠 부착 후 화면 범위 재적용, GeometryReader로 웹뷰 실제 영역 배정 | 첫 설치 QA에서 1120×2018pt로 커짐을 발견. 다음 수정에서 웹뷰 크기 0 문제도 확인·수정. 최종 1120×900pt로 유지 |
| 별도 창과 hidden recovery/cURL/Chrome/수동 갱신 중복 실행 | interactive owner가 있는 동안 다른 인증 진입 차단 | 검증·Keychain 저장 중 창 닫기를 막고, 닫은 뒤 hidden recovery에 cooldown 적용 |
| 네트워크 검증 실패 CLI 안내가 저장 성공처럼 표시 | 사람이 읽는 `web-auth` 출력은 인증 유지 안내 및 exit 1 | 앱용 JSON bridge는 구조화된 실패 상태와 기존 exit 계약 유지 |

## 실제 설치·검증 기록

- 최종 버전: **0.8.3**, build 34.
- 빌드 시각: `2026-09-06T15:27:15Z` (KST 2026-09-07 00:27:15).
- 소스 fingerprint: `ccec68554caae940f5b04ae7384a78cc8ad1529a661714d4908103d85790855a`.
- 설치 binary SHA256: `4beb579a4c74ed9a63987eeb9f88d080df692449db769943480167767aff6e98`.
- Python 전체 **451 passed**. 인증 subset **106 passed**. 변경 Python Ruff 통과.
- 최종 Swift 전체 **43 passed**, 2026-09-07 00:30:41 KST. release build, signature 검증, `git diff --check` 통과.
- 실제 실행 파일: `/Applications/Plaud Note Manager.app/Contents/MacOS/PlaudNoteApp`.
- 실제 실행 PID 35320, 시작 시각 00:27:31 KST. 단일 관측 CPU 0.1%, RSS 471216 KB. 벤치마크나 장시간 성능 개선율로 해석하지 않는다.
- 로그인 창 저장 frame: `340 138 1120 900`, display `0 0 1800 1130`. 웹 로드 후에도 화면 안의 기본 크기 유지.
- 실제 설치 UI에서 `큰 로그인 창 열기` → 별도 Plaud 창 → Google 별도 창 → 기존 Google 계정 → `Try another way` → `Enter your password` 진행 확인.
- Google 비밀번호 입력 화면을 사용자에게 열어 두었다. 비밀번호·본인 인증 코드를 대화로 요청하거나 브라우저에서 추출하지 않았다.
- Google 본인 인증 완료 전 읽기 전용 `auth --json --live`: `state=valid`, `live_ok=true`, `live_state=ok`, `auto_refresh=not_bootstrapped`.

**현재 접속은 실제 API에서 검증됐지만, 자동 갱신 연결은 아직 미완료다.**
사용자가 열린 Google 창에서 비밀번호/추가 본인 인증을 완료하면 앱이 같은 access/refresh
쌍을 검증하고 저장한다. 이후 `auto_refresh=ready` 및 실제 sync를 확인해야 한다.
창 크기·팝업 진입·테스트 성공을 Google 로그인 완료나 장기 세션 유지 성공으로 간주하지 않는다.

## 복원과 남은 조건

- 처음 업데이트 전 안정 버전 0.8.2 보존 경로:
  `/Applications/.plaud-install.QFnQXM/Previous Plaud Note Manager.app`.
- 최종 교체 직전 번들 보존 경로:
  `/Applications/.plaud-install.gFNIuc/Previous Plaud Note Manager.app`.
- Google 또는 Plaud 계정 쿠키를 자동 삭제하지 않았다. Chrome 자격증명 추출, 보안 설정 변경,
  유료 AI/STT, Cloud 변경, Vault 송출을 검증 목적으로 실행하지 않았다.
- 기본 브라우저 로그인은 별도 세션이다. 브라우저로 이동했다는 이유만으로 앱 자동 갱신이
  연결됐다고 표시하지 않는다. 기존 같은 계정의 cURL로 현재 읽기·쓰기 접속을 연결할 수 있다.

## 제공자 문서 확인

- Google은 embedded user-agent 환경에서 OAuth가 제한될 수 있다고 설명한다:
  [Google native OAuth / disallowed_useragent](https://developers.google.com/identity/protocols/oauth2/native-app#disallowed_useragent).
- Google 패스키 도움말은 기기 요구사항과 다른 로그인 방법을 설명한다:
  [Sign in with a passkey](https://support.google.com/accounts/answer/13548313?hl=en).
- Apple WKWebView 패스키 지원에는 relying party associated domain 등 조건이 있다:
  [Supporting passkeys](https://developer.apple.com/documentation/authenticationservices/supporting-passkeys).
- 같은 이메일이어도 Plaud 로그인 방식에 따라 계정이 다를 수 있다. 기존 Google 사용자를
  Plaud 이메일 코드 로그인으로 임의 전환하지 않는다:
  [Plaud sign-in methods](https://support.plaud.ai/hc/en-us/articles/50913509647513-Sign-in-methods).
- ASWebAuthenticationSession은 제공자가 지원하는 callback 계약이 필요하므로 임의로 시스템
  브라우저를 열고 Plaud 웹 토큰을 회수하는 대체 수단으로 취급하지 않는다:
  [ASWebAuthenticationSession](https://developer.apple.com/documentation/authenticationservices/aswebauthenticationsession).

self_docked: Google 패스키 자체가 수정됐다는 주장은 기각한다. 실제 Google 로그인 완료,
자동 갱신 ready 및 만료 후 재갱신 성공은 아직 관측하지 못했다.
