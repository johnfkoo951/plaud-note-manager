import XCTest
@testable import PlaudNoteApp

final class RecordingLinkTests: XCTestCase {
    private let id = "0f3c9a1b2d4e5f60718293a4b5c6d7e8"

    func testRecordingLinkRoundTrips() {
        let url = RecordingLink.url(forRecording: id)
        XCTAssertEqual(url.absoluteString, "plaudnote://open?id=\(id)")
        XCTAssertEqual(RecordingLink.parse(url), .recording(id: id))
    }

    func testDayLinkRoundTrips() {
        let day = CalendarDay(year: 2026, month: 8, day: 29)
        let url = RecordingLink.url(forDay: day)
        XCTAssertEqual(url.absoluteString, "plaudnote://day?date=2026-08-29")
        XCTAssertEqual(RecordingLink.parse(url), .day(day))
    }

    func testPathFormsAndSchemeCaseAreAccepted() {
        XCTAssertEqual(RecordingLink.parse(URL(string: "plaudnote://recording/\(id)")!),
                       .recording(id: id))
        XCTAssertEqual(RecordingLink.parse(URL(string: "PlaudNote://open?id=\(id)")!),
                       .recording(id: id))
        XCTAssertEqual(RecordingLink.parse(URL(string: "plaudnote://day/2026-01-05")!),
                       .day(CalendarDay(year: 2026, month: 1, day: 5)))
    }

    func testMalformedLinksAreRejected() {
        for raw in [
            "https://web.plaud.ai/file/\(id)",       // wrong scheme
            "plaudnote://open",                      // no id
            "plaudnote://open?id=a%20b",             // space in id
            "plaudnote://open?id=..%2Fetc",          // path characters
            "plaudnote://settings",                  // unknown host
            "plaudnote://day?date=2026-02-30",       // not a real date
            "plaudnote://day?date=26-8-29",          // not ISO
        ] {
            XCTAssertNil(RecordingLink.parse(URL(string: raw)!), raw)
        }
    }

    func testMarkdownEscapesBracketsAndFallsBackOnEmptyTitle() {
        XCTAssertEqual(
            RecordingLink.markdown(title: "08-29 [초안] 신앙 공동체", id: id),
            "[08-29 \\[초안\\] 신앙 공동체](plaudnote://open?id=\(id))"
        )
        XCTAssertEqual(RecordingLink.markdown(title: "  ", id: id),
                       "[Plaud recording](plaudnote://open?id=\(id))")
    }

    func testHTMLEscapesMarkup() {
        XCTAssertEqual(RecordingLink.html(title: "A&B <draft>", id: id),
                       "<a href=\"plaudnote://open?id=\(id)\">A&amp;B &lt;draft&gt;</a>")
    }

    func testCalendarDayLeapYearAndMonthPaging() {
        XCTAssertNotNil(CalendarDay(iso: "2028-02-29"))
        XCTAssertNil(CalendarDay(iso: "2026-02-29"))
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "Asia/Seoul")!
        let jan = CalendarDay(year: 2026, month: 1, day: 31)
        XCTAssertEqual(jan.addingMonths(1, calendar: cal), CalendarDay(year: 2026, month: 2, day: 1))
        XCTAssertEqual(jan.addingMonths(-1, calendar: cal), CalendarDay(year: 2025, month: 12, day: 1))
    }
}
