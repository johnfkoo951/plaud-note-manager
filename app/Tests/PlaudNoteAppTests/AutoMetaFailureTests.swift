import XCTest
@testable import PlaudNoteApp

final class AutoMetaFailureTests: XCTestCase {
    func testParsesReasonTimeAndAttempts() throws {
        let json = """
            {"title": "t", "classification_llm_status": "failed",
             "auto_meta": {"attempts": 2, "last_attempt": 1760000000,
                           "last_error": "codex CLI failed (exit 1): ERROR: usage limit",
                           "last_error_at": 1760000100}}
            """
        let failure = try XCTUnwrap(AutoMetaFailureVM.parse(metadataJSON: json))
        XCTAssertEqual(failure.reason, "codex CLI failed (exit 1): ERROR: usage limit")
        XCTAssertEqual(failure.at, Date(timeIntervalSince1970: 1_760_000_100))
        XCTAssertEqual(failure.attempts, 2)
        XCTAssertEqual(failure.line, "자동 메타데이터 실패 (2회): codex CLI failed (exit 1): ERROR: usage limit")
    }

    func testNilAfterSuccess() {
        let json = #"{"auto_meta": {"source_hash": "abc", "at": 1760000200, "attempts": 0}}"#
        XCTAssertNil(AutoMetaFailureVM.parse(metadataJSON: json))
    }

    func testNilForMissingOrMalformedJSON() {
        let inputs: [String?] = [
            nil, "", "not json", "[1, 2]", "{}",                 // absent, garbage, old CLI
            #"{"auto_meta": "x"}"#,                               // not an object
            #"{"auto_meta": {"attempts": 1}}"#,                   // no reason recorded
            #"{"auto_meta": {"last_error": 5}}"#,                 // wrong type
            #"{"auto_meta": {"last_error": "  \n "}}"#,           // blank
        ]
        for input in inputs {
            XCTAssertNil(AutoMetaFailureVM.parse(metadataJSON: input), String(describing: input))
        }
    }

    func testMissingAttemptsAndTimeStillShowTheReason() throws {
        let failure = try XCTUnwrap(AutoMetaFailureVM.parse(
            metadataJSON: #"{"auto_meta": {"last_error": "boom", "last_error_at": true}}"#))
        XCTAssertEqual(failure.attempts, 0)
        XCTAssertNil(failure.at, "a boolean is not a timestamp")
        XCTAssertEqual(failure.line, "자동 메타데이터 실패: boom")
    }

    func testReasonIsOneBoundedLine() throws {
        let long = (1...40).map { "line \($0) " + String(repeating: "x", count: 20) }.joined(separator: "\n")
        let json = String(data: try JSONSerialization.data(withJSONObject: [
            "auto_meta": ["last_error": long, "attempts": 1],
        ]), encoding: .utf8)
        let failure = try XCTUnwrap(AutoMetaFailureVM.parse(metadataJSON: json))
        XCTAssertFalse(failure.reason.contains("\n"))
        XCTAssertEqual(failure.reason.count, 400)
        XCTAssertTrue(failure.reason.hasPrefix("line 1 xxxx"))
        XCTAssertTrue(failure.reason.hasSuffix("…"))
    }

    func testFailureDateAcceptsEpochAndISO8601() {
        XCTAssertEqual(FailureText.date(1_760_000_000), Date(timeIntervalSince1970: 1_760_000_000))
        XCTAssertEqual(FailureText.date("2026-10-11T12:00:00Z"),
                       Date(timeIntervalSince1970: 1_791_720_000))
        XCTAssertNotNil(FailureText.date("2026-10-11T12:00:00.250Z"))
        XCTAssertNil(FailureText.date("yesterday"))
        XCTAssertNil(FailureText.date(nil))
    }
}
