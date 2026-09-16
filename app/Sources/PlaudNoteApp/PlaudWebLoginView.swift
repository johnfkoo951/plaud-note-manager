import AppKit
import SwiftUI
import WebKit

private let plaudAuthMessageName = "plaudAuthCapture"

enum PlaudWebCapturePolicy {
    struct WorkspaceClaims: Equatable {
        let sub: String
        let wid: String
        let expiresAt: TimeInterval
    }

    static func workspaceClaims(authorization: String, now: TimeInterval = Date().timeIntervalSince1970) -> WorkspaceClaims? {
        let token = bareToken(authorization)
        let parts = token.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 3 else { return nil }
        var encoded = String(parts[1]).replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        encoded += String(repeating: "=", count: (4 - encoded.count % 4) % 4)
        guard let data = Data(base64Encoded: encoded),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let sub = object["sub"] as? String, !sub.isEmpty,
              let wid = object["wid"] as? String, !wid.isEmpty,
              let expiry = (object["exp"] as? NSNumber)?.doubleValue,
              expiry.isFinite, expiry > now else { return nil }
        return WorkspaceClaims(sub: sub, wid: wid, expiresAt: expiry)
    }

    static func bareToken(_ authorization: String) -> String {
        authorization.replacingOccurrences(of: "^Bearer\\s+", with: "", options: [.regularExpression, .caseInsensitive])
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func canSupersede(current: WorkspaceClaims?, candidate: WorkspaceClaims) -> Bool {
        current.map { candidate.expiresAt >= $0.expiresAt } ?? true
    }

    static func workspaceListMatches(_ raw: String, authorization: String) -> Bool {
        guard let claims = workspaceClaims(authorization: authorization),
              let data = raw.data(using: .utf8),
              let value = try? JSONSerialization.jsonObject(with: data) else { return false }
        let entries = (value as? [[String: Any]]) ?? (value as? [String: Any]).map { [$0] } ?? []
        return entries.contains { entry in
            let wid = entry["workspaceId"] as? String ?? entry["workspace_id"] as? String
            let access = entry["workspaceToken"] as? String ?? entry["workspace_token"] as? String
                ?? entry["accessToken"] as? String ?? entry["access_token"] as? String
            let refresh = entry["refreshToken"] as? String ?? entry["refresh_token"] as? String
            return wid == claims.wid && access.map { bareToken($0) == bareToken(authorization) } == true
                && refresh?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
        }
    }

    static func accepts(kind: String?, recoveryMode: Bool) -> Bool {
        recoveryMode ? kind == "recoveryCapture" : kind == "capture"
    }

    static func trustedCookieDomain(_ domain: String) -> Bool {
        let host = domain.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
        return host == "plaud.ai" || host.hasSuffix(".plaud.ai")
    }

    static func trustedAPIURL(_ raw: String) -> URL? {
        guard let url = URL(string: raw), url.scheme?.lowercased() == "https",
              url.user == nil, url.password == nil, url.port == nil || url.port == 443,
              let host = url.host?.lowercased(),
              host.range(of: #"^api(?:-[a-z0-9-]+)?\.plaud\.ai$"#, options: .regularExpression) != nil
        else { return nil }
        return url
    }
}

enum PlaudLoginNavigationStatus: Equatable {
    case plaud, googleSignIn, googlePasskey, googlePasskeyError, googleUnsupported, other

    static func classify(_ url: URL?) -> Self {
        guard let url, let host = url.host?.lowercased() else { return .other }
        if PlaudWebCapturePolicy.trustedCookieDomain(host) { return .plaud }
        guard host == "accounts.google.com" else { return .other }
        let path = url.path.lowercased()
        let denied = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.contains {
            $0.name.lowercased() == "error" && $0.value?.lowercased() == "disallowed_useragent"
        } ?? false
        if denied || path.contains("deniedsigninrejected") || path.contains("/signin/rejected") {
            return .googleUnsupported
        }
        if path.contains("/challenge/pk/error") { return .googlePasskeyError }
        if path.contains("/challenge/pk") || path.contains("/challenge/webauthn") { return .googlePasskey }
        return .googleSignIn
    }

    var message: String {
        switch self {
        case .plaud: return "Plaud 로그인 진행 중입니다. 로그인 후 연결 상태를 확인합니다."
        case .googleSignIn: return "Google 로그인 화면입니다. 같은 Google 계정으로 인증을 완료하면 Plaud로 돌아갑니다."
        case .googlePasskey: return "Google 패스키 인증 화면입니다. 기기 인증을 완료하거나 Google의 ‘다른 방법 시도’를 직접 선택해주세요."
        case .googlePasskeyError: return "Google 패스키 인증에 실패했습니다. Google의 ‘다른 방법 시도’로 같은 계정의 다른 인증 방법을 선택할 수 있습니다."
        case .googleUnsupported: return "Google이 이 로그인 환경을 지원하지 않는다고 표시했습니다. 외부 브라우저에서 Plaud를 열어 같은 Google 계정으로 로그인해주세요."
        case .other: return "외부 인증 화면에서 로그인을 진행 중입니다."
        }
    }
}

/// Return this child WebView to WebKit so window.opener/postMessage remain
/// attached to the original Plaud page. Loading its request in the opener
/// destroys the Google Identity Services callback context.
private final class PlaudOAuthPopupController: NSWindowController, NSWindowDelegate {
    let webView: WKWebView
    private let domainLabel = NSTextField(labelWithString: "로그인")
    var onClose: (() -> Void)?

    init(webView: WKWebView, parent: NSWindow?) {
        self.webView = webView
        let screen = parent?.screen ?? NSScreen.main
        let visible = screen?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1100, height: 850)
        let width = max(360, min(760, visible.width - 60))
        let height = max(360, min(800, visible.height - 70))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable],
                              backing: .buffered, defer: false)
        window.title = "Google / Plaud 로그인"
        window.minSize = NSSize(width: min(520, width), height: min(500, height))
        window.isReleasedWhenClosed = false
        super.init(window: window)
        window.delegate = self
        domainLabel.font = .systemFont(ofSize: 12)
        domainLabel.lineBreakMode = .byTruncatingTail
        let reload = NSButton(title: "새로고침", target: self, action: #selector(reloadPage))
        reload.bezelStyle = .rounded
        let toolbar = NSStackView(views: [domainLabel, reload])
        toolbar.orientation = .horizontal
        toolbar.edgeInsets = NSEdgeInsets(top: 8, left: 12, bottom: 8, right: 12)
        toolbar.distribution = .fill
        domainLabel.setContentHuggingPriority(.defaultLow, for: .horizontal)
        let content = NSView()
        for view in [toolbar, webView] {
            view.translatesAutoresizingMaskIntoConstraints = false
            content.addSubview(view)
        }
        NSLayoutConstraint.activate([
            toolbar.topAnchor.constraint(equalTo: content.topAnchor),
            toolbar.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            toolbar.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            webView.topAnchor.constraint(equalTo: toolbar.bottomAnchor),
            webView.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            webView.bottomAnchor.constraint(equalTo: content.bottomAnchor),
        ])
        window.contentView = content
        window.center()
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    func updatePage(_ url: URL?) {
        domainLabel.stringValue = url?.host ?? "로그인"
        window?.title = PlaudLoginNavigationStatus.classify(url) == .plaud ? "Plaud 로그인" : "Google / 외부 로그인"
    }

    @objc private func reloadPage() { webView.reload() }
    func windowWillClose(_ notification: Notification) { onClose?() }
}

struct PlaudWebCaptureEpoch {
    private(set) var id = UUID().uuidString
    private(set) var active = true
    func accepts(_ id: String?) -> Bool { active && id == self.id }
    mutating func reset() { id = UUID().uuidString; active = true }
    mutating func dispose() { id = UUID().uuidString; active = false }
}

enum PlaudWebSession {
    static func clear(completion: @escaping () -> Void) {
        // Wipe everything, not just *.plaud.ai — Google SSO cookies otherwise
        // survive and the live page silently stays logged in. Safe because
        // this is the app's only WKWebView.
        let store = WKWebsiteDataStore.default()
        store.removeData(
            ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(),
            modifiedSince: .distantPast
        ) {
            DispatchQueue.main.async { completion() }
        }
    }
}

struct PlaudWebLoginView: NSViewRepresentable {
    var onCapture: (PlaudWebAuthCapture) -> Void
    var onStatus: (String) -> Void
    /// When true, use Plaud's persistent HttpOnly account session to mint a
    /// brand-new workspace credential pair. Interactive login leaves this off.
    var recoveryMode: Bool = false
    /// Bumped by the owner after a failed capture or a session clear. Each
    /// change resets the coordinator's one-shot capture latch and reloads the
    /// login page so a fresh attempt is possible.
    var captureGeneration: Int = 0

