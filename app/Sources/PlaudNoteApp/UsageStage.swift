import Foundation

/// Presentation groups only; the five CLI/SQLite lifecycle values stay intact.
enum UsageStage: String, CaseIterable, Identifiable {
    case unused, prepared, tracking, archived

    var id: String { rawValue }
    var title: String {
        switch self {
        case .unused: return "Not Used"
        case .prepared: return "메타데이터 준비"
        case .tracking: return "집중 사용중"
        case .archived: return "사용 완료"
        }
    }
    var dbValue: String {
        switch self {
        case .unused: return "unused"
        case .prepared: return "metadata-ready"
        case .tracking: return "vault-linked"
        case .archived: return "archived"
        }
    }
    static func resolve(_ raw: String?) -> UsageStage? {
        switch raw?.trimmingCharacters(in: .whitespacesAndNewlines) {
        case nil, "", "unused": return .unused
        case "metadata-ready": return .prepared
        case "vault-linked", "used-elsewhere": return .tracking
        case "archived": return .archived
        default: return nil
        }
    }
    /// Nil means no write, preserving both existing tracking representations.
    func updateValue(current: String?) -> String? {
        Self.resolve(current) == self ? nil : dbValue
    }

    static func matches(_ raw: String?, selection: Set<UsageStage>) -> Bool {
        selection.isEmpty || selection == UsageStageFilter.all
            || resolve(raw).map { selection.contains($0) } == true
    }
}

/// The 사용 단계 menu. The stored filter keeps `[]` for "nothing chosen",
/// and what that lists depends on the place: working lists leave 사용 완료
/// to Archive, Trash keeps every stage, Archive is 사용 완료 by definition.
/// The menu ticks what is actually listed, so "전체 단계" means all four.
enum UsageStageFilter {
    static let all = Set(UsageStage.allCases)

    /// What an empty filter lists in `sidebar`.
    static func implied(in sidebar: SidebarItem) -> Set<UsageStage> {
        switch sidebar {
        case .archive: return [.archived]
        case .trash: return all
        default: return all.subtracting([.archived])
        }
    }

    static func ticked(_ stored: Set<UsageStage>, in sidebar: SidebarItem) -> Set<UsageStage> {
        stored.isEmpty ? implied(in: sidebar) : stored
    }

    /// The filter to store after ticking or unticking one stage. Landing
    /// back on the place's own default (or on nothing) clears the filter.
    static func toggling(_ stage: UsageStage, on: Bool, stored: Set<UsageStage>,
                         in sidebar: SidebarItem) -> Set<UsageStage> {
        var next = ticked(stored, in: sidebar)
        if on { next.insert(stage) } else { next.remove(stage) }
        return next.isEmpty || next == implied(in: sidebar) ? [] : next
    }

    /// True when the filter changes what this place lists (shows the ✕).
    static func isActive(_ stored: Set<UsageStage>, in sidebar: SidebarItem) -> Bool {
        !stored.isEmpty && stored != implied(in: sidebar)
    }

    static func label(_ stored: Set<UsageStage>, in sidebar: SidebarItem) -> String {
        let shown = ticked(stored, in: sidebar)
        if shown == all { return "전체" }
        if shown == all.subtracting([.archived]) { return "전체 · 사용 완료 제외" }
        return UsageStage.allCases.filter { shown.contains($0) }.map(\.title)
            .joined(separator: ", ")
    }
}

/// Where an archived (사용 완료) recording still shows. Archive exists to get
/// low-value or finished recordings out of the working lists, so they leave
/// All Files, Unfiled, Starred, folders, tags and the calendar — but, like
/// mail archives, a search still finds them, and so does ticking 사용 완료
/// (or 전체 단계) in the 사용 단계 filter. Trash stays the only owner of trashed
/// rows; the caller checks `isTrash` separately.
enum ArchiveVisibility {
    static func shows(isArchived: Bool, in sidebar: SidebarItem,
                      stageFilter: Set<UsageStage>, searching: Bool) -> Bool {
        switch sidebar {
        case .archive:
            return isArchived
        case .trash:
            return true
        default:
            return !isArchived || searching || stageFilter.contains(.archived)
        }
    }

    static func isArchived(_ usageStatus: String?) -> Bool {
        UsageStage.resolve(usageStatus) == .archived
    }
}
