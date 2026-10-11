import XCTest
@testable import PlaudNoteApp

final class IntegrateFailureTests: XCTestCase {
    func testParsesSidecar() throws {
        let data = Data("""
            {"at": 1760000000, "provider": "claude", "model": "claude-fable-5",
             "template": "note-meeting", "reason": "claude CLI failed (exit 1): usage limit"}
            """.utf8)
        let failure = try XCTUnwrap(IntegrateFailureVM.parse(data))
        XCTAssertEqual(failure.reason, "claude CLI failed (exit 1): usage limit")
        XCTAssertEqual(failure.at, Date(timeIntervalSince1970: 1_760_000_000))
        XCTAssertEqual(failure.line, "마지막 통합 실패: claude CLI failed (exit 1): usage limit")
    }

    func testReasonIsShownOnOneLine() throws {
        let failure = try XCTUnwrap(IntegrateFailureVM.parse(
            Data(#"{"reason": "Traceback\n  ModelFailed: quota\n"}"#.utf8)))
        XCTAssertEqual(failure.reason, "Traceback ModelFailed: quota")
        XCTAssertNil(failure.at)
    }

    func testNilForGarbage() {
        for raw in ["", "nope", "[]", "{}", #"{"reason": 3}"#, #"{"reason": "   "}"#] {
            XCTAssertNil(IntegrateFailureVM.parse(Data(raw.utf8)), raw)
        }
    }

    func testSidecarStemMatchesTheIntegratedFileStem() {
        // Same rule as core's _safe: letters, digits, '-' and '_' kept; anything else '_'.
        XCTAssertEqual(Database.integratedStem(model: "gpt-5.6-sol", template: "note-meeting"),
                       "gpt-5_6-sol__note-meeting")
        XCTAssertEqual(Database.integratedFailureFileName(model: "gpt-5.6-sol", template: "note-meeting"),
                       "gpt-5_6-sol__note-meeting.error.json")
        XCTAssertEqual(Database.integratedFailureFileName(model: "claude fable/5", template: "회의 노트"),
                       "claude_fable_5__회의_노트.error.json")
        // The sidecar does not end in .md, so integratedAnyExists and the
        // integrated readers never mistake it for an output.
        XCTAssertFalse(Database.integratedFailureFileName(model: "m", template: "t").hasSuffix(".md"))
    }
}
