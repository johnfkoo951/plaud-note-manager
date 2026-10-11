import XCTest
@testable import PlaudNoteApp

final class RecordingActivityTests: XCTestCase {
    func testActivitiesAreFileScopedAndConcurrentSlotsAreCounted() {
        let snapshot = RecordingActivity.snapshot(
            dual: ["a"], metadata: ["b"], meeting: [], vault: ["a"],
            transcribing: [], relabeling: [], fetching: [],
            summaryKeys: ["a::claude::m::t", "a::codex::m::t", "ab::codex::m::t", "invalid"]
        )
        XCTAssertEqual(snapshot["a"], ["듀얼 전사·통합 중", "볼트 전송 중", "AI 요약·통합 2개 생성 중"])
        XCTAssertEqual(snapshot["b"], ["메타데이터 생성 중"])
        XCTAssertEqual(snapshot["ab"], ["AI 요약·통합 생성 중"])
        XCTAssertNil(snapshot["invalid"])
    }

    func testNoSpinnerAfterAllProcessesFinish() {
        XCTAssertTrue(RecordingActivity.snapshot(
            dual: [], metadata: [], meeting: [], vault: [], transcribing: [],
            relabeling: [], fetching: [], summaryKeys: []
        ).isEmpty)
    }
}
