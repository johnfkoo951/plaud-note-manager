import GRDB
import XCTest
@testable import PlaudNoteApp

final class RecordingCalendarModelTests: XCTestCase {
    private var seoul: Calendar = {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "Asia/Seoul")!
        cal.firstWeekday = 1  // Sunday
        return cal
    }()

    private func file(_ id: String, _ iso: String, hour: Int = 10,
                      minutes: Double = 30) -> PlaudFileVM {
        let day = CalendarDay(iso: iso)!
        let date = seoul.date(from: DateComponents(year: day.year, month: day.month,
                                                   day: day.day, hour: hour))!
        return PlaudFileVM(row: Row([
            "id": id,
            "start_time": Int64(date.timeIntervalSince1970 * 1000),
            "duration": minutes * 60_000,
        ]))
    }

    func testStatsGroupByLocalDayNotUTC() {
        // 00:30 KST on Aug 29 is still Aug 28 in UTC — it must land on the 29th.
        let files = [file("a", "2026-08-29", hour: 0), file("b", "2026-08-29", hour: 21),
                     file("c", "2026-08-28")]
        let stats = RecordingCalendarModel.stats(for: files, calendar: seoul)
        XCTAssertEqual(stats[CalendarDay(year: 2026, month: 8, day: 29)]?.count, 2)
        XCTAssertEqual(stats[CalendarDay(year: 2026, month: 8, day: 29)]?.totalMs, 3_600_000)
        XCTAssertEqual(stats[CalendarDay(year: 2026, month: 8, day: 28)]?.count, 1)
    }

    func testGridStartsOnFirstWeekdayAndKeepsSixWeeks() {
        // Aug 1 2026 is a Saturday.
        let grid = RecordingCalendarModel.grid(for: CalendarDay(year: 2026, month: 8, day: 1),
                                               calendar: seoul)
        XCTAssertEqual(grid.count, 42)
        XCTAssertEqual(grid.prefix(6).compactMap { $0 }.count, 0)
        XCTAssertEqual(grid[6], CalendarDay(year: 2026, month: 8, day: 1))
        XCTAssertEqual(grid.compactMap { $0 }.count, 31)

        var monday = seoul
        monday.firstWeekday = 2
        let mondayGrid = RecordingCalendarModel.grid(for: CalendarDay(year: 2026, month: 8, day: 1),
                                                     calendar: monday)
        XCTAssertEqual(mondayGrid[5], CalendarDay(year: 2026, month: 8, day: 1))
        XCTAssertEqual(RecordingCalendarModel.weekday(ofColumn: 6, calendar: monday), 1)
    }

    func testMonthJumpListIsNewestFirstWithCounts() {
        let files = [file("a", "2026-08-29"), file("b", "2026-08-01"), file("c", "2025-12-31")]
        let months = RecordingCalendarModel.months(
            in: RecordingCalendarModel.stats(for: files, calendar: seoul))
        XCTAssertEqual(months.map(\.month), [CalendarDay(year: 2026, month: 8, day: 1),
                                             CalendarDay(year: 2025, month: 12, day: 1)])
        XCTAssertEqual(months.map(\.count), [2, 1])
    }

    func testListNarrowsToDayOrMonthAndKeepsOrder() {
        let files = [file("new", "2026-08-29"), file("mid", "2026-08-03"), file("old", "2026-07-30")]
        let month = CalendarDay(year: 2026, month: 8, day: 1)
        XCTAssertEqual(RecordingCalendarModel.files(files, month: month, days: [],
                                                    calendar: seoul).map(\.id), ["new", "mid"])
        XCTAssertEqual(RecordingCalendarModel.files(files, month: month,
                                                    days: [CalendarDay(year: 2026, month: 8, day: 3)],
                                                    calendar: seoul).map(\.id), ["mid"])
        // Picked days win over the month on screen (a ⇧ range can cross months).
        XCTAssertEqual(RecordingCalendarModel.files(files, month: month,
                                                    days: [CalendarDay(year: 2026, month: 7, day: 30),
                                                           CalendarDay(year: 2026, month: 8, day: 29)],
                                                    calendar: seoul).map(\.id), ["new", "old"])
    }

    func testHeatLevelBounds() {
        let busiest = CalendarDayStat(count: 1, totalMs: 3 * 3_600_000)
        XCTAssertEqual(RecordingCalendarModel.heatLevel(CalendarDayStat(), busiest: busiest), 0)
        XCTAssertEqual(RecordingCalendarModel.heatLevel(busiest, busiest: busiest), 4)
        let short = CalendarDayStat(count: 1, totalMs: 60_000)
        XCTAssertGreaterThanOrEqual(RecordingCalendarModel.heatLevel(short, busiest: busiest), 1)
        // Recordings with no duration still show as "something happened".
        XCTAssertEqual(RecordingCalendarModel.heatLevel(CalendarDayStat(count: 2, totalMs: 0),
                                                        busiest: CalendarDayStat()), 1)
    }

    func testDurationLabel() {
        XCTAssertEqual(RecordingCalendarModel.durationLabel(ms: 0), "0m")
        XCTAssertEqual(RecordingCalendarModel.durationLabel(ms: 45 * 60_000), "45m")
        XCTAssertEqual(RecordingCalendarModel.durationLabel(ms: 192 * 60_000), "3h 12m")
    }
}

