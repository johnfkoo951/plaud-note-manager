import XCTest
@testable import PlaudNoteApp

final class VaultSendOutcomeTests: XCTestCase {
    private func decode(_ json: String) throws -> VaultSendOutcome {
        try JSONDecoder().decode(VaultSendOutcome.self, from: Data(json.utf8))
    }

    func testDecodesDuplicatePayload() throws {
        let outcome = try decode("""
            {"status": "duplicate",
             "detail": "already in the vault: 20260914_x.md — choose --on-duplicate skip|overwrite|new",
             "path": "/synthetic/vaults/Main/00. Inbox/20260914_x.md", "vault": "/synthetic/vaults/Main",
             "dest": "00. Inbox", "content": "integrated", "via": "direct",
             "obsidian_url": "obsidian://open?file=x", "tags": [], "action": "",
             "existing": [
               {"path": "/synthetic/vaults/Main/00. Inbox/20260914_x.md", "updated_at": 1760000100,
                "modified_after_send": true, "obsidian_url": "obsidian://open?file=x"},
               {"path": "/synthetic/vaults/Main/63. Meetings/20260914_x.md", "updated_at": 1760000000,
                "modified_after_send": false, "obsidian_url": "obsidian://open?file=y"}]}
            """)
        XCTAssertEqual(outcome.status, "duplicate")
        XCTAssertEqual(outcome.existing?.count, 2)
        XCTAssertEqual(outcome.existing?.first?.modifiedAfterSend, true)
        XCTAssertEqual(outcome.existing?.first?.updatedAt, 1_760_000_100)
        XCTAssertEqual(outcome.existing?.first?.filename, "20260914_x.md")
        XCTAssertEqual(outcome.existing?.last?.obsidianURL, "obsidian://open?file=y")
    }

    func testDuplicateIsNotAnErrorForSendOrCompose() throws {
        let duplicate = try decode(#"{"status": "duplicate", "detail": "already in the vault"}"#)
        XCTAssertNil(duplicate.sendFailureMessage, "the dialog answers it, not the alert")
        XCTAssertNil(VaultComposeOutcome.errorMessage(status: "duplicate", detail: "already in the vault"))
        XCTAssertFalse(duplicate.showsFeedback(selectedID: ""))
    }

    func testSendFailureMessages() throws {
        XCTAssertNil(try decode(#"{"status": "ok"}"#).sendFailureMessage)
        XCTAssertTrue(try decode(#"{"status": "no_content"}"#).sendFailureMessage!.hasPrefix("보낼 결과물이 없습니다"))
        XCTAssertTrue(try decode(#"{"status": "no_vault"}"#).sendFailureMessage!.hasPrefix("볼트가 설정되지 않았습니다"))
        XCTAssertEqual(try decode(#"{"status": "wiki_unsupported", "detail": " use vault-compose "}"#)
                        .sendFailureMessage,
                       "볼트 전송에 실패했습니다 (wiki_unsupported) — use vault-compose")
    }

    func testOkPayloadsWithoutOrWithEmptyExistingDecode() throws {
        let created = try decode(#"{"status": "ok", "path": "/synthetic/n.md", "action": "created", "existing": []}"#)
        XCTAssertEqual(created.existing, [])
        XCTAssertNil(created.feedbackLabel)

        // claude-window and older CLIs answer without action/existing.
        let window = try decode(#"{"status": "ok", "via": "claude-window", "detail": "Claude Code launched"}"#)
        XCTAssertNil(window.action)
        XCTAssertNil(window.existing)
    }

    func testFeedbackLabelForSkippedAndOverwritten() {
        var outcome = VaultSendOutcome(status: "ok", path: "/synthetic/n.md")
        outcome.action = "overwritten"
        XCTAssertEqual(outcome.feedbackLabel, "덮어씀")
        outcome.action = "skipped"
        XCTAssertEqual(outcome.feedbackLabel, "이미 있음")
        outcome.action = "created"
        XCTAssertNil(outcome.feedbackLabel)
    }
}