    func makeCoordinator() -> Coordinator {
        Coordinator(
            onCapture: onCapture,
            onStatus: onStatus,
            recoveryMode: recoveryMode
        )
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        let script = WKUserScript(
            source: context.coordinator.captureScript,
            injectionTime: .atDocumentStart,
            forMainFrameOnly: false
        )
        configuration.userContentController.addUserScript(script)
        configuration.userContentController.add(
            context.coordinator,
            name: plaudAuthMessageName
        )

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        context.coordinator.webView = webView
        context.coordinator.lastSeenGeneration = captureGeneration
        webView.load(URLRequest(url: URL(string: "https://web.plaud.ai/")!))
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        guard context.coordinator.lastSeenGeneration != captureGeneration else { return }
        context.coordinator.invalidatePage(webView)
        webView.stopLoading()
        context.coordinator.lastSeenGeneration = captureGeneration
        context.coordinator.resetCaptureState()
        webView.configuration.userContentController.removeAllUserScripts()
        webView.configuration.userContentController.addUserScript(WKUserScript(
            source: context.coordinator.captureScript,
            injectionTime: .atDocumentStart, forMainFrameOnly: false
        ))
        webView.load(URLRequest(url: URL(string: "https://web.plaud.ai/")!))
    }

    static func dismantleNSView(_ webView: WKWebView, coordinator: Coordinator) {
        coordinator.dispose()
        coordinator.invalidatePage(webView)
        webView.stopLoading()
        webView.navigationDelegate = nil
        webView.uiDelegate = nil
        webView.configuration.userContentController.removeScriptMessageHandler(
            forName: plaudAuthMessageName
        )
        webView.configuration.userContentController.removeAllUserScripts()
        coordinator.webView = nil
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
        var latestHeaders: [String: String] = [:]
        var latestURL: URL?
        var latestWorkspaceList: String?
        var isEmitting = false
        var didCapture = false
        var didStartAccountRecovery = false
        var cookieReadAttempts = 0
        var workspaceReadAttempts = 0
        var lastSeenGeneration = 0
        weak var webView: WKWebView?
        private weak var captureWebView: WKWebView?
        private var captureEpoch = PlaudWebCaptureEpoch()
        private var assemblyGeneration: UInt64 = 0
        private var nextCaptureAttemptAt = Date.distantPast
        private var popups: [ObjectIdentifier: PlaudOAuthPopupController] = [:]

        static let invalidateScript = "window.__plaudNativeCaptureGeneration = null; window.__plaudRecoveryAbort?.abort();"
        var captureScript: String {
            """
            (() => {
              const host = window.location.hostname.toLowerCase();
              if (host !== "plaud.ai" && !host.endsWith(".plaud.ai")) return;
              window.__plaudNativeCaptureGeneration = \(javascriptLiteral(captureEpoch.id));
              \(plaudAuthCaptureScript)
            })();
            """
        }

        func invalidatePage(_ webView: WKWebView) {
            guard PlaudLoginNavigationStatus.classify(webView.url) == .plaud else { return }
            webView.evaluateJavaScript(Self.invalidateScript, completionHandler: nil)
        }

        private let onCapture: (PlaudWebAuthCapture) -> Void
        private let onStatus: (String) -> Void
        private let recoveryMode: Bool

        init(
            onCapture: @escaping (PlaudWebAuthCapture) -> Void,
            onStatus: @escaping (String) -> Void,
            recoveryMode: Bool
        ) {
            self.onCapture = onCapture
            self.onStatus = onStatus
            self.recoveryMode = recoveryMode
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            guard captureEpoch.active, isManaged(webView) else { return }
            reportNavigation(webView.url, in: webView)
            guard PlaudLoginNavigationStatus.classify(webView.url) == .plaud else { return }
            if recoveryMode {
                let path = webView.url?.path.lowercased() ?? ""
                if path.contains("login") || path.contains("sign-in") {
                    onStatus("Plaud account sign-in required.")
                    return
                }
                runAccountSessionRecovery(in: webView)
            }
            emitIfComplete()
        }

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            if captureEpoch.active, isManaged(webView), navigationAction.targetFrame?.isMainFrame != false {
                reportNavigation(navigationAction.request.url, in: webView)
                if navigationAction.targetFrame?.isMainFrame == true,
                   PlaudLoginNavigationStatus.classify(navigationAction.request.url) != .plaud,
                   captureWebView === webView {
                    assemblyGeneration &+= 1
                    isEmitting = false
                }
            }
            decisionHandler(.allow)
        }

