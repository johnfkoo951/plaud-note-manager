import AppKit
import SwiftUI

/// Recordings and total length on one calendar day.
struct CalendarDayStat: Equatable {
    var count: Int = 0
    var totalMs: Double = 0
}

/// Days picked in the calendar, with Finder's click rules:
/// click = just this day (again = none), ⌘ = toggle one day,
/// ⇧ = every day from the anchor to this one, ⌘⇧ = add that range.
/// The anchor is the last day clicked without ⇧, so repeated ⇧-clicks
/// re-span from the same start. Ranges may cross months.
struct CalendarSelection: Equatable {
    private(set) var days: Set<CalendarDay> = []
    private(set) var anchor: CalendarDay?

    /// Longest ⇧ range, so a stray click years away can't build a
    /// ten-thousand-day set.
    static let maxRangeDays = 400

    init() {}

    init(day: CalendarDay) {
        days = [day]
        anchor = day
    }

    var isEmpty: Bool { days.isEmpty }
    func contains(_ day: CalendarDay) -> Bool { days.contains(day) }

    mutating func click(_ day: CalendarDay, command: Bool, shift: Bool,
                        calendar: Calendar = .current) {
        if shift, let anchor {
            let span = Set(Self.span(from: anchor, to: day, calendar: calendar))
            days = command ? days.union(span) : span
            return
        }
        if command {
            if days.contains(day) { days.remove(day) } else { days.insert(day) }
            anchor = day
            return
        }
        if days == [day] {
            self = CalendarSelection()
        } else {
            self = CalendarSelection(day: day)
        }
    }

    /// Inclusive run of days between two days, in either order.
    static func span(from a: CalendarDay, to b: CalendarDay,
                     calendar: Calendar = .current) -> [CalendarDay] {
        let (start, end) = a <= b ? (a, b) : (b, a)
        guard var cursor = start.date(in: calendar) else { return [start] }
        var out: [CalendarDay] = []
        while out.count < maxRangeDays {
            let day = CalendarDay(cursor, calendar: calendar)
            out.append(day)
            if day >= end { break }
            guard let next = calendar.date(byAdding: .day, value: 1, to: cursor) else { break }
            cursor = next
        }
        return out
    }

    /// `Aug 29`, `Aug 28 – Sep 3` for one unbroken run, else `5일 선택`.
    func summary(calendar: Calendar = .current) -> String {
        let sorted = days.sorted()
        guard let first = sorted.first, let last = sorted.last else { return "" }
        func label(_ day: CalendarDay, weekday: Bool) -> String {
            guard let date = day.date(in: calendar) else { return day.iso }
            return weekday
                ? date.formatted(.dateTime.month(.abbreviated).day().weekday(.wide))
                : date.formatted(.dateTime.month(.abbreviated).day())
        }
        if sorted.count == 1 { return label(first, weekday: true) }
        if Self.span(from: first, to: last, calendar: calendar).count == sorted.count {
            return "\(label(first, weekday: false)) – \(label(last, weekday: false))"
        }
        return "\(sorted.count)일 선택"
    }
}

/// Pure grouping/layout math behind the recording calendar, kept apart from
/// the view so it is unit-testable without a window.
enum RecordingCalendarModel {
    /// Bucket recordings by the LOCAL day they started on.
    static func stats(for files: [PlaudFileVM],
                      calendar: Calendar = .current) -> [CalendarDay: CalendarDayStat] {
        var out: [CalendarDay: CalendarDayStat] = [:]
        for file in files {
            guard let date = file.createdAt else { continue }
            let day = CalendarDay(date, calendar: calendar)
            out[day, default: CalendarDayStat()].count += 1
            out[day, default: CalendarDayStat()].totalMs += max(0, file.durationMs ?? 0)
        }
        return out
    }

