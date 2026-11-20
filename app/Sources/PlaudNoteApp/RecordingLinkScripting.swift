import AppKit

/// List column layout: the plain list, or the month calendar above a list
/// narrowed to the month/day picked there. Persisted across launches.
enum LibraryMode: String, CaseIterable, Identifiable {
    case list
    case calendar

    static let defaultsKey = "libraryMode"

    static var stored: LibraryMode {
        UserDefaults.standard.string(forKey: defaultsKey).flatMap(LibraryMode.init) ?? .list
    }

    var id: String { rawValue }
}

/// "Scroll the list to this recording" — the nonce makes a second request for
/// the same id still fire `onChange`.
struct RevealRequest: Equatable {
    let id: String
    let nonce = UUID()
}

extension Notification.Name {
    /// Edit ▸ Copy Link to Recording (⌥⌘C).
    static let copyPlaudRecordingLink = Notification.Name("plaud.copyRecordingLink")
}

/// Which window's store answers AppleScript. The store whose selection
/// changed last wins, which in a single-window app is simply "the window".
@MainActor
final class RecordingLinkBoard {
    static let shared = RecordingLinkBoard()
    weak var activeStore: FileStore?
}

/// AppleScript surface declared in `app/Resources/PlaudNoteManager.sdef`.
/// Cocoa scripting reads application properties by KVC on `NSApp`, so plain
/// `@objc` getters here are all it needs. Hookmark's "Get Address" / "Get
/// Name" scripts read these to hook the selected recording (docs/HOOKMARK.md):
///
///     tell application "Plaud Note Manager" to get selected recording url
extension NSApplication {
    @objc var plaudSelectedRecordingURL: String {
        RecordingLinkBoard.shared.activeStore?.selectedRecordingLink?.url.absoluteString ?? ""
    }

    @objc var plaudSelectedRecordingName: String {
        RecordingLinkBoard.shared.activeStore?.selectedRecordingLink?.title ?? ""
    }
}