        private func reportNavigation(_ url: URL?, in webView: WKWebView) {
            guard captureEpoch.active, !didCapture else { return }
            popups[ObjectIdentifier(webView)]?.updatePage(url)
            let status = PlaudLoginNavigationStatus.classify(url)
            // A background Plaud request must not overwrite the actual Google
            // challenge the user is looking at in a retained popup.
            if status == .plaud, webView === self.webView, !popups.isEmpty { return }
            onStatus(status.message)
        }

        private func isManaged(_ candidate: WKWebView) -> Bool {
            candidate === webView || popups[ObjectIdentifier(candidate)] != nil
        }

        private func runAccountSessionRecovery(in webView: WKWebView) {
            guard captureEpoch.active, !didStartAccountRecovery, !didCapture else { return }
            let epoch = captureEpoch.id
            didStartAccountRecovery = true
            onStatus("Recovering from the saved Plaud account session…")
            // Unlike evaluateJavaScript, this API awaits the async recovery's
            // Promise instead of reporting an unsupported result immediately.
            webView.callAsyncJavaScript("return await \(plaudAccountSessionRecoveryScript);",
                                        arguments: [:], in: nil, in: .page) { [weak self, weak webView] result in
                DispatchQueue.main.async {
                    guard let self, let webView, self.isCurrent(epoch, in: webView) else { return }
                    if case .failure(let error) = result {
                        self.onStatus(
                            "Plaud silent recovery failed: "
                                + error.localizedDescription
                        )
                        return
                    }
                    if case .success(let value) = result,
                       let body = value as? [String: Any],
                       let status = body["status"] as? String,
                       status == "needsLogin" {
                        self.onStatus("Plaud account sign-in required.")
                    }
                }
            }
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            guard captureEpoch.active, isManaged(webView) else { return }
            let nsError = error as NSError
            if nsError.domain == NSURLErrorDomain, nsError.code == NSURLErrorCancelled { return }
            onStatus("Plaud Web failed to load: \(error.localizedDescription)")
        }

