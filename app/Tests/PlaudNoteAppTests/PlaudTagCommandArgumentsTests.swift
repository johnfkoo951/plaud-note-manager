import XCTest
@testable import PlaudNoteApp

final class PlaudTagCommandArgumentsTests: XCTestCase {
    func testAddTreatsLeadingHyphenTagAsPositionalData() {
        XCTAssertEqual(
            PlaudTagCommandArguments.add(fileID: "file-1", tag: "--topic"),
            ["tag-add", "file-1", "--", "--topic"]
        )
    }

    func testRemoveTreatsLeadingHyphenTagAsPositionalData() {
        XCTAssertEqual(
            PlaudTagCommandArguments.remove(fileID: "file-1", tag: "-topic"),
            ["tag-remove", "file-1", "--", "-topic"]
        )
    }
}
