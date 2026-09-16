import XCTest
@testable import PlaudNoteApp

final class MarkdownDocumentTests: XCTestCase {
    func testPreservesStructureAndParsesInlineFormattingOnce() {
        let document = MarkdownDocument(text: "## Summary\n- **Decision**\n> quoted\n\n---")
        XCTAssertEqual(document.blocks.map(\.id), [0, 1, 2, 3, 4])
        guard case .heading(let level, let heading) = document.blocks[0].kind,
              case .bullet(let bullet) = document.blocks[1].kind,
              case .quote(let quote) = document.blocks[2].kind,
              case .blank = document.blocks[3].kind,
              case .rule = document.blocks[4].kind else {
            return XCTFail("Markdown block structure changed")
        }
        XCTAssertEqual(level, 2)
        XCTAssertEqual(String(heading.characters), "Summary")
        XCTAssertEqual(String(bullet.characters), "Decision")
        XCTAssertTrue(bullet.runs.contains { $0.inlinePresentationIntent?.contains(.stronglyEmphasized) == true })
        XCTAssertEqual(String(quote.characters), "quoted")
    }

    func testCodeRemainsLiteralIncludingUnclosedFence() {
        let document = MarkdownDocument(text: "```swift\n# literal\n  **code**\n```\n```\nunfinished")
        guard case .code(let first) = document.blocks[0].kind,
              case .code(let second) = document.blocks[1].kind else {
            return XCTFail("Code fences must remain literal")
        }
        XCTAssertEqual(first, "# literal\n  **code**")
        XCTAssertEqual(second, "unfinished")
    }

    func testKoreanRangesAndSingleTildesRemainVisible() {
        let document = MarkdownDocument(text: "회의 3~5시, 약 10~20명 ~~취소~~")
        guard case .paragraph(let paragraph) = document.blocks[0].kind else {
            return XCTFail("Expected paragraph")
        }
        XCTAssertEqual(String(paragraph.characters), "회의 3~5시, 약 10~20명 취소")
        XCTAssertEqual(document.source, "회의 3~5시, 약 10~20명 ~~취소~~")
    }
}
