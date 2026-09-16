import AppKit
import SwiftUI

struct PlaudAuthSheet: View {
    @ObservedObject var store: FileStore
    let onDone: () -> Void

    @State private var curlText: String = ""
    @State private var authenticating = false
    @State private var clipboardWatching = false
    @State private var showAdvancedCurl = false
    @State private var webStatus = "Sign in once; automatic renewal will be verified and saved."
    /// Failure surfaced inline in the sheet. The root ContentView alert is
    /// queued behind this sheet on macOS, so errors must be shown here.
    @State private var importError: String?
    /// A valid access credential may be stored even when Chrome privacy rules
    /// prevent capture of the rotating renewal token. Keep that partial success
    /// visibly distinct from an authentication failure.
    @State private var importNotice: String?
    @State private var accessCredentialSaved = false
    @State private var recovering = false
    @State private var recoverStatus: String?

    private var trimmedCurl: String {
        curlText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var curlValidation: PlaudCurlValidation {
        PlaudCurlValidator.inspect(trimmedCurl)
    }

    private var isBusy: Bool {
        authenticating || store.refreshingAuth || recovering || store.refreshingWorkspaceToken || store.interactiveLoginActive
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            ScrollView {
                VStack(alignment: .leading, spacing: AppUI.spacingL) {
                    connectionStatus
                    browserImportCard
                    advancedCurl
                    separateLoginCard
                    autoRecoverCard
                    PlaudOfficialAccessView(store: store)
                }.padding(.trailing, 4)
            }
            footer
        }
        .padding(22)
        .frame(width: 820, height: 740)
        .onAppear {
            if store.authRecoveryPhase == .webSession {
                store.authRecoveryPhase = .idle
                store.authRecoveryRequestID &+= 1
            }
        }
        .task(id: clipboardWatching) {
            guard clipboardWatching else { return }
            await watchClipboardForPlaudCurl()
        }
        .onDisappear {
            clipboardWatching = false
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "key.viewfinder")
                .font(.system(size: 22, weight: .semibold))
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(AppUI.accentPink)
            VStack(alignment: .leading, spacing: 3) {
                Text("Authenticate with Plaud")
                    .font(.title3.weight(.semibold))
                Text("cURL로 현재 접속을 연결하고, 앱 로그인으로 자동 갱신을 설정할 수 있습니다. 인증 정보는 macOS Keychain에 저장됩니다.")
                    .font(AppUI.metaFont)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
        }
    }

    private var connectionStatus: some View {
        VStack(alignment: .leading, spacing: 8) {
            if store.interactiveLoginActive {
                Text("별도 로그인 창이 열려 있습니다. 다른 연결 방식을 쓰려면 먼저 로그인 창을 닫아 주세요.")
                    .font(AppUI.metaFont).foregroundStyle(.orange)
            }
            Label(store.auth?.state == "valid" ? "현재 접속 가능" : "현재 접속 상태 확인 필요",
                  systemImage: store.auth?.state == "valid" ? "checkmark.shield.fill" : "key")
                .foregroundStyle(store.auth?.state == "valid" ? AppUI.brandGreen : Color.orange)
            Label(store.auth?.autoRefreshReady == true ? "자동 갱신 연결됨" : "자동 갱신 미연결",
                  systemImage: "arrow.triangle.2.circlepath")
                .foregroundStyle(store.auth?.autoRefreshReady == true ? AppUI.brandGreen : Color.orange)
            if store.auth?.autoRefreshReady != true {
                Text("cURL의 접속 토큰만 저장하면 만료 후 다시 연결해야 합니다. 아래 ‘자동 갱신 연결’을 한 번 완료하면 앱에 저장된 세션으로 갱신을 시도합니다.")
                    .font(AppUI.metaFont).foregroundStyle(.secondary)
            }
        }
    }

    /// Explicit external-browser fallback. Normal automatic recovery uses the
    /// app-owned persistent WebKit session before this sheet is shown.
    private var autoRecoverCard: some View {
        VStack(alignment: .leading, spacing: AppUI.spacingS) {
            HStack(alignment: .center, spacing: AppUI.spacingM) {
                Label("Chrome의 기존 로그인 연결", systemImage: "arrow.triangle.2.circlepath")
                    .font(AppUI.sectionFont)
                Spacer()
                Button {
                    runAutoRecover()
                } label: {
                    HStack(spacing: 6) {
                        if recovering {
                            ProgressView().controlSize(.small)
                        }
                        Text("Chrome 연결")
                    }
                }
                .disabled(recovering || isBusy)
            }
            Text(
                recoverStatus
                    ?? "이 버튼은 Chrome에 저장된 Plaud 인증 자료를 로컬에서 읽고, 서버가 검증한 갱신 토큰만 앱의 Keychain에 저장합니다. 브라우저 보안 설정을 바꾸지 않습니다."
            )
            .font(AppUI.metaFont)
            .foregroundStyle(recoverStatus == nil ? .secondary : Color.primary)
            .fixedSize(horizontal: false, vertical: true)
        }
    }

