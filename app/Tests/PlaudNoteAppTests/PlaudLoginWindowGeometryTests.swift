import XCTest
@testable import PlaudNoteApp

final class PlaudLoginWindowGeometryTests: XCTestCase {
    func testLaptopUsesAvailableHeightForBrowser() {
        let screen = NSRect(x: 0, y: 25, width: 1608, height: 829)
        let frame = PlaudLoginWindowGeometry.fittedFrame(nil, visibleFrame: screen)
        XCTAssertEqual(frame.width, 1120)
        XCTAssertEqual(frame.height, 805)
        XCTAssertTrue(screen.contains(frame))
    }

    func testDisconnectedMonitorFrameComesBackOnScreen() {
        let screen = NSRect(x: 0, y: 25, width: 1280, height: 695)
        let old = NSRect(x: 2500, y: 700, width: 1600, height: 1100)
        let frame = PlaudLoginWindowGeometry.fittedFrame(old, visibleFrame: screen)
        XCTAssertTrue(screen.contains(frame))
        XCTAssertLessThanOrEqual(frame.height, screen.height - 24)
    }

    func testResizedWindowOnNegativeOriginDisplayIsPreserved() {
        let screen = NSRect(x: -1920, y: 0, width: 1920, height: 1080)
        let saved = NSRect(x: -1600, y: 100, width: 1000, height: 800)
        XCTAssertEqual(PlaudLoginWindowGeometry.fittedFrame(saved, visibleFrame: screen), saved)
    }
}
