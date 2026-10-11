import XCTest
@testable import PlaudNoteApp

final class ClassifyPlanDecodingTests: XCTestCase {
    private func plans(_ json: String) throws -> [FileStore.ClassifyPlan] {
        try JSONDecoder().decode([FileStore.ClassifyPlan].self, from: Data(json.utf8))
    }

    private func plan(status: String, error: String = "") throws -> FileStore.ClassifyPlan {
        try plans(#"[{"file_id": "f", "llm_status": "\#(status)", "llm_error": "\#(error)"}]"#)[0]
    }

    func testDecodesLLMStatusAndError() throws {
        let row = try plans("""
            [{"file_id": "f1", "title": "t", "folder_name": "", "confidence": 0.3, "reason": "weak",
              "source": "rule", "llm_status": "failed",
              "llm_error": "codex CLI failed (exit 1): ERROR: usage limit"}]
            """)[0]
        XCTAssertEqual(row.llmStatus, "failed")
        XCTAssertEqual(row.llmError, "codex CLI failed (exit 1): ERROR: usage limit")
        XCTAssertTrue(row.llmFailed)
    }

    func testOldCLIOutputDefaultsToOff() throws {
        let row = try plans("""
            [{"file_id": "f1", "title": "t", "folder_name": "01. Work", "confidence": 0.8,
              "reason": "rule", "source": "rule", "alternatives": []}]
            """)[0]
        XCTAssertEqual(row.llmStatus, "off")
        XCTAssertEqual(row.llmError, "")
        XCTAssertFalse(row.llmFailed)
        XCTAssertEqual(row.folderName, "01. Work")
    }

    func testWrongTypedLLMKeysFallBackInsteadOfThrowing() throws {
        let row = try plans(#"[{"file_id": "f1", "llm_status": 3, "llm_error": null}]"#)[0]
        XCTAssertEqual(row.llmStatus, "off")
        XCTAssertEqual(row.llmError, "")
    }

    func testLLMFailedTruthTable() throws {
        for status in ["failed", "unavailable", "skipped"] {
            XCTAssertTrue(try plan(status: status).llmFailed, status)
        }
        for status in ["off", "ok", "rejected", "something-new"] {
            XCTAssertFalse(try plan(status: status).llmFailed, status)
        }
    }

    func testNoticeCountsFailedUnavailableAndSkipped() throws {
        let rows = [
            try plan(status: "ok"),
            try plan(status: "failed", error: "usage limit"),
            try plan(status: "skipped", error: "not attempted, earlier call failed: usage limit"),
            try plan(status: "rejected"),
        ]
        XCTAssertEqual(ClassifyLLMNotice.line(for: rows), "LLM 보조 실패 2건 — usage limit")
    }

    func testNoticeTakesTheFirstNonEmptyReason() throws {
        let rows = [try plan(status: "unavailable"), try plan(status: "failed", error: "quota")]
        XCTAssertEqual(ClassifyLLMNotice.line(for: rows), "LLM 보조 실패 2건 — quota")
        XCTAssertEqual(ClassifyLLMNotice.line(for: [try plan(status: "unavailable")]), "LLM 보조 실패 1건")
    }

    func testNoNoticeWhenNothingFailed() throws {
        XCTAssertNil(ClassifyLLMNotice.line(for: [try plan(status: "ok"), try plan(status: "off"),
                                                  try plan(status: "rejected")]))
        XCTAssertNil(ClassifyLLMNotice.line(for: []))
    }

    func testNoticeReasonIsOneLine() throws {
        let rows = [try plan(status: "failed", error: "line one\\nline two")]
        XCTAssertEqual(ClassifyLLMNotice.line(for: rows), "LLM 보조 실패 1건 — line one line two")
    }
}
