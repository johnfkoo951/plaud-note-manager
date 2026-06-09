import AppKit
import SwiftUI

struct PlaudAuthSheet: View {
    @ObservedObject var store: FileStore
    let onDone: () -> Void

    @State private var curlText: String = ""
    @State private var authenticating = false
    @State private var clipboardWatching = false
    @State private var showAdvancedCurl = false
    @State private var showEmbeddedLogin = false
    @State private var webStatus = "Embedded login is available if browser import is not enough."
    @State private var clearingSession = false

    private var trimmedCurl: String {
        curlText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var isBusy: Bool {
        authenticating || store.refreshingAuth
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppUI.spacingL) {
            header
            browserImportCard
            embeddedLoginFallback
            advancedCurl
            footer
        }
        .padding(22)
        .frame(width: 820)
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
                Text("Use your browser first. The app imports credentials locally and never prints tokens.")
                    .font(AppUI.metaFont)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
    }

    private var browserImportCard: some View {
        VStack(alignment: .leading, spacing: AppUI.spacingM) {
            HStack(alignment: .center, spacing: AppUI.spacingM) {
                Label("Browser login", systemImage: "safari")
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

            Text("A URL alone is not enough. In DevTools > Network, find the `api-apne1.plaud.ai/file/simple/web?...` request, right-click it, then Copy > Copy as cURL. The copied text must start with `curl` and include headers.")
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
                        : "Chrome path: View > Developer > Developer Tools > Network > filter simple/web."
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

    private var embeddedLoginFallback: some View {
        DisclosureGroup("Embedded Web Login fallback", isExpanded: $showEmbeddedLogin) {
            VStack(alignment: .leading, spacing: AppUI.spacingS) {
                HStack {
                    Label(webStatus, systemImage: authenticating ? "arrow.triangle.2.circlepath" : "globe")
                        .font(AppUI.metaFont)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button {
                        clearingSession = true
                        PlaudWebSession.clear {
                            clearingSession = false
                            webStatus = "Plaud Web session cleared. Sign in again."
                        }
                    } label: {
                        HStack(spacing: 6) {
                            if clearingSession {
                                ProgressView().controlSize(.small)
                            }
                            Text("Clear Web Session")
                        }
                    }
                    .disabled(clearingSession || isBusy)
                }

                Text("If Google shows a Bluetooth or passkey error here, use Try another way or the browser import above.")
                    .font(AppUI.metaFont)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                webLoginCard
            }
            .padding(.top, AppUI.spacingS)
        }
    }

    private var webLoginCard: some View {
        PlaudWebLoginView(
            onCapture: { capture in
                authenticateWithWebCapture(capture)
            },
            onStatus: { status in
                webStatus = status
            }
        )
        .frame(minHeight: 420)
        .clipShape(RoundedRectangle(cornerRadius: AppUI.radius))
        .overlay(
            RoundedRectangle(cornerRadius: AppUI.radius)
                .stroke(AppUI.cardStroke)
        )
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
                    .disabled(trimmedCurl.isEmpty || isBusy)
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
            }
            .padding(.top, AppUI.spacingS)
        }
    }

    private var footer: some View {
        HStack {
            Text("Credentials are stored in the project .env and used by the Plaud CLI bridge.")
                .font(AppUI.metaFont)
                .foregroundStyle(.secondary)
            Spacer()
            Button("Cancel") { onDone() }
                .keyboardShortcut(.cancelAction)
        }
    }

    private func startBrowserLogin() {
        NSWorkspace.shared.open(URL(string: "https://web.plaud.ai/")!)
        clipboardWatching = true
        webStatus = "Browser opened. Copy a Plaud API request as cURL."
    }

    private func importClipboardCurl() {
        let text = NSPasteboard.general.string(forType: .string) ?? ""
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            store.lastCommandError = "클립보드에 Plaud cURL 텍스트가 없습니다."
            return
        }
        guard looksLikePlaudCurl(trimmed) else {
            curlText = trimmed
            store.lastCommandError = "URL만으로는 부족합니다. DevTools > Network에서 Plaud 요청을 우클릭한 뒤 Copy > Copy as cURL로 복사해 주세요."
            return
        }
        curlText = trimmed
        authenticateWithCurl(trimmed)
    }

    private func pasteClipboardIntoEditor() {
        let text = NSPasteboard.general.string(forType: .string) ?? ""
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            store.lastCommandError = "클립보드에 Plaud cURL 텍스트가 없습니다."
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
                if looksLikePlaudCurl(trimmed) {
                    curlText = trimmed
                    webStatus = "Plaud cURL found on clipboard. Importing..."
                    authenticateWithCurl(trimmed)
                    return
                }
            }
            try? await Task.sleep(nanoseconds: 800_000_000)
        }
    }

    private func looksLikePlaudCurl(_ text: String) -> Bool {
        let lowercased = text.lowercased()
        return lowercased.contains("curl")
            && lowercased.contains("plaud")
            && (
                lowercased.contains("authorization")
                    || lowercased.contains("x-pld-user")
                    || lowercased.contains("x-device-id")
            )
    }

    private func authenticateWithWebCapture(_ capture: PlaudWebAuthCapture) {
        guard !isBusy else { return }
        clipboardWatching = false
        authenticating = true
        Task {
            let ok = await store.refreshAuthFromWebLogin(capture)
            await MainActor.run {
                authenticating = false
                if ok {
                    onDone()
                } else {
                    webStatus = "Capture received, but Plaud rejected it. Try browser import."
                }
            }
        }
    }

    private func authenticateWithCurl(_ curlOverride: String? = nil) {
        let curl = (curlOverride ?? trimmedCurl).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !curl.isEmpty, !isBusy else { return }
        clipboardWatching = false
        authenticating = true
        Task {
            let ok = await store.refreshAuthCredentials(curlText: curl)
            await MainActor.run {
                authenticating = false
                if ok {
                    curlText = ""
                    onDone()
                }
            }
        }
    }
}