        func webView(
            _ webView: WKWebView,
            didFailProvisionalNavigation navigation: WKNavigation!,
            withError error: Error
        ) {
            guard captureEpoch.active, isManaged(webView) else { return }
            let nsError = error as NSError
            if nsError.domain == NSURLErrorDomain, nsError.code == NSURLErrorCancelled { return }
            onStatus("Plaud Web failed to load: \(error.localizedDescription)")
        }

        /// Clears the one-shot capture latch so the embedded login can emit
        /// again after a rejected capture or a session reset.
        func resetCaptureState() {
            captureEpoch.reset()
            closeAllPopups()
            assemblyGeneration &+= 1
            captureWebView = nil
            nextCaptureAttemptAt = .distantPast
            didCapture = false
            isEmitting = false
            cookieReadAttempts = 0
            workspaceReadAttempts = 0
            latestWorkspaceList = nil
            latestHeaders.removeAll()
            latestURL = nil
            didStartAccountRecovery = false
        }

        func dispose() {
            captureEpoch.dispose()
            closeAllPopups()
            assemblyGeneration &+= 1
            captureWebView = nil
            didCapture = true
            isEmitting = false
        }

        private func isCurrent(_ epoch: String, in webView: WKWebView, assembly: UInt64? = nil) -> Bool {
            captureEpoch.accepts(epoch) && isManaged(webView) && !didCapture
                && (assembly == nil || assembly == assemblyGeneration)
        }

