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
