import SwiftUI
import WebKit

private let plaudAuthMessageName = "plaudAuthCapture"

enum PlaudWebSession {
    static func clear(completion: @escaping () -> Void) {
        let store = WKWebsiteDataStore.default()
        store.fetchDataRecords(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes()) { records in
            let plaudRecords = records.filter {
                $0.displayName.localizedCaseInsensitiveContains("plaud")
            }
            store.removeData(
                ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(),
                for: plaudRecords
            ) {
                store.httpCookieStore.getAllCookies { cookies in
                    let plaudCookies = cookies.filter {
                        $0.domain.lowercased().contains("plaud.ai")
                    }
                    guard !plaudCookies.isEmpty else {
                        DispatchQueue.main.async { completion() }
                        return
                    }
                    let group = DispatchGroup()
                    for cookie in plaudCookies {
                        group.enter()
                        store.httpCookieStore.delete(cookie) {
                            group.leave()
                        }
                    }
                    group.notify(queue: .main) {
                        completion()
                    }
                }
            }
        }
    }
}

struct PlaudWebLoginView: NSViewRepresentable {
    var onCapture: (PlaudWebAuthCapture) -> Void
    var onStatus: (String) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onCapture: onCapture, onStatus: onStatus)
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        let script = WKUserScript(
            source: plaudAuthCaptureScript,
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
        webView.load(URLRequest(url: URL(string: "https://web.plaud.ai/")!))
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
        var latestHeaders: [String: String] = [:]
        var latestURL: URL?
        var isEmitting = false
        var didCapture = false
        var cookieReadAttempts = 0
        weak var webView: WKWebView?

        private let onCapture: (PlaudWebAuthCapture) -> Void
        private let onStatus: (String) -> Void

        init(
            onCapture: @escaping (PlaudWebAuthCapture) -> Void,
            onStatus: @escaping (String) -> Void
        ) {
            self.onCapture = onCapture
            self.onStatus = onStatus
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            onStatus("Embedded login waiting for Plaud API traffic.")
            emitIfComplete()
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            onStatus("Plaud Web failed to load: \(error.localizedDescription)")
        }

        func webView(
            _ webView: WKWebView,
            createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction,
            windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            if navigationAction.targetFrame == nil {
                webView.load(navigationAction.request)
            }
            return nil
        }

        func userContentController(
            _ userContentController: WKUserContentController,
            didReceive message: WKScriptMessage
        ) {
            guard message.name == plaudAuthMessageName,
                  let body = message.body as? [String: Any],
                  let headers = body["headers"] as? [String: Any]
            else {
                return
            }
            if let urlString = body["url"] as? String {
                latestURL = URL(string: urlString)
            }
            for (key, value) in headers {
                latestHeaders[key.lowercased()] = String(describing: value)
            }
            cookieReadAttempts = 0
            emitIfComplete()
        }

        private func header(_ key: String) -> String? {
            latestHeaders[key.lowercased()]?
                .trimmingCharacters(in: .whitespacesAndNewlines)
        }

        private func emitIfComplete() {
            guard !didCapture, !isEmitting, let webView else { return }
            guard let authorization = header("authorization"),
                  let deviceID = header("x-device-id"),
                  let user = header("x-pld-user"),
                  !authorization.isEmpty,
                  !deviceID.isEmpty,
                  !user.isEmpty
            else {
                onStatus("Embedded login waiting for Plaud auth headers.")
                return
            }

            isEmitting = true
            webView.configuration.websiteDataStore.httpCookieStore.getAllCookies { cookies in
                let cookieLine = cookies
                    .filter { $0.domain.lowercased().contains("plaud.ai") }
                    .sorted { $0.name < $1.name }
                    .map { "\($0.name)=\($0.value)" }
                    .joined(separator: "; ")

                DispatchQueue.main.async {
                    if cookieLine.isEmpty && self.cookieReadAttempts < 6 {
                        self.cookieReadAttempts += 1
                        self.isEmitting = false
                        self.onStatus("Captured headers; waiting for Plaud cookies.")
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                            self.emitIfComplete()
                        }
                        return
                    }
                    self.finishCapture(
                        authorization: authorization,
                        deviceID: deviceID,
                        user: user,
                        cookieLine: cookieLine
                    )
                }
            }
        }

        private func finishCapture(
            authorization: String,
            deviceID: String,
            user: String,
            cookieLine: String
        ) {
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
                    timezone: header("timezone")
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
