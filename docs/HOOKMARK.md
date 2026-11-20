# 녹음 딥링크와 Hookmark 연동

Plaud Note Manager는 녹음 하나, 또는 하루치 녹음 목록을 가리키는 `plaudnote://` 링크를
만든다. 다른 앱(Obsidian, Hookmark, 메일, 브라우저, Raycast)에서 이 링크를 누르면 앱이
앞으로 나오고 그 녹음이 선택된다.

## 링크 형식

| 링크 | 동작 |
|---|---|
| `plaudnote://open?id=<file_id>` | 그 녹음을 선택하고 목록에서 보이게 스크롤. 현재 폴더·검색·사용 단계 필터에 가려져 있으면 All Files(휴지통이면 Trash, 아카이브면 Archive)로 넓힌다 |
| `plaudnote://day?date=2026-08-29` | 달력 보기로 바꾸고 그날 녹음만 목록에 표시 (모든 폴더, 검색·사용 단계 필터 해제) |

입력으로는 `plaudnote://recording/<id>`, `plaudnote://day/<YYYY-MM-DD>`도 받는다.
앱이 꺼져 있으면 링크가 앱을 띄우고, 라이브러리를 처음 읽은 직후에 그 녹음을 연다.

스킴은 `scripts/package-macos-app.sh`가 만드는 번들의 Info.plist에 등록된다.
`swift run`으로 띄운 개발 빌드는 링크를 받지 못한다.

## 앱에서 링크 얻기

- 녹음 우클릭 → **Copy App Link (Markdown)**: `[제목](plaudnote://open?id=…)`.
  HTML 링크도 같이 복사돼서 Notes·Mail에는 제목이 링크로 붙는다.
- 녹음 우클릭 → **Copy App Link (URL)**: 주소만.
- 메뉴 Edit → **Copy Link to Recording** (`⌥⌘C`): 선택한 녹음의 Markdown 링크.
- `⌘K` → **Copy App Link (Markdown)**.
- 달력에서 날짜를 고른 뒤 목록 위 🔗 버튼: 그날의 `plaudnote://day?date=…` 링크.
  데일리 노트에 붙여 두면 그날 녹음 목록으로 바로 열린다.

## Hookmark 설정 (1회)

> ✅ 주 작업 Mac에는 설치 완료 (2026-11-20, Hookmark Setapp판). 아래는 다른 Mac에서
> 처음부터 할 때의 절차다.

앱은 AppleScript로 "지금 선택된 녹음"의 주소와 제목을 알려준다
(`app/Resources/PlaudNoteManager.sdef`). Hookmark에는 이 두 값을 읽는 스크립트만 등록하면 된다.
Scripts 탭은 Hookmark Pro 기능이며 Setapp판에는 포함돼 있다.

1. 사전 확인 — 앱에서 녹음 하나를 선택한 상태로 터미널에서:
   ```bash
   osascript -e 'tell application "Plaud Note Manager" to get selected recording url'
   # → plaudnote://open?id=… 가 나오면 OK
   ```
2. Hookmark → **Settings…(⌘,) → Scripts 탭**
3. 왼쪽 앱 목록에서 **Plaud Note Manager**를 고른다. 없으면 좌하단 **+** →
   `/Applications/Plaud Note Manager.app`
4. **Get Address** 탭에 붙여넣고 **Save**:
   ```applescript
   tell application "Plaud Note Manager" to return selected recording url
   ```
5. **Get Name** 탭에 붙여넣고 **Save**:
   ```applescript
   tell application "Plaud Note Manager" to return selected recording name
   ```
6. Scheme 칸과 Open Item·New Item 탭은 비워 둔다. 주소가 URL이라 Hookmark가
   LaunchServices로 열고, 앱이 받아서 그 녹음으로 이동한다.

처음 Hookmark를 부를 때 macOS가 "Hookmark가 Plaud Note Manager를 제어하려고 합니다"라고
물으면 허용한다 (시스템 설정 → 개인정보 보호 및 보안 → 자동화에서 나중에 바꿀 수 있다).

사용: 앱에서 녹음을 선택 → Hookmark 단축키 → 제목·`plaudnote://` 주소 확인 →
Copy Link로 노트에 붙이거나 다른 파일·메일과 Hook.

설정 UI 대신 파일로 넣을 수도 있다. Hookmark가 저장하는 곳은
`~/Library/Application Support/com.cogsciapps.hook-setapp/Customized-Software-Integations.plist`
(Setapp판)의 `scripts` → `com.cmdspace.PlaudNoteManager` → `name`·`title`(Get Name)·`url`(Get Address)이다.
Hookmark를 끈 상태에서 고치고 다시 실행해야 한다. 실행 중에 고치면 종료할 때 덮어쓴다.

Hookmark까지 거친 최종 확인 (Plaud Note Manager가 맨 앞 창이어야 함):

```bash
osascript -e 'tell application "Plaud Note Manager" to activate' && sleep 1 && \
osascript -e 'tell application "Hookmark" to address of (bookmark from active window)'
# → plaudnote://open?id=…
```

## 문제 해결

| 증상 | 원인 / 해법 |
|---|---|
| 링크를 눌러도 아무 일도 없음 | 스킴이 없는 예전 빌드가 실행 중. `scripts/package-macos-app.sh`로 다시 설치하고, 실행 중인 앱을 종료한 뒤 `/Applications/Plaud Note Manager.app`을 경로로 실행 |
| 링크가 예전 버전 앱이나 `dist/` 사본을 띄움 | 같은 bundle id를 쓰는 사본이 여럿이다. 패키징 스크립트가 설치 뒤 `dist/` 사본의 LaunchServices 등록을 풀고 `/Applications` 쪽을 다시 등록한다. 꼬이면 `lsregister -f "/Applications/Plaud Note Manager.app"` |
| Hookmark 창에 "주소를 가져올 수 없음" | 맨 앞 창이 Plaud Note Manager가 아니거나, 앱에서 녹음이 선택돼 있지 않음 (날짜 링크로 연 직후처럼 상세 화면이 비어 있을 때) |
| Hookmark가 주소를 못 얻음 | 앱에서 녹음이 선택돼 있는지 확인 (선택이 없으면 빈 값). 1번 `osascript` 확인이 실패하면 자동화 권한부터 확인 |
| "링크한 녹음을 찾지 못했습니다" | 아직 동기화되지 않았거나 Plaud에서 완전히 지워진 녹음. 툴바 새로고침 후 다시 열기 |