        func webView(
            _ webView: WKWebView,
            createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction,
            windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            guard captureEpoch.active, !recoveryMode, isManaged(webView),
                  navigationAction.targetFrame == nil else { return nil }
            // WebKit must create/navigate its related child using this exact
            // configuration. Do not replace it or load the request in Plaud's
            // opener; both break provider popup callbacks.
            let child = WKWebView(frame: .zero, configuration: configuration)
            child.navigationDelegate = self
            child.uiDelegate = self
            let key = ObjectIdentifier(child)
            let controller = PlaudOAuthPopupController(webView: child, parent: webView.window)
            controller.onClose = { [weak self] in self?.closePopup(key) }
            popups[key] = controller
            controller.updatePage(navigationAction.request.url)
            controller.showWindow(nil)
            controller.window?.makeKeyAndOrderFront(nil)
            reportNavigation(navigationAction.request.url, in: child)
            return child
        }

        func webViewDidClose(_ webView: WKWebView) {
            closePopup(ObjectIdentifier(webView))
        }

        private func closePopup(_ key: ObjectIdentifier) {
            guard let popup = popups.removeValue(forKey: key) else { return }
            popup.onClose = nil
            let wasCaptureSource = captureWebView === popup.webView
            invalidatePage(popup.webView)
            popup.webView.stopLoading()
            popup.webView.navigationDelegate = nil
            popup.webView.uiDelegate = nil
            popup.close()
            // The opener stays alive for Google's postMessage callback. If the
            // child supplied a Plaud request just before closing, continue
            // reading the shared Plaud storage from the opener.
            if wasCaptureSource, captureEpoch.active, !didCapture {
                assemblyGeneration &+= 1
                captureWebView = webView
                isEmitting = false
                emitIfComplete()
            } else if captureEpoch.active, !didCapture {
                onStatus("로그인 창이 닫혔습니다. Plaud 화면에서 연결 결과를 확인하거나 같은 계정으로 다시 시도해주세요.")
            }
        }

        private func closeAllPopups() {
            let windows = Array(popups.values)
            popups.removeAll()
            for popup in windows {
                popup.onClose = nil
                invalidatePage(popup.webView)
                popup.webView.stopLoading()
                popup.webView.navigationDelegate = nil
                popup.webView.uiDelegate = nil
                popup.close()
            }
        }

