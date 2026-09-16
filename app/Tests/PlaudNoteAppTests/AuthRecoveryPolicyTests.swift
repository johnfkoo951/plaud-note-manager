import XCTest
@testable import PlaudNoteApp

final class AuthRecoveryPolicyTests: XCTestCase {
    private func status(_ state: String, renewal: String) -> AuthStatus {
        var result = AuthStatus.unknown(detail: "")
        result.configured = true
        result.state = state
        result.autoRefresh = renewal
        return result
    }

    func testAccessOnlyCurlAttemptsRenewalBeforeExpiryWithoutForcingLogin() {
        let value = status("valid", renewal: "not_bootstrapped")
        XCTAssertTrue(AuthRecoveryPolicy.shouldAttempt(value))
        XCTAssertFalse(AuthRecoveryPolicy.needsInteractiveLogin(value))
    }

    func testRejectedAccessMayStillUseAReadyRenewalToken() {
        let value = status("rejected", renewal: "ready")
        XCTAssertTrue(AuthRecoveryPolicy.shouldAttempt(value))
        XCTAssertTrue(value.autoRefreshReady)
    }

    func testHealthyOrUnavailableStoreDoesNotTriggerWebLogin() {
        XCTAssertFalse(AuthRecoveryPolicy.shouldAttempt(status("valid", renewal: "ready")))
        XCTAssertFalse(AuthRecoveryPolicy.shouldAttempt(status("valid", renewal: "store_unavailable")))
        XCTAssertFalse(AuthRecoveryPolicy.needsInteractiveLogin(.unknown(detail: "offline")))
        XCTAssertFalse(AuthRecoveryPolicy.needsInteractiveLogin(nil))
    }

    func testOnlyUnusableAccessRequiresInteractiveLoginAfterMissingSession() {
        XCTAssertTrue(AuthRecoveryPolicy.needsInteractiveLogin(status("expired", renewal: "expired")))
        XCTAssertFalse(AuthRecoveryPolicy.needsInteractiveLogin(status("expiring", renewal: "expired")))
    }

    func testPreviouslyMissingSessionPromptsOnceWhenSameAccessExpires() {
        var value = status("valid", renewal: "not_bootstrapped")
        value.issuedAt = 123
        XCTAssertFalse(AuthRecoveryPolicy.shouldPromptForMissingSession(
            value, missingGeneration: 123, promptedGeneration: nil))
        value.state = "expired"
        XCTAssertTrue(AuthRecoveryPolicy.shouldPromptForMissingSession(
            value, missingGeneration: 123, promptedGeneration: nil))
        XCTAssertFalse(AuthRecoveryPolicy.shouldPromptForMissingSession(
            value, missingGeneration: 123, promptedGeneration: 123))
        XCTAssertFalse(AuthRecoveryPolicy.shouldPromptForMissingSession(
            value, missingGeneration: 122, promptedGeneration: nil))
    }
}
