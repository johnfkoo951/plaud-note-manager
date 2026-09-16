import AppKit
import SwiftUI

/// Fits both fresh and restored windows to the current display, including a
/// smaller laptop display after an external monitor has been disconnected.
enum PlaudLoginWindowGeometry {
    static func fittedFrame(_ proposed: NSRect?, visibleFrame: NSRect) -> NSRect {
        let bounds = visibleFrame.insetBy(dx: 12, dy: 12)
        let size = NSSize(
            width: min(max(proposed?.width ?? 1120, 780), bounds.width),
            height: min(max(proposed?.height ?? 900, 680), bounds.height)
        )
        let origin = proposed?.origin ?? NSPoint(
            x: bounds.midX - size.width / 2, y: bounds.midY - size.height / 2
        )
        return NSRect(
            x: min(max(origin.x, bounds.minX), bounds.maxX - size.width),
            y: min(max(origin.y, bounds.minY), bounds.maxY - size.height),
            width: size.width, height: size.height
        )
    }
}

@MainActor
final class PlaudLoginWindowController: NSObject, NSWindowDelegate {
    private var window: NSWindow?
    private weak var store: FileStore?
    private var presentationID = UUID()

    func show(for store: FileStore) {
        if let window {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        self.store = store
        store.interactiveLoginActive = true
        store.authRecoveryPhase = .idle
        store.authRecoveryRequestID &+= 1
        let screen = NSApp.keyWindow?.screen ?? NSScreen.main
        let visible = screen?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let frame = PlaudLoginWindowGeometry.fittedFrame(nil, visibleFrame: visible)
        let window = NSWindow(
            contentRect: frame,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false
        )
        window.title = "Plaud 로그인 · 자동 갱신 연결"
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.minSize = NSSize(width: min(780, frame.width), height: min(680, frame.height))
        let restored = window.setFrameUsingName("PlaudLoginWindow.v2")
        window.setFrame(
            PlaudLoginWindowGeometry.fittedFrame(restored ? window.frame : nil, visibleFrame: visible),
            display: false
        )
        window.setFrameAutosaveName("PlaudLoginWindow.v2")
        presentationID = UUID()
        let currentID = presentationID
        let hostingView = NSHostingView(rootView: PlaudLoginWindowView(store: store) { [weak self] in
            guard self?.presentationID == currentID else { return }
            self?.window?.performClose(nil)
        })
        // Web pages can report a tall ideal size. AppKit owns this resizable
        // window's geometry; never let SwiftUI grow it to the page's height.
        hostingView.sizingOptions = []
        window.contentView = hostingView
        window.setFrame(
            PlaudLoginWindowGeometry.fittedFrame(window.frame, visibleFrame: visible),
            display: false
        )
        self.window = window
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        // web-auth validates then atomically writes Keychain in a subprocess.
        // Do not imply that closing this UI cancels that transaction.
        store?.refreshingAuth != true
    }

    func windowWillClose(_ notification: Notification) {
        store?.interactiveLoginActive = false
        store?.authRecoveryRetryAfter = Date().addingTimeInterval(AuthRecoveryPolicy.retryDelay)
        // Release WebKit and its popups, callbacks and in-memory capture with
        // the window instead of keeping a hidden sign-in flow alive.
        window?.contentView = nil
        window?.delegate = nil
        window = nil
        store = nil
        presentationID = UUID()
    }
}

private struct PlaudLoginWindowView: View {
    @ObservedObject var store: FileStore
    let onDone: () -> Void
    @State private var status = "Plaud에서 기존 계정으로 로그인해 주세요."
    @State private var error: String?
    @State private var generation = 0
    @State private var validating = false
    @State private var pendingCapture: PlaudWebAuthCapture?
    @State private var validationTask: Task<Void, Never>?

    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Label("자동 갱신 연결", systemImage: "key.viewfinder")
                        .font(.headline)
                    Spacer()
                    Button("새 로그인 다시 시작") {
                        pendingCapture = nil
                        error = nil
                        generation += 1
                    }.disabled(validating)
                    Button("기본 브라우저에서 열기") {
                        NSWorkspace.shared.open(URL(string: "https://web.plaud.ai/")!)
                    }
                    Button("닫기", action: onDone)
                        .keyboardShortcut(.cancelAction)
                        .disabled(validating)
                }
                Text("기존 계정과 같은 로그인 방식을 사용하세요. Google 패스키가 실패하면 Google 화면에서 ‘다른 방법 시도’를 선택하세요.")
                    .font(.callout).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(alignment: .top) {
                    if validating { ProgressView().controlSize(.small) }
                    Text(validating ? "접속과 자동 갱신 정보를 검증하고 있습니다. 저장이 끝나면 창을 닫을 수 있습니다…" : status)
                        .font(.callout).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                if let error {
                    HStack(alignment: .top) {
                        Label(error, systemImage: "exclamationmark.triangle")
                            .font(.callout).foregroundStyle(.orange)
                            .fixedSize(horizontal: false, vertical: true)
                        if pendingCapture != nil {
                            Button("연결 검증 재시도") { startValidation() }.disabled(validating)
                        }
                    }
                }
            }
            .padding(14)
            Divider()
            GeometryReader { geometry in
                PlaudWebLoginView(
                    onCapture: { capture in
                        pendingCapture = capture
                        startValidation()
                    },
                    onStatus: { status = $0 },
                    captureGeneration: generation
                )
                .frame(width: geometry.size.width, height: geometry.size.height)
            }
            Divider()
            Text("기본 브라우저로 로그인한 경우 인증 설정에서 cURL을 가져오세요. 이 창은 앱 전용 세션의 자동 갱신 연결을 확인한 뒤 닫힙니다.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
        }
        .onDisappear {
            validationTask?.cancel()
            validationTask = nil
            pendingCapture = nil
        }
    }

    private func startValidation() {
        guard !validating, pendingCapture != nil else { return }
        validating = true
        error = nil
        validationTask = Task { @MainActor in
            while let capture = pendingCapture, !Task.isCancelled {
                pendingCapture = nil
                let success = await store.refreshAuthFromWebLogin(capture)
                guard !Task.isCancelled else { return }
                if success {
                    validating = false
                    onDone()
                    return
                }
                error = store.lastCommandError ?? "연결을 검증하지 못했습니다. 잠시 후 다시 시도해 주세요."
                store.lastCommandError = nil
                // A newer capture received while validating takes precedence.
                // Retain a failed capture in memory for an explicit retry.
                if pendingCapture == nil {
                    pendingCapture = capture
                    break
                }
            }
            validating = false
        }
    }
}
