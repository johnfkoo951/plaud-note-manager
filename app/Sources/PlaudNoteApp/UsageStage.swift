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
        selection.isEmpty || resolve(raw).map { selection.contains($0) } == true
    }
}

/// Where an archived (사용 완료) recording still shows. Archive exists to get
/// low-value or finished recordings out of the working lists, so they leave
/// All Files, Unfiled, Starred, folders, tags and the calendar — but, like
/// mail archives, a search still finds them, and so does asking for
/// 사용 완료 in the 사용 단계 filter. Trash stays the only owner of trashed
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