    /// Months that hold at least one recording, newest first — the jump menu
    /// behind the month title.
    static func months(in stats: [CalendarDay: CalendarDayStat]) -> [(month: CalendarDay, count: Int)] {
        var counts: [CalendarDay: Int] = [:]
        for (day, stat) in stats { counts[day.monthStart, default: 0] += stat.count }
        return counts.sorted { $0.key > $1.key }.map { (month: $0.key, count: $0.value) }
    }

    /// 6 weeks × 7 days for `month`, starting on the user's first weekday.
    /// Cells outside the month are nil so the grid keeps a fixed height and
    /// the list below never jumps while paging months.
    static func grid(for month: CalendarDay, calendar: Calendar = .current) -> [CalendarDay?] {
        guard let first = month.monthStart.date(in: calendar),
              let days = calendar.range(of: .day, in: .month, for: first) else { return [] }
        let weekday = calendar.component(.weekday, from: first)
        let leading = (weekday - calendar.firstWeekday + 7) % 7
        var cells: [CalendarDay?] = Array(repeating: nil, count: leading)
        cells += days.map { CalendarDay(year: month.year, month: month.month, day: $0) }
        while cells.count < 42 { cells.append(nil) }
        return cells
    }

    /// Weekday initials in display order, e.g. `S M T W T F S`.
    static func weekdaySymbols(calendar: Calendar = .current) -> [String] {
        let symbols = calendar.veryShortWeekdaySymbols
        let shift = (calendar.firstWeekday - 1) % symbols.count
        return Array(symbols[shift...] + symbols[..<shift])
    }

    /// Gregorian weekday (1 = Sunday … 7 = Saturday) of grid column `column`.
    static func weekday(ofColumn column: Int, calendar: Calendar = .current) -> Int {
        (column + calendar.firstWeekday - 1) % 7 + 1
    }

    /// How strongly to tint a day cell: 0 = no recordings, 1…4 = light → dark.
    ///
    /// `busiest` is the heaviest day in the month on screen, so the scale is
    /// relative to that month rather than to the whole library.
    ///
    /// Default: total recorded length, square-root scaled, so a 20-minute call
    /// still shows next to a 3-hour retreat day. Counting recordings instead
    /// would make a day of five 2-minute memos look busier than one long
    /// meeting; a linear scale would leave most days near-blank.
    static func heatLevel(_ stat: CalendarDayStat, busiest: CalendarDayStat) -> Int {
        guard stat.count > 0 else { return 0 }
        guard busiest.totalMs > 0 else { return 1 }
        let ratio = (stat.totalMs / busiest.totalMs).squareRoot()
        return min(4, max(1, Int((ratio * 4).rounded(.up))))
    }

    /// The rows the list shows under the calendar: the selected days, or the
    /// whole month on screen when none is picked. Keeps `files`' order.
    static func files(_ files: [PlaudFileVM], month: CalendarDay, days: Set<CalendarDay>,
                      calendar: Calendar = .current) -> [PlaudFileVM] {
        files.filter { file in
            guard let date = file.createdAt else { return false }
            let fileDay = CalendarDay(date, calendar: calendar)
            if !days.isEmpty { return days.contains(fileDay) }
            return fileDay.sameMonth(as: month)
        }
    }

    /// `3h 12m`, `45m`, `0m`.
    static func durationLabel(ms: Double) -> String {
        let minutes = Int((ms / 60_000).rounded())
        let hours = minutes / 60
        if hours > 0 { return "\(hours)h \(minutes % 60)m" }
        return "\(minutes)m"
    }
}

/// Month grid above the recording list. Each day is tinted by how much was
/// recorded on it; clicking days narrows the list to them (⌘ adds, ⇧ spans —
/// see `CalendarSelection`), clicking the only selected day again widens back
/// to the month. Works on whatever the sidebar, search and 사용 단계 filters
/// already let through.
struct RecordingCalendarView: View {
    let files: [PlaudFileVM]
    @Binding var month: CalendarDay
    @Binding var selection: CalendarSelection

