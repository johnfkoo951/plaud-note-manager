import AppKit
import SwiftUI

struct PlaudAuthSheet: View {
    @ObservedObject var store: FileStore
    let onDone: () -> Void

    @State private var curlText: String = ""
    @State private var authenticating = false
    @State private var showAdvancedCurl = false
    @State private var webStatus = "Log in with Plaud. The app will capture the session locally."
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
            webLoginCard
            advancedCurl
            footer
        }
        .padding(22)
        .frame(width: 820, height: 720)
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "key.viewfinder")
                .font(.system(size: 22, weight: .semibold))
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(AppUI.accentPink)
            VStack(alignment: .leading, spacing: 3) {
                Text("Sign in with Plaud")
                    .font(.title3.weight(.semibold))
                Text("No terminal command. Credentials are written only to the local .env.")
                    .font(AppUI.metaFont)
                    .foregroundStyle(.secondary)
            }
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
    }

    private var webLoginCard: some View {
        VStack(alignment: .leading, spacing: AppUI.spacingS) {
            HStack {
                Label(webStatus, systemImage: authenticating ? "arrow.triangle.2.circlepath" : "globe")
                    .font(AppUI.metaFont)
                    .foregroundStyle(.secondary)
                Spacer()
                Button {
                    NSWorkspace.shared.open(URL(string: "https://web.plaud.ai/")!)
                } label: {
                    Label("Open in Browser", systemImage: "safari")
                }
            }

            PlaudWebLoginView(
                onCapture: { capture in
                    authenticateWithWebCapture(capture)
                },
                onStatus: { status in
                    webStatus = status
                }
            )
            .frame(minHeight: 480)
            .clipShape(RoundedRectangle(cornerRadius: AppUI.radius))
            .overlay(
                RoundedRectangle(cornerRadius: AppUI.radius)
                    .stroke(AppUI.cardStroke)
            )
        }
    }

    private var advancedCurl: some View {
        DisclosureGroup("Advanced cURL fallback", isExpanded: $showAdvancedCurl) {
            VStack(alignment: .leading, spacing: AppUI.spacingS) {
                HStack(spacing: AppUI.spacingS) {
                    Button {
                        let text = NSPasteboard.general.string(forType: .string) ?? ""
                        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                            store.lastCommandError = "클립보드에 Plaud cURL 텍스트가 없습니다."
                        } else {
                            curlText = text
                        }
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
            Text("The app stores auth locally and never prints tokens after capture.")
                .font(AppUI.metaFont)
                .foregroundStyle(.secondary)
            Spacer()
            Button("Cancel") { onDone() }
                .keyboardShortcut(.cancelAction)
        }
    }

    private func authenticateWithWebCapture(_ capture: PlaudWebAuthCapture) {
        guard !isBusy else { return }
        authenticating = true
        Task {
            let ok = await store.refreshAuthFromWebLogin(capture)
            await MainActor.run {
                authenticating = false
                if ok {
                    onDone()
                } else {
                    webStatus = "Capture received, but Plaud rejected it. Try Clear Web Session."
                }
            }
        }
    }

    private func authenticateWithCurl() {
        guard !trimmedCurl.isEmpty, !isBusy else { return }
        authenticating = true
        let curl = trimmedCurl
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
