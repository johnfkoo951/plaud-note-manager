import XCTest
@testable import PlaudNoteApp

final class AuthIndicatorStatusTests: XCTestCase {
    func testValidAccessWithoutRenewalShowsWarningWithoutInvalidatingLogin() {
        let status = AuthIndicatorStatus(accessState: "valid", autoRefresh: "not_bootstrapped",
                                         liveState: nil, liveOK: nil)
        XCTAssertTrue(status.usable)
        XCTAssertTrue(status.hasWarning)
        XCTAssertEqual(status.accessLabel, "사용 가능")
        XCTAssertEqual(status.renewalWarning, "자동 갱신 미연결")
        XCTAssertEqual(status.compactSuffix, "갱신 미연결")
    }

    func testNetworkFailureTakesPrecedenceOverLegacyFalseLiveFlag() {
        let status = AuthIndicatorStatus(accessState: "valid", autoRefresh: "ready",
                                         liveState: "unreachable", liveOK: false)
        XCTAssertTrue(status.usable)
        XCTAssertEqual(status.liveLabel, "네트워크 연결 불가")
        XCTAssertEqual(status.compactSuffix, "연결 확인")
        XCTAssertTrue(status.hasWarning)
    }

    func testExplicitRejectionAndHealthyRenewalRemainDistinct() {
        let rejected = AuthIndicatorStatus(accessState: "rejected", autoRefresh: "ready",
                                           liveState: "rejected", liveOK: false)
        XCTAssertFalse(rejected.usable)
        XCTAssertEqual(rejected.liveLabel, "인증 거부됨")
        let healthy = AuthIndicatorStatus(accessState: "valid", autoRefresh: "ready",
                                          liveState: "ok", liveOK: true)
        XCTAssertFalse(healthy.hasWarning)
        XCTAssertNil(healthy.compactSuffix)
    }
}
