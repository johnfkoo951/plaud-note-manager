import XCTest
@testable import PlaudNoteApp

final class SlotOutputSnapshotTests: XCTestCase {
    func testPreviousRecordingOutputIsUnavailableAfterSelectionChanges() {
        let snapshot = SlotOutputSnapshot(fileID: "previous-recording", outputs: [
            "slot-1": .init(summary: "private previous summary", integrated: [.all: "previous integration"])
        ])
        XCTAssertEqual(snapshot.output(for: "slot-1", selectedID: "previous-recording")?.summary,
                       "private previous summary")
        XCTAssertNil(snapshot.output(for: "slot-1", selectedID: "next-recording"))
        XCTAssertNil(snapshot.output(for: "slot-1", selectedID: nil))
    }

    func testChangedSlotDoesNotReuseAnotherSlotOutput() {
        let snapshot = SlotOutputSnapshot(fileID: "recording", outputs: [
            "old-slot": .init(summary: "old", integrated: [:])
        ])
        XCTAssertNil(snapshot.output(for: "new-slot", selectedID: "recording"))
        XCTAssertFalse(snapshot.output(for: "old-slot", selectedID: "recording")!.hasIntegrated)
    }
}