final class CalendarSelectionTests: XCTestCase {
    private var seoul: Calendar = {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "Asia/Seoul")!
        return cal
    }()
    private func d(_ iso: String) -> CalendarDay { CalendarDay(iso: iso)! }

    func testPlainClickSelectsOneDayAndClickingItAgainClears() {
        var sel = CalendarSelection()
        sel.click(d("2026-08-03"), command: false, shift: false, calendar: seoul)
        sel.click(d("2026-08-05"), command: false, shift: false, calendar: seoul)
        XCTAssertEqual(sel.days, [d("2026-08-05")])
        sel.click(d("2026-08-05"), command: false, shift: false, calendar: seoul)
        XCTAssertTrue(sel.isEmpty)
    }

    func testCommandTogglesDays() {
        var sel = CalendarSelection(day: d("2026-08-03"))
        sel.click(d("2026-08-10"), command: true, shift: false, calendar: seoul)
        sel.click(d("2026-08-17"), command: true, shift: false, calendar: seoul)
        XCTAssertEqual(sel.days, [d("2026-08-03"), d("2026-08-10"), d("2026-08-17")])
        sel.click(d("2026-08-10"), command: true, shift: false, calendar: seoul)
        XCTAssertEqual(sel.days, [d("2026-08-03"), d("2026-08-17")])
        XCTAssertEqual(sel.summary(calendar: seoul), "2일 선택")
    }

    func testShiftSpansFromAnchorAcrossMonthsInEitherDirection() {
        var sel = CalendarSelection(day: d("2026-08-30"))
        sel.click(d("2026-09-02"), command: false, shift: true, calendar: seoul)
        XCTAssertEqual(sel.days, [d("2026-08-30"), d("2026-08-31"), d("2026-09-01"), d("2026-09-02")])
        // Re-spans from the same anchor, replacing the previous range.
        sel.click(d("2026-08-28"), command: false, shift: true, calendar: seoul)
        XCTAssertEqual(sel.days, [d("2026-08-28"), d("2026-08-29"), d("2026-08-30")])
        XCTAssertEqual(sel.anchor, d("2026-08-30"))
    }

    func testCommandShiftAddsRangeToExistingSelection() {
        var sel = CalendarSelection(day: d("2026-08-03"))
        sel.click(d("2026-08-20"), command: true, shift: false, calendar: seoul)
        sel.click(d("2026-08-22"), command: true, shift: true, calendar: seoul)
        XCTAssertEqual(sel.days, [d("2026-08-03"), d("2026-08-20"), d("2026-08-21"), d("2026-08-22")])
    }

    func testShiftWithoutAnchorActsLikeAClick() {
        var sel = CalendarSelection()
        sel.click(d("2026-08-03"), command: false, shift: true, calendar: seoul)
        XCTAssertEqual(sel.days, [d("2026-08-03")])
    }

    func testRangeIsCappedAndSpansLeapDay() {
        XCTAssertEqual(CalendarSelection.span(from: d("2028-02-28"), to: d("2028-03-01"),
                                              calendar: seoul).count, 3)
        XCTAssertEqual(CalendarSelection.span(from: d("2020-01-01"), to: d("2026-01-01"),
                                              calendar: seoul).count,
                       CalendarSelection.maxRangeDays)
    }
}