        func userContentController(
            _ userContentController: WKUserContentController,
            didReceive message: WKScriptMessage
        ) {
            // Only trust messages posted from Plaud's own origin — the user
            // script runs in every frame (Google SSO included).
            let host = message.frameInfo.securityOrigin.host
            guard captureEpoch.active, let sourceWebView = message.webView, isManaged(sourceWebView),
                  message.frameInfo.securityOrigin.protocol == "https",
                  PlaudWebCapturePolicy.trustedCookieDomain(host) else { return }
            guard message.name == plaudAuthMessageName,
                  let body = message.body as? [String: Any]
            else {
                return
            }
            guard captureEpoch.accepts(body["generation"] as? String) else { return }
            if body["kind"] as? String == "recoveryStatus" {
                guard recoveryMode, !didCapture else { return }
                let state = body["status"] as? String ?? "failed"
                let detail = body["detail"] as? String ?? ""
                if state == "needsLogin" {
                    onStatus("Plaud account sign-in required.")
                } else if state == "failed" || state == "deferred" {
                    onStatus("Plaud silent recovery failed: \(detail)")
                } else if state == "starting" {
                    onStatus("Recovering from the saved Plaud account session…")
                }
                return
            }
            guard PlaudWebCapturePolicy.accepts(kind: body["kind"] as? String,
                                                recoveryMode: recoveryMode),
                  !didCapture,
                  let urlString = body["url"] as? String,
                  let capturedURL = PlaudWebCapturePolicy.trustedAPIURL(urlString)
            else { return }
            if recoveryMode, (body["workspaceList"] as? String)?.isEmpty != false {
                onStatus("Plaud silent recovery failed: fresh workspace pair missing")
                return
            }
            guard let headers = body["headers"] as? [String: Any] else { return }
            var requestHeaders: [String: String] = [:]
            for (key, value) in headers {
                requestHeaders[key.lowercased()] = String(describing: value)
            }
            // Keep one request intact. Merging headers from unrelated API
            // requests can pair a token with the wrong device/base URL.
            guard requestHeaders["authorization"]?.isEmpty == false,
                  requestHeaders["x-device-id"]?.isEmpty == false
            else { return }
            guard let authorization = requestHeaders["authorization"],
                  let candidate = PlaudWebCapturePolicy.workspaceClaims(authorization: authorization)
            else { return }
            let sameToken = header("authorization").map {
                PlaudWebCapturePolicy.bareToken($0) == PlaudWebCapturePolicy.bareToken(authorization)
            } ?? false
            if sameToken && (isEmitting || Date() < nextCaptureAttemptAt) { return }
            let previous = header("authorization").flatMap { PlaudWebCapturePolicy.workspaceClaims(authorization: $0) }
            guard PlaudWebCapturePolicy.canSupersede(current: previous, candidate: candidate) else { return }
            assemblyGeneration &+= 1
            isEmitting = false
            captureWebView = sourceWebView
            latestHeaders = requestHeaders
            latestURL = capturedURL
            latestWorkspaceList = nil
            if let workspaceList = body["workspaceList"] as? String,
               PlaudWebCapturePolicy.workspaceListMatches(workspaceList, authorization: authorization) {
                latestWorkspaceList = workspaceList
            }
            cookieReadAttempts = 0
            workspaceReadAttempts = 0
            emitIfComplete()
        }

        private func header(_ key: String) -> String? {
            latestHeaders[key.lowercased()]?
                .trimmingCharacters(in: .whitespacesAndNewlines)
        }

