import XCTest
@testable import PlaudNoteApp

final class PlaudWebCapturePolicyTests: XCTestCase {
    private func authorization(expiry: Int = 4_102_444_800, wid: String? = "workspace", sub: String = "account") throws -> String {
        var claims: [String: Any] = ["sub": sub, "exp": expiry]
        claims["wid"] = wid
        let payload = try JSONSerialization.data(withJSONObject: claims).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        return "bearer header.\(payload).signature"
    }

    func testExpiredAndAccountOnlyHeadersCannotLatchWorkspaceCapture() throws {
        XCTAssertNil(PlaudWebCapturePolicy.workspaceClaims(authorization: try authorization(expiry: 1000), now: 1001))
        XCTAssertNil(PlaudWebCapturePolicy.workspaceClaims(authorization: try authorization(wid: nil), now: 1001))
        XCTAssertNotNil(PlaudWebCapturePolicy.workspaceClaims(authorization: try authorization(expiry: 1002), now: 1001))
    }

    func testFreshLoginCanReplaceAssemblyButOlderTrafficCannotReplaceFreshLogin() throws {
        let older = try XCTUnwrap(PlaudWebCapturePolicy.workspaceClaims(authorization: authorization(expiry: 2000), now: 1000))
        let newer = try XCTUnwrap(PlaudWebCapturePolicy.workspaceClaims(authorization: authorization(expiry: 3000), now: 1000))
        XCTAssertTrue(PlaudWebCapturePolicy.canSupersede(current: older, candidate: newer))
        XCTAssertFalse(PlaudWebCapturePolicy.canSupersede(current: newer, candidate: older))
    }

    func testWorkspaceRefreshMustBelongToExactCapturedAccessToken() throws {
        let captured = try authorization()
        let newer = try authorization(expiry: 4_102_448_400)
        func list(_ token: String, wid: String = "workspace") throws -> String {
            let entry = [["workspaceId": wid, "workspaceToken": PlaudWebCapturePolicy.bareToken(token),
                          "refreshToken": "synthetic-refresh"]]
            return String(decoding: try JSONSerialization.data(withJSONObject: entry), as: UTF8.self)
        }
        XCTAssertTrue(PlaudWebCapturePolicy.workspaceListMatches(try list(captured), authorization: captured))
        XCTAssertFalse(PlaudWebCapturePolicy.workspaceListMatches(try list(newer), authorization: captured))
        XCTAssertFalse(PlaudWebCapturePolicy.workspaceListMatches(try list(captured, wid: "other"), authorization: captured))
        XCTAssertFalse(PlaudWebCapturePolicy.workspaceListMatches("[]", authorization: captured))
    }

    func testGooglePasskeyFailureHasProviderSpecificStatusWithoutLeakingQuery() {
        let status = PlaudLoginNavigationStatus.classify(URL(string: "https://accounts.google.com/v3/signin/challenge/pk/error?token=synthetic-sensitive"))
        XCTAssertEqual(status, .googlePasskeyError)
        XCTAssertTrue(status.message.contains("Google 패스키"))
        XCTAssertFalse(status.message.contains("synthetic-sensitive"))
        XCTAssertEqual(PlaudLoginNavigationStatus.classify(URL(string: "https://accounts.google.com/v3/signin/challenge/pk")), .googlePasskey)
        XCTAssertEqual(PlaudLoginNavigationStatus.classify(URL(string: "https://accounts.google.com/signin?error=disallowed_useragent")), .googleUnsupported)
        XCTAssertEqual(PlaudLoginNavigationStatus.classify(URL(string: "https://web.plaud.ai/")), .plaud)
        XCTAssertEqual(PlaudLoginNavigationStatus.classify(URL(string: "https://accounts.google.com.evil.invalid/challenge/pk/error")), .other)
    }

    func testRecoveryOnlyAcceptsExplicitFreshPairMessage() {
        XCTAssertFalse(PlaudWebCapturePolicy.accepts(kind: "capture", recoveryMode: true))
        XCTAssertFalse(PlaudWebCapturePolicy.accepts(kind: nil, recoveryMode: true))
        XCTAssertTrue(PlaudWebCapturePolicy.accepts(kind: "recoveryCapture", recoveryMode: true))
        XCTAssertTrue(PlaudWebCapturePolicy.accepts(kind: "capture", recoveryMode: false))
    }

    func testResetAndDisposeRejectOldAsyncCallbacks() {
        var epoch = PlaudWebCaptureEpoch()
        let old = epoch.id
        XCTAssertTrue(epoch.accepts(old))
        epoch.reset()
        XCTAssertFalse(epoch.accepts(old))
        let current = epoch.id
        XCTAssertTrue(epoch.accepts(current))
        epoch.dispose()
        XCTAssertFalse(epoch.accepts(current))
        XCTAssertFalse(epoch.accepts(epoch.id))
    }

    func testCookieDomainUsesHostBoundary() {
        for domain in ["plaud.ai", ".plaud.ai", "web.plaud.ai", "api-apne1.plaud.ai"] {
            XCTAssertTrue(PlaudWebCapturePolicy.trustedCookieDomain(domain), domain)
        }
        for domain in ["notplaud.ai", "plaud.ai.evil.invalid", "fakeplaud.ai"] {
            XCTAssertFalse(PlaudWebCapturePolicy.trustedCookieDomain(domain), domain)
        }
    }

    func testCapturedRequestCannotSetNonPlaudOrInsecureBaseURL() {
        XCTAssertNotNil(PlaudWebCapturePolicy.trustedAPIURL("https://api-apne1.plaud.ai/file/list"))
        for url in ["http://api.plaud.ai/list", "https://api.plaud.ai.evil.invalid/list",
                    "https://api.plaud.ai@evil.invalid/list", "https://api.plaud.ai:444/list",
                    "https://api-evil.other.plaud.ai/list", "https://web.plaud.ai/list"] {
            XCTAssertNil(PlaudWebCapturePolicy.trustedAPIURL(url), url)
        }
    }
}