final class ArchiveVisibilityTests: XCTestCase {
    func testArchivedLeaveWorkingListsButStayInArchiveAndTrash() {
        for sidebar: SidebarItem in [.allFiles, .unfiled, .starred, .folder("f"), .tag("t"),
                                     .tagPrefix("AI")] {
            XCTAssertFalse(ArchiveVisibility.shows(isArchived: true, in: sidebar,
                                                   stageFilter: [], searching: false), "\(sidebar)")
            XCTAssertTrue(ArchiveVisibility.shows(isArchived: false, in: sidebar,
                                                  stageFilter: [], searching: false), "\(sidebar)")
        }
        XCTAssertTrue(ArchiveVisibility.shows(isArchived: true, in: .archive,
                                              stageFilter: [], searching: false))
        XCTAssertFalse(ArchiveVisibility.shows(isArchived: false, in: .archive,
                                               stageFilter: [], searching: false))
        XCTAssertTrue(ArchiveVisibility.shows(isArchived: true, in: .trash,
                                              stageFilter: [], searching: false))
    }

    func testSearchOrExplicitStageFilterBringsArchivedBack() {
        XCTAssertTrue(ArchiveVisibility.shows(isArchived: true, in: .allFiles,
                                              stageFilter: [], searching: true))
        XCTAssertTrue(ArchiveVisibility.shows(isArchived: true, in: .folder("f"),
                                              stageFilter: [.archived, .tracking], searching: false))
        XCTAssertFalse(ArchiveVisibility.shows(isArchived: true, in: .allFiles,
                                               stageFilter: [.tracking], searching: false))
    }

    func testArchivedResolvesThroughUsageStage() {
        XCTAssertTrue(ArchiveVisibility.isArchived("archived"))
        XCTAssertTrue(ArchiveVisibility.isArchived(" archived "))
        XCTAssertFalse(ArchiveVisibility.isArchived(nil))
        XCTAssertFalse(ArchiveVisibility.isArchived("vault-linked"))
    }
}

final class UsageStageFilterTests: XCTestCase {
    private let working: Set<UsageStage> = [.unused, .prepared, .tracking]

    func testTicksMatchWhatThePlaceLists() {
        XCTAssertEqual(UsageStageFilter.ticked([], in: .allFiles), working)
        XCTAssertEqual(UsageStageFilter.ticked([], in: .folder("f")), working)
        XCTAssertEqual(UsageStageFilter.ticked([], in: .trash), UsageStageFilter.all)
        XCTAssertEqual(UsageStageFilter.ticked([], in: .archive), [.archived])
        XCTAssertEqual(UsageStageFilter.ticked([.tracking], in: .allFiles), [.tracking])
    }

    func testAllStagesShowArchivedInWorkingLists() {
        // The bug: "전체" stored as [] still hid 사용 완료.
        let all = UsageStageFilter.all
        XCTAssertTrue(UsageStage.matches("archived", selection: all))
        XCTAssertTrue(ArchiveVisibility.shows(isArchived: true, in: .allFiles,
                                              stageFilter: all, searching: false))
        XCTAssertEqual(UsageStageFilter.label(all, in: .allFiles), "전체")
        XCTAssertEqual(UsageStageFilter.label([], in: .allFiles), "전체 · 사용 완료 제외")
        XCTAssertEqual(UsageStageFilter.label([], in: .trash), "전체")
    }

    func testTickingArchivedFromTheDefaultSelectsAllFour() {
        let next = UsageStageFilter.toggling(.archived, on: true, stored: [], in: .allFiles)
        XCTAssertEqual(next, UsageStageFilter.all)
        XCTAssertTrue(UsageStageFilter.isActive(next, in: .allFiles))
        // Unticking it again lands on the default, which clears the filter.
        XCTAssertEqual(UsageStageFilter.toggling(.archived, on: false, stored: next, in: .allFiles), [])
    }

    func testUntickingFromTheDefaultNarrows() {
        XCTAssertEqual(UsageStageFilter.toggling(.unused, on: false, stored: [], in: .allFiles),
                       [.prepared, .tracking])
        XCTAssertEqual(UsageStageFilter.toggling(.tracking, on: false, stored: [.tracking],
                                                 in: .allFiles), [])
        XCTAssertFalse(UsageStageFilter.isActive(UsageStageFilter.all, in: .trash))
    }
}
