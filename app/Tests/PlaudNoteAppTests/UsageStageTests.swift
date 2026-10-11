import XCTest
@testable import PlaudNoteApp

final class UsageStageTests: XCTestCase {
    func testMenuReselectionPreservesStoredTrackingRepresentation() {
        XCTAssertNil(UsageStage.tracking.updateValue(current: "used-elsewhere"))
        XCTAssertNil(UsageStage.tracking.updateValue(current: "vault-linked"))
        XCTAssertNil(UsageStage.unused.updateValue(current: nil))
        XCTAssertEqual(UsageStage.tracking.updateValue(current: "metadata-ready"), "vault-linked")
        XCTAssertEqual(UsageStage.archived.updateValue(current: "used-elsewhere"), "archived")
        XCTAssertEqual(UsageStage.unused.updateValue(current: "archived"), "unused")
    }

    func testTrackingGroupsBothStoredValues() {
        XCTAssertEqual(UsageStage.resolve("vault-linked"), .tracking)
        XCTAssertEqual(UsageStage.resolve("used-elsewhere"), .tracking)
        XCTAssertEqual(UsageStage.tracking.dbValue, "vault-linked")
    }

    func testMissingMetadataIsUnusedAndUnknownIsNotMisclassified() {
        XCTAssertEqual(UsageStage.resolve(nil), .unused)
        XCTAssertEqual(UsageStage.resolve("  "), .unused)
        XCTAssertNil(UsageStage.resolve("future-state"))
    }

    func testMultiSelectUsesUnionAndEmptySelectionIncludesEverything() {
        let selection: Set<UsageStage> = [.prepared, .tracking]
        for raw in ["metadata-ready", "vault-linked", "used-elsewhere"] {
            XCTAssertTrue(UsageStage.matches(raw, selection: selection))
        }
        for raw in ["unused", "archived", "future-state"] {
            XCTAssertFalse(UsageStage.matches(raw, selection: selection))
            XCTAssertTrue(UsageStage.matches(raw, selection: []))
        }
        XCTAssertTrue(UsageStage.matches(nil, selection: [.unused]))
        XCTAssertFalse(UsageStage.matches(nil, selection: selection))
    }
}
