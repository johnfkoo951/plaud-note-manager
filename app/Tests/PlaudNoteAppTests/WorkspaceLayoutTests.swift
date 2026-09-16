import XCTest
@testable import PlaudNoteApp

final class WorkspaceLayoutTests: XCTestCase {
    func testReferenceWindowLeavesAbout550PointsForRecordingDetail() {
        let outer = WorkspaceLayout.outerWidths(available: 1606, sidebar: 280, library: 420)
        let detailAndWork = 1606 - outer.sidebar - outer.library - 1
        let work = WorkspaceLayout.workWidth(available: detailAndWork, preferred: 350)
        XCTAssertEqual(outer.sidebar, 280)
        XCTAssertEqual(outer.library, 420)
        XCTAssertEqual(work, 350)
        XCTAssertEqual(detailAndWork - work, 555)
    }

    func testSmallWindowKeepsAllFourPanesUsable() {
        let outer = WorkspaceLayout.outerWidths(available: 1278, sidebar: 280, library: 420)
        let detailAndWork = 1278 - outer.sidebar - outer.library - 1
        let work = WorkspaceLayout.workWidth(available: detailAndWork, preferred: 350)
        XCTAssertGreaterThanOrEqual(outer.sidebar, WorkspaceLayout.sidebarRange.lowerBound)
        XCTAssertGreaterThanOrEqual(outer.library, WorkspaceLayout.libraryRange.lowerBound)
        XCTAssertGreaterThanOrEqual(work, WorkspaceLayout.workRange.lowerBound)
        XCTAssertGreaterThanOrEqual(detailAndWork - work, WorkspaceLayout.detailMinimum)
    }

    func testSavedDividerChoicesSurviveWhenSpaceAllows() {
        let outer = WorkspaceLayout.outerWidths(available: 1800, sidebar: 310, library: 480)
        XCTAssertEqual(outer.sidebar, 310)
        XCTAssertEqual(outer.library, 480)
        XCTAssertEqual(WorkspaceLayout.workWidth(available: 1009, preferred: 390), 390)
    }

    func testInvalidOrOversizedPreferenceDoesNotBreakLayout() {
        let outer = WorkspaceLayout.outerWidths(available: 1800, sidebar: .nan, library: 9000)
        XCTAssertEqual(outer.sidebar, 280)
        XCTAssertEqual(outer.library, 600)
        XCTAssertEqual(WorkspaceLayout.workWidth(available: 905, preferred: -5), 350)
        XCTAssertEqual(WorkspaceLayout.workWidth(available: 720, preferred: 520), 320)
    }
}