    private let calendar = Calendar.current
    private let cellHeight: CGFloat = 34
    private static let sundayColor = Color(red: 0.86, green: 0.36, blue: 0.36)
    private static let saturdayColor = Color(red: 0.38, green: 0.56, blue: 0.92)

    var body: some View {
        let stats = RecordingCalendarModel.stats(for: files, calendar: calendar)
        let grid = RecordingCalendarModel.grid(for: month, calendar: calendar)
        let monthDays = grid.compactMap { $0 }
        let busiest = monthDays.compactMap { stats[$0] }
            .max { $0.totalMs < $1.totalMs } ?? CalendarDayStat()
        let today = CalendarDay(Date(), calendar: calendar)

        VStack(spacing: 6) {
            header(stats: stats, monthDays: monthDays, today: today)

            HStack(spacing: 3) {
                ForEach(Array(RecordingCalendarModel.weekdaySymbols(calendar: calendar).enumerated()),
                        id: \.offset) { column, symbol in
                    Text(symbol)
                        .font(.system(size: 10.5, weight: .semibold))
                        .foregroundStyle(weekdayColor(column: column) ?? .secondary)
                        .frame(maxWidth: .infinity)
                }
            }

            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 3), count: 7),
                      spacing: 3) {
                ForEach(Array(grid.enumerated()), id: \.offset) { index, day in
                    if let day {
                        dayCell(day, column: index % 7,
                                stat: stats[day] ?? CalendarDayStat(),
                                busiest: busiest, today: today)
                    } else {
                        Color.clear.frame(height: cellHeight)
                    }
                }
            }
        }
        .padding(10)
        .background(AppUI.cardFill, in: RoundedRectangle(cornerRadius: AppUI.radius))
        .overlay(
            RoundedRectangle(cornerRadius: AppUI.radius)
                .stroke(AppUI.cardStroke, lineWidth: 0.5)
        )
    }

    // MARK: Header

    private func header(stats: [CalendarDay: CalendarDayStat], monthDays: [CalendarDay],
                        today: CalendarDay) -> some View {
        HStack(spacing: 4) {
            Button { page(-1) } label: {
                Image(systemName: "chevron.left").frame(width: 22, height: 22)
            }
            .buttonStyle(.borderless)
            .help("이전 달")

            Menu {
                monthJumpItems(stats: stats)
            } label: {
                Text(monthTitle(month))
                    .font(AppUI.sectionFont)
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .help("녹음이 있는 달로 이동")

            Button { page(1) } label: {
                Image(systemName: "chevron.right").frame(width: 22, height: 22)
            }
            .buttonStyle(.borderless)
            .help("다음 달")

            Spacer(minLength: 4)

            let monthStat = monthDays.compactMap { stats[$0] }
                .reduce(into: CalendarDayStat()) { $0.count += $1.count; $0.totalMs += $1.totalMs }
            if monthStat.count > 0 {
                Text("\(monthStat.count)개 · \(RecordingCalendarModel.durationLabel(ms: monthStat.totalMs))")
                    .font(AppUI.metaFont)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            Button("오늘") {
                month = today.monthStart
                selection = CalendarSelection(day: today)
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .help("오늘로 이동")
        }
    }

    @ViewBuilder
    private func monthJumpItems(stats: [CalendarDay: CalendarDayStat]) -> some View {
        let months = RecordingCalendarModel.months(in: stats)
        if months.isEmpty {
            Text("녹음이 없습니다")
        } else {
            let years = Array(Set(months.map(\.month.year))).sorted(by: >)
            ForEach(years, id: \.self) { year in
                Section(String(year)) {
                    ForEach(months.filter { $0.month.year == year }, id: \.month) { entry in
                        Button("\(monthTitle(entry.month))  —  \(entry.count)") {
                            month = entry.month
                            selection = CalendarSelection()
                        }
                    }
                }
            }
        }
    }

    // MARK: Day cell

    private func dayCell(_ day: CalendarDay, column: Int, stat: CalendarDayStat,
                         busiest: CalendarDayStat, today: CalendarDay) -> some View {
        let level = RecordingCalendarModel.heatLevel(stat, busiest: busiest)
        let isSelected = selection.contains(day)
        let isToday = day == today
        return Button {
            let flags = NSApp.currentEvent?.modifierFlags ?? NSEvent.modifierFlags
            selection.click(day, command: flags.contains(.command),
                            shift: flags.contains(.shift), calendar: calendar)
        } label: {
            VStack(spacing: 0) {
                Text("\(day.day)")
                    .font(.system(size: 12, weight: isToday || isSelected ? .bold : .medium))
                    .foregroundStyle(isToday ? AppUI.accentPink
                                     : (weekdayColor(column: column) ?? .primary))
                Text(stat.count > 0 ? "\(stat.count)" : " ")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(.primary.opacity(level >= 3 ? 0.85 : 0.55))
            }
            .frame(maxWidth: .infinity, minHeight: cellHeight, maxHeight: cellHeight)
            .background(fill(level: level), in: RoundedRectangle(cornerRadius: 7))
            .overlay {
                if isSelected {
                    RoundedRectangle(cornerRadius: 7)
                        .stroke(AppUI.accentPink, lineWidth: 1.6)
                }
            }
            .contentShape(RoundedRectangle(cornerRadius: 7))
        }
        .buttonStyle(.plain)
        .help(cellHelp(day, stat: stat))
        .accessibilityLabel(cellHelp(day, stat: stat))
    }

    private func fill(level: Int) -> Color {
        switch level {
        case 0: return Color.primary.opacity(0.035)
        case 1: return AppUI.accentPink.opacity(0.16)
        case 2: return AppUI.accentPink.opacity(0.28)
        case 3: return AppUI.accentPink.opacity(0.44)
        default: return AppUI.accentPink.opacity(0.62)
        }
    }

    private func weekdayColor(column: Int) -> Color? {
        switch RecordingCalendarModel.weekday(ofColumn: column, calendar: calendar) {
        case 1: return Self.sundayColor
        case 7: return Self.saturdayColor
        default: return nil
        }
    }

    // MARK: Helpers

    /// Paging keeps the selection so a ⇧ range can span months; the bar
    /// under the calendar says what the list holds and offers 달 전체.
    private func page(_ delta: Int) {
        month = month.addingMonths(delta, calendar: calendar)
    }

    private func monthTitle(_ month: CalendarDay) -> String {
        guard let date = month.date(in: calendar) else { return month.iso }
        return date.formatted(.dateTime.year().month(.wide))
    }

    private func cellHelp(_ day: CalendarDay, stat: CalendarDayStat) -> String {
        let label = day.date(in: calendar)?
            .formatted(.dateTime.month(.abbreviated).day().weekday(.abbreviated)) ?? day.iso
        let hint = " — ⌘ 여러 날, ⇧ 기간 선택"
        guard stat.count > 0 else { return "\(label) · 녹음 없음" + hint }
        return "\(label) · \(stat.count)개 · \(RecordingCalendarModel.durationLabel(ms: stat.totalMs))" + hint
    }
}

/// Icon + title + one line of guidance, floated over an empty list. Same
/// card as the 전체 내용 no-results state.
struct CenteredHint: View {
    let systemImage: String
    let title: String
    let detail: String

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: systemImage)
                .font(.system(size: 28, weight: .regular))
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(.secondary)
            Text(title)
                .font(AppUI.rowTitleFont)
                .multilineTextAlignment(.center)
            Text(detail)
                .font(AppUI.metaFont)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppUI.spacingXL)
        .frame(maxWidth: 300)
        .background(AppUI.cardFill, in: RoundedRectangle(cornerRadius: AppUI.radius))
    }
}
