import Foundation

/// A calendar date with no time or time zone — the unit the recording
/// calendar groups by and the `plaudnote://day` link carries. Keeping it as
/// plain year/month/day (instead of a midnight `Date`) means a link written
/// in one time zone opens the same day in another, and DST never shifts a
/// recording onto the neighbouring cell.
struct CalendarDay: Hashable, Comparable, CustomStringConvertible {
    let year: Int
    let month: Int
    let day: Int

    init(year: Int, month: Int, day: Int) {
        self.year = year
        self.month = month
        self.day = day
    }

    init(_ date: Date, calendar: Calendar = .current) {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        self.init(year: parts.year ?? 1970, month: parts.month ?? 1, day: parts.day ?? 1)
    }

    /// `2026-08-29`. Rejects anything that is not a real date (`2026-02-30`).
    init?(iso: String) {
        let parts = iso.split(separator: "-", omittingEmptySubsequences: false)
        guard parts.count == 3, parts[0].count == 4, parts[1].count == 2, parts[2].count == 2,
              let y = Int(parts[0]), let m = Int(parts[1]), let d = Int(parts[2]),
              (1...12).contains(m), (1...31).contains(d) else { return nil }
        var gregorian = Calendar(identifier: .gregorian)
        gregorian.timeZone = TimeZone(identifier: "UTC")!
        guard let date = gregorian.date(from: DateComponents(year: y, month: m, day: d)),
              CalendarDay(date, calendar: gregorian) == CalendarDay(year: y, month: m, day: d)
        else { return nil }
        self.init(year: y, month: m, day: d)
    }

    var iso: String { String(format: "%04d-%02d-%02d", year, month, day) }
    var description: String { iso }

    /// The first day of this day's month.
    var monthStart: CalendarDay { CalendarDay(year: year, month: month, day: 1) }

    func sameMonth(as other: CalendarDay) -> Bool {
        year == other.year && month == other.month
    }

    /// Local midnight of this day.
    func date(in calendar: Calendar = .current) -> Date? {
        calendar.date(from: DateComponents(year: year, month: month, day: day))
    }

    /// Shift by whole months, landing on the 1st (month navigation only).
    func addingMonths(_ delta: Int, calendar: Calendar = .current) -> CalendarDay {
        guard let start = monthStart.date(in: calendar),
              let moved = calendar.date(byAdding: .month, value: delta, to: start)
        else { return self }
        return CalendarDay(moved, calendar: calendar).monthStart
    }

    static func < (lhs: CalendarDay, rhs: CalendarDay) -> Bool {
        (lhs.year, lhs.month, lhs.day) < (rhs.year, rhs.month, rhs.day)
    }
}

/// `plaudnote://` deep links — the address other apps (Hookmark, Obsidian,
/// Raycast, a browser) store to bring this app forward on a recording or on
/// one calendar day. The scheme is registered in the bundle's Info.plist by
/// `scripts/package-macos-app.sh`; `swift run` builds can't receive them.
///
/// Canonical forms (what the app writes):
///   plaudnote://open?id=<file_id>
///   plaudnote://day?date=2026-08-29
/// Also accepted on input: `plaudnote://recording/<id>`, `plaudnote://day/<date>`.
enum RecordingLink: Equatable {
    case recording(id: String)
    case day(CalendarDay)

    static let scheme = "plaudnote"

    static func url(forRecording id: String) -> URL {
        var parts = URLComponents()
        parts.scheme = scheme
        parts.host = "open"
        parts.queryItems = [URLQueryItem(name: "id", value: id)]
        return parts.url!
    }

    static func url(forDay day: CalendarDay) -> URL {
        var parts = URLComponents()
        parts.scheme = scheme
        parts.host = "day"
        parts.queryItems = [URLQueryItem(name: "date", value: day.iso)]
        return parts.url!
    }

    /// `[title](plaudnote://open?id=…)` with the brackets in the title escaped
    /// so Obsidian and other Markdown renderers keep the link intact.
    static func markdown(title: String?, id: String) -> String {
        "[\(escapedTitle(title))](\(url(forRecording: id).absoluteString))"
    }

    /// `<a href=…>title</a>` — rich-text apps (Notes, Mail) paste a titled
    /// link; Obsidian converts it back to the Markdown form above.
    static func html(title: String?, id: String) -> String {
        let text = displayTitle(title)
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
        return "<a href=\"\(url(forRecording: id).absoluteString)\">\(text)</a>"
    }

    static func displayTitle(_ title: String?) -> String {
        let trimmed = (title ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "Plaud recording" : trimmed
    }

    private static func escapedTitle(_ title: String?) -> String {
        displayTitle(title)
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "[", with: "\\[")
            .replacingOccurrences(of: "]", with: "\\]")
            .replacingOccurrences(of: "\n", with: " ")
    }

    static func parse(_ url: URL) -> RecordingLink? {
        guard url.scheme?.lowercased() == scheme,
              let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
        else { return nil }
        let host = (parts.host ?? "").lowercased()
        let pathValue = parts.path
            .split(separator: "/").first.map(String.init)
        func query(_ name: String) -> String? {
            parts.queryItems?.first { $0.name == name }?.value
        }

        switch host {
        case "open", "recording", "file":
            guard let id = query("id") ?? pathValue, isValidID(id) else { return nil }
            return .recording(id: id)
        case "day", "date":
            guard let raw = query("date") ?? pathValue,
                  let day = CalendarDay(iso: raw) else { return nil }
            return .day(day)
        default:
            return nil
        }
    }

    /// Plaud file ids are 32 hex characters; local ids may carry `-`/`_`.
    /// Anything else (spaces, slashes, quotes) is refused rather than passed
    /// on to SQLite lookups or shell-outs.
    static func isValidID(_ id: String) -> Bool {
        !id.isEmpty && id.count <= 128 && id.allSatisfy {
            $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_")
        }
    }
}