    @MainActor
    private func runAutoRecover() {
        guard !recovering else { return }
        recovering = true
        recoverStatus = "Checking an existing external Plaud browser session…"
        Task { @MainActor in
            let ok = await store.recoverAuthViaBrowser()
            recovering = false
            if ok {
                recoverStatus = "✅ Recovered — automatic renewal re-armed."
                if accessCredentialSaved {
                    onDone()
                }
            } else {
                recoverStatus = store.lastCommandError
                    ?? "Recovery failed — use Web Login below."
                store.lastCommandError = nil  // keep the error inline, not behind the sheet
                if accessCredentialSaved {
                    importNotice = "현재 접속은 유지됩니다. 앱 로그인으로 자동 갱신을 연결할 수 있습니다."
                }
            }
        }
    }

    private var browserImportCard: some View {
        VStack(alignment: .leading, spacing: AppUI.spacingM) {
            HStack(alignment: .center, spacing: AppUI.spacingM) {
                Label("cURL로 읽기·쓰기 연결", systemImage: "safari")
                    .font(AppUI.sectionFont)
                Spacer()
                Button {
                    startBrowserLogin()
                } label: {
                    Label("Open Plaud", systemImage: "safari")
                }
                Button {
                    importClipboardCurl()
                } label: {
                    HStack(spacing: 6) {
                        if isBusy {
                            ProgressView().controlSize(.small)
                        }
                        Label("Import Copied cURL", systemImage: "doc.on.clipboard")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(isBusy)
            }

            if let importError {
                Label(importError, systemImage: "exclamationmark.triangle.fill")
                    .font(AppUI.metaFont)
                    .foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let importNotice {
                Label(importNotice, systemImage: "checkmark.shield.fill")
                    .font(AppUI.metaFont)
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if authenticating {
                Label(
                    "Checking the copied credentials with Plaud before saving to Keychain…",
                    systemImage: "checkmark.shield"
                )
                .font(AppUI.metaFont)
                .foregroundStyle(AppUI.accentPink)
                .fixedSize(horizontal: false, vertical: true)
            }

            Text("A URL alone is not enough. In DevTools > Network, find any authenticated `api-*.plaud.ai` request (for example `weekly_recommend` or `file/simple/web`), right-click it, then Copy > Copy as cURL. The copied text must include authorization and x-device-id headers.")
                .font(AppUI.metaFont)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: AppUI.spacingS) {
                Image(systemName: clipboardWatching ? "dot.radiowaves.left.and.right" : "doc.on.clipboard")
                    .font(.system(size: 13, weight: .semibold))
                    .symbolRenderingMode(.hierarchical)
                    .foregroundStyle(clipboardWatching ? AppUI.accentPink : .secondary)
                Text(
                    clipboardWatching
                        ? "Watching clipboard for a Plaud cURL..."
                        : "Chrome path: View > Developer > Developer Tools > Network > filter plaud.ai."
                )
                .font(AppUI.metaFont)
                .foregroundStyle(clipboardWatching ? AppUI.accentPink : .secondary)
                Spacer()
                if clipboardWatching {
                    Button("Stop Watching") {
                        clipboardWatching = false
                    }
                    .controlSize(.small)
                }
            }
        }
        .padding(AppUI.spacingL)
        .background(
            LinearGradient(
                colors: [
                    AppUI.brandGreen.opacity(0.10),
                    AppUI.accentPink.opacity(0.08),
                    AppUI.cardFill
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            ),
            in: RoundedRectangle(cornerRadius: AppUI.radius)
        )
        .overlay(
            RoundedRectangle(cornerRadius: AppUI.radius)
                .stroke(AppUI.cardStroke, lineWidth: 1)
        )
    }

    private var separateLoginCard: some View {
        VStack(alignment: .leading, spacing: AppUI.spacingS) {
            HStack {
                Label("자동 갱신 연결 · 앱에서 한 번 로그인", systemImage: "macwindow")
                    .font(AppUI.sectionFont)
                Spacer()
                Button("큰 로그인 창 열기") {
                    clipboardWatching = false
                    onDone()
                    // End the document sheet before showing a full, resizable
                    // browser window. Web content owns its own scrolling.
                    DispatchQueue.main.async { store.plaudLoginWindow.show(for: store) }
                }
                .buttonStyle(.borderedProminent)
                .disabled(isBusy && !store.interactiveLoginActive)
            }
            Text("크기 조절이 가능한 별도 창에서 로그인합니다. 기존 계정과 같은 로그인 방식을 사용해 주세요. Google 패스키가 실패하면 Google 화면의 ‘다른 방법 시도’를 선택할 수 있습니다.")
                .font(AppUI.metaFont).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppUI.spacingL)
        .background(AppUI.cardFill, in: RoundedRectangle(cornerRadius: AppUI.radius))
    }

    private var advancedCurl: some View {
        DisclosureGroup("Paste cURL manually", isExpanded: $showAdvancedCurl) {
            VStack(alignment: .leading, spacing: AppUI.spacingS) {
                HStack(spacing: AppUI.spacingS) {
                    Button {
                        pasteClipboardIntoEditor()
                    } label: {
                        Label("Paste Clipboard", systemImage: "doc.on.clipboard")
                    }
                    Spacer()
                    Button {
                        authenticateWithCurl()
                    } label: {
                        HStack(spacing: 6) {
                            if isBusy {
                                ProgressView().controlSize(.small)
                            }
                            Text("Use cURL")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!curlValidation.canImport || isBusy)
                }

                TextEditor(text: $curlText)
                    .font(.system(size: 11.5, design: .monospaced))
                    .frame(height: 110)
                    .padding(6)
                    .background(AppUI.subtleFill, in: RoundedRectangle(cornerRadius: AppUI.radius))
                    .overlay(
                        RoundedRectangle(cornerRadius: AppUI.radius)
                            .stroke(AppUI.cardStroke)
                    )

                if !trimmedCurl.isEmpty {
                    Label(
                        curlValidation.message,
                        systemImage: curlValidation.canImport
                            ? "checkmark.circle.fill"
                            : "exclamationmark.circle.fill"
                    )
                    .font(AppUI.metaFont)
                    .foregroundStyle(curlValidation.canImport ? AppUI.brandGreen : .red)
                    .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.top, AppUI.spacingS)
        }
    }

    private var footer: some View {
        HStack {
            Text("Authentication is stored in macOS Keychain. The project .env contains no Plaud tokens or cookies.")
                .font(AppUI.metaFont)
                .foregroundStyle(.secondary)
            Spacer()
            Button(accessCredentialSaved ? "Done" : "Cancel") { onDone() }
                .keyboardShortcut(.cancelAction)
        }
    }

    private func startBrowserLogin() {
        NSWorkspace.shared.open(URL(string: "https://web.plaud.ai/")!)
        clipboardWatching = true
        webStatus = "Browser opened. Copy a Plaud API request as cURL."
    }

    private func importClipboardCurl() {
        importError = nil
        importNotice = nil
        accessCredentialSaved = false
        let text = NSPasteboard.general.string(forType: .string) ?? ""
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let validation = PlaudCurlValidator.inspect(trimmed)
        guard validation.canImport else {
            curlText = trimmed
            showAdvancedCurl = true
            importError = validation.message
            return
        }
        curlText = trimmed
        authenticateWithCurl(trimmed)
    }

    private func pasteClipboardIntoEditor() {
        importError = nil
        importNotice = nil
        accessCredentialSaved = false
        let text = NSPasteboard.general.string(forType: .string) ?? ""
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            importError = "클립보드에 Plaud cURL 텍스트가 없습니다."
        } else {
            curlText = text
        }
    }

    @MainActor
    private func watchClipboardForPlaudCurl() async {
        var lastChangeCount = NSPasteboard.general.changeCount
        while clipboardWatching && !Task.isCancelled {
            if NSPasteboard.general.changeCount != lastChangeCount {
                lastChangeCount = NSPasteboard.general.changeCount
                let text = NSPasteboard.general.string(forType: .string) ?? ""
                let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
                if PlaudCurlValidator.inspect(trimmed).canImport {
                    curlText = trimmed
                    webStatus = "Plaud cURL found on clipboard. Importing..."
                    authenticateWithCurl(trimmed)
                    return
                }
            }
            try? await Task.sleep(nanoseconds: 800_000_000)
        }
    }

    private func authenticateWithCurl(_ curlOverride: String? = nil) {
        let curl = (curlOverride ?? trimmedCurl).trimmingCharacters(in: .whitespacesAndNewlines)
        let validation = PlaudCurlValidator.inspect(curl)
        guard validation.canImport, !isBusy else {
            importError = validation.message
            showAdvancedCurl = true
            return
        }
        clipboardWatching = false
        importError = nil
        importNotice = nil
        accessCredentialSaved = false
        authenticating = true
        Task {
            let ok = await store.refreshAuthCredentials(curlText: curl)
            await MainActor.run {
                authenticating = false
                if ok {
                    curlText = ""
                    if store.lastCurlImportAutoRefreshArmed != true {
                        accessCredentialSaved = true
                        importNotice = "현재 접속을 연결했습니다. 자동 갱신은 아래 앱 로그인으로 한 번 설정할 수 있습니다. 지금 창을 닫고 앱을 사용해도 됩니다."
                    } else {
                        onDone()
                    }
                } else {
                    let message = store.lastCommandError
                        ?? "인증 갱신에 실패했습니다. Plaud cURL을 다시 복사해 주세요."
                    store.lastCommandError = nil
                    importError = message
                }
            }
        }
    }
}
