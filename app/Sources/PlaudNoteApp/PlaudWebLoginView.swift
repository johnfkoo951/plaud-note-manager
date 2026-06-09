import SwiftUI
import WebKit

private let plaudAuthMessageName = "plaudAuthCapture"

private let plaudAuthCaptureScript = """
(() => {
  if (window.__plaudAuthCaptureInstalled) { return; }
  window.__plaudAuthCaptureInstalled = true;
  const targetHost = "api-apne1.plaud.ai";
  const post = (url, headers) => {
    try {
      const rawURL = String(url || "");
      if (!rawURL.includes(targetHost)) { return; }
      window.webkit.messageHandlers.plaudAuthCapture.postMessage({
        url: rawURL,
        headers: headers || {}
      });
    } catch (_) {}
  };
  const headersObject = (headers) => {
    const out = {};
    if (!headers) { return out; }
    try {
      if (headers instanceof Headers) {
        headers.forEach((value, key) => { out[String(key).toLowerCase()] = String(value); });
        return out;
      }
      if (Array.isArray(headers)) {
        headers.forEach((pair) => {
          if (pair && pair.length >= 2) { out[String(pair[0]).toLowerCase()] = String(pair[1]); }
        });
        return out;
      }
      Object.keys(headers).forEach((key) => { out[String(key).toLowerCase()] = String(headers[key]); });
    } catch (_) {}
    return out;
  };
  const originalFetch = window.fetch;
  window.fetch = function(input, init) {
    const inputHeaders = input && input.headers ? headersObject(input.headers) : {};
    const initHeaders = init && init.headers ? headersObject(init.headers) : {};
    const url = typeof input === "string" ? input : input && input.url;
    post(url, Object.assign({}, inputHeaders, initHeaders));
    return originalFetch.apply(this, arguments);
  };
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__plaudAuthURL = url;
    this.__plaudAuthHeaders = {};
    return originalOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function(key, value) {
    this.__plaudAuthHeaders[String(key).toLowerCase()] = String(value);
    return originalSetRequestHeader.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function() {
    post(this.__plaudAuthURL, this.__plaudAuthHeaders);
    return originalSend.apply(this, arguments);
  };
})();
"""

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
        context.coordinator.webView = webView
        webView.load(URLRequest(url: URL(string: "https://web.plaud.ai/")!))
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        var latestHeaders: [String: String] = [:]
        var latestURL: URL?
        var isEmitting = false
        var didCapture = false
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
            onStatus("Log in; waiting for Plaud API traffic.")
            emitIfComplete()
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            onStatus("Plaud Web failed to load: \(error.localizedDescription)")
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
                onStatus("Log in; waiting for Plaud auth headers.")
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
                    guard !cookieLine.isEmpty else {
                        self.isEmitting = false
                        self.onStatus("Captured headers; waiting for Plaud cookies.")
                        return
                    }

                    self.didCapture = true
                    self.isEmitting = false
                    self.onStatus("Captured Plaud session. Saving locally.")
                    self.onCapture(
                        PlaudWebAuthCapture(
                            authorization: authorization,
                            xDeviceID: deviceID,
                            xPldUser: user,
                            cookie: cookieLine,
                            xPldTag: self.header("x-pld-tag"),
                            baseURL: self.baseURLString(),
                            appLanguage: self.header("app-language"),
                            appPlatform: self.header("app-platform"),
                            editFrom: self.header("edit-from"),
                            origin: self.header("origin"),
                            referer: self.header("referer"),
                            timezone: self.header("timezone")
                        )
                    )
                }
            }
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