        private func emitIfComplete() {
            guard captureEpoch.active, !didCapture, !isEmitting,
                  let webView = captureWebView ?? self.webView,
                  PlaudLoginNavigationStatus.classify(webView.url) == .plaud else { return }
            let epoch = captureEpoch.id
            let assembly = assemblyGeneration
            guard let authorization = header("authorization"),
                  let deviceID = header("x-device-id"),
                  !authorization.isEmpty,
                  !deviceID.isEmpty
            else {
                if popups.isEmpty { onStatus("Plaud 로그인 완료 후 연결 정보를 기다리고 있습니다.") }
                return
            }
            let user = header("x-pld-user")

            isEmitting = true
            webView.configuration.websiteDataStore.httpCookieStore.getAllCookies { [weak self, weak webView] cookies in
                let cookieLine = cookies
                    .filter {
                        let name = $0.name.lowercased()
                        return PlaudWebCapturePolicy.trustedCookieDomain($0.domain)
                            && name != "pld_ut"
                            && name != "pld_urt"
                    }
                    .sorted { $0.name < $1.name }
                    .map { "\($0.name)=\($0.value)" }
                    .joined(separator: "; ")

                DispatchQueue.main.async {
                    guard let self, let webView, self.isCurrent(epoch, in: webView, assembly: assembly) else { return }
                    if cookieLine.isEmpty && self.cookieReadAttempts < 6 {
                        self.cookieReadAttempts += 1
                        self.isEmitting = false
                        self.onStatus("Captured headers; waiting for Plaud cookies.")
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self, weak webView] in
                            guard let self, let webView, self.isCurrent(epoch, in: webView, assembly: assembly) else { return }
                            self.emitIfComplete()
                        }
                        return
                    }
                    self.finishCapture(
                        authorization: authorization,
                        deviceID: deviceID,
                        user: user,
                        cookieLine: cookieLine, epoch: epoch, assembly: assembly
                    )
                }
            }
        }

        private func finishCapture(
            authorization: String,
            deviceID: String,
            user: String?,
            cookieLine: String,
            epoch: String,
            assembly: UInt64
        ) {
            // Plaud 3.x stores this under `pld_<JWT sub>:workspaceList`, not
            // the old raw `workspaceList` key.  Select only the current JWT's
            // account + workspace; never scan another signed-in account's
            // namespaced value.
            guard let webView = captureWebView ?? self.webView,
                  isCurrent(epoch, in: webView, assembly: assembly),
                  PlaudLoginNavigationStatus.classify(webView.url) == .plaud else { return }
            if let workspaceList = latestWorkspaceList, !workspaceList.isEmpty {
                emitCapture(
                    authorization: authorization,
                    deviceID: deviceID,
                    user: user,
                    cookieLine: cookieLine,
                    workspaceList: workspaceList, epoch: epoch, assembly: assembly
                )
                return
            }
            guard let claims = PlaudWebCapturePolicy.workspaceClaims(authorization: authorization) else {
                isEmitting = false
                onStatus("Waiting for a Plaud workspace session.")
                return
            }
            let script = workspaceListScript(sub: claims.sub, wid: claims.wid, authorization: authorization)
            webView.evaluateJavaScript(script) { [weak self, weak webView] value, _ in
                DispatchQueue.main.async {
                    guard let self, let webView, self.isCurrent(epoch, in: webView, assembly: assembly) else { return }
                    if let workspaceList = value as? String, !workspaceList.isEmpty {
                        self.emitCapture(
                            authorization: authorization, deviceID: deviceID, user: user,
                            cookieLine: cookieLine, workspaceList: workspaceList, epoch: epoch, assembly: assembly
                        )
                        return
                    }
                    // The request hook fires before Plaud's response stores the
                    // rotating token. Poll for up to 12 seconds instead of
                    // completing a false-success access-token-only login.
                    if self.workspaceReadAttempts < 30 {
                        self.workspaceReadAttempts += 1
                        if self.workspaceReadAttempts == 1 || self.workspaceReadAttempts % 10 == 0 {
                            self.onStatus("Plaud 로그인 확인됨 — 같은 세션의 자동 갱신 정보를 기다리고 있습니다…")
                        }
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self, weak webView] in
                            guard let self, let webView, self.isCurrent(epoch, in: webView, assembly: assembly) else { return }
                            self.finishCapture(
                                authorization: authorization,
                                deviceID: deviceID,
                                user: user,
                                cookieLine: cookieLine, epoch: epoch, assembly: assembly
                            )
                        }
                    } else {
                        // Do not latch an access-only result as a completed
                        // login: a fresh matching pair can arrive afterward.
                        self.isEmitting = false
                        self.nextCaptureAttemptAt = Date().addingTimeInterval(3)
                        self.onStatus("Plaud 자동 갱신 정보가 아직 준비되지 않았습니다. 다음 연결 정보를 기다리거나 이 창을 새로고침해주세요.")
                    }
                }
            }
        }

        private func javascriptLiteral(_ value: String) -> String {
            guard let data = try? JSONEncoder().encode(value),
                  let literal = String(data: data, encoding: .utf8)
            else { return "\"\"" }
            return literal
        }

        private func workspaceListScript(sub: String, wid: String, authorization: String) -> String {
            let subLiteral = javascriptLiteral(sub)
            let widLiteral = javascriptLiteral(wid)
            let tokenLiteral = javascriptLiteral(PlaudWebCapturePolicy.bareToken(authorization))
            return """
            (() => {
              const expectedSub = \(subLiteral);
              const expectedWid = \(widLiteral);
              const expectedToken = \(tokenLiteral);
              const preferredKey = `pld_${expectedSub}:workspaceList`;
              const namespacedKeys = [];
              for (let i = 0; i < localStorage.length; i += 1) {
                const key = localStorage.key(i);
                if (key && key.endsWith(':workspaceList')) namespacedKeys.push(key);
              }
              const preferredRaw = localStorage.getItem(preferredKey);
              const rawValues = [];
              if (preferredRaw !== null) {
                rawValues.push(preferredRaw);
              } else if (namespacedKeys.length === 0) {
                for (const key of ['workspaceList', 'pld_workspaceList']) {
                  const raw = localStorage.getItem(key);
                  if (raw !== null) rawValues.push(raw);
                }
              }
              const decode = (raw) => {
                let value = raw;
                for (let i = 0; i < 2 && typeof value === 'string'; i += 1) {
                  try { value = JSON.parse(value); } catch (_) { return []; }
                }
                if (Array.isArray(value)) return value;
                return value && typeof value === 'object' ? [value] : [];
              };
              const candidates = [];
              for (const raw of rawValues) {
                for (const entry of decode(raw)) {
                  if (!entry || typeof entry !== 'object') continue;
                  const workspaceId = String(entry.workspaceId ?? entry.workspace_id ?? '');
                  const refreshToken = entry.refreshToken ?? entry.refresh_token;
                  const accessToken = entry.workspaceToken ?? entry.workspace_token ?? entry.accessToken ?? entry.access_token;
                  const rawExpiry = entry.refreshExpiresAt ?? entry.refresh_expires_at ?? null;
                  let expiresAt = Number(rawExpiry ?? 0);
                  if (expiresAt > 0 && expiresAt < 1e12) expiresAt *= 1000;
                  if (workspaceId !== expectedWid) continue;
                  if (typeof accessToken !== 'string'
                      || accessToken.replace(/^bearer\\s+/i, '').trim() !== expectedToken) continue;
                  if (typeof refreshToken !== 'string' || !refreshToken.trim()) continue;
                  if (Number.isFinite(expiresAt) && expiresAt > 0 && expiresAt <= Date.now()) continue;
                  candidates.push({
                    workspaceId,
                    refreshToken: refreshToken.trim(),
                    refreshExpiresAt: rawExpiry,
                    domain: typeof entry.domain === 'string' ? entry.domain : null,
                    expiresAt: Number.isFinite(expiresAt) ? expiresAt : 0
                  });
                }
              }
              candidates.sort((a, b) => b.expiresAt - a.expiresAt);
              if (!candidates.length) return null;
              const selected = candidates[0];
              return JSON.stringify([{
                workspaceId: selected.workspaceId,
                workspaceToken: expectedToken,
                refreshToken: selected.refreshToken,
                refreshExpiresAt: selected.refreshExpiresAt,
                domain: selected.domain
              }]);
            })()
            """
        }

        private func emitCapture(
            authorization: String,
            deviceID: String,
            user: String?,
            cookieLine: String,
            workspaceList: String?,
            epoch: String,
            assembly: UInt64
        ) {
            guard let webView = captureWebView ?? self.webView,
                  isCurrent(epoch, in: webView, assembly: assembly) else { return }
            guard let workspaceList,
                  PlaudWebCapturePolicy.workspaceListMatches(workspaceList, authorization: authorization) else {
                isEmitting = false
                nextCaptureAttemptAt = Date().addingTimeInterval(3)
                onStatus("로그인과 같은 세션의 자동 갱신 정보를 기다리고 있습니다.")
                return
            }
            didCapture = true
            isEmitting = false
            onStatus(
                cookieLine.isEmpty
                    ? "Captured headers. Saving without cookies."
                    : "Captured Plaud session. Saving locally."
            )
            onCapture(
                PlaudWebAuthCapture(
                    authorization: authorization,
                    xDeviceID: deviceID,
                    xPldUser: user,
                    cookie: cookieLine.isEmpty ? nil : cookieLine,
                    xPldTag: header("x-pld-tag"),
                    baseURL: baseURLString(),
                    appLanguage: header("app-language"),
                    appPlatform: header("app-platform"),
                    editFrom: header("edit-from"),
                    origin: header("origin"),
                    referer: header("referer"),
                    timezone: header("timezone"),
                    workspaceList: workspaceList
                )
            )
        }

        private func baseURLString() -> String? {
            guard let latestURL, let scheme = latestURL.scheme, let host = latestURL.host else {
                return nil
            }
            if let port = latestURL.port {
                return "\(scheme)://\(host):\(port)"
            }
            return "\(scheme)://\(host)"
        }
    }
}
