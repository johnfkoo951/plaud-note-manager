import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldSaveApplicationState(_ app: NSApplication) -> Bool {
        false
    }

    func applicationShouldRestoreApplicationState(_ app: NSApplication) -> Bool {
        false
    }
}

@main
struct PlaudNoteApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    init() {
        UserDefaults.standard.set(false, forKey: "NSQuitAlwaysKeepsWindows")
        NSApplication.shared.setActivationPolicy(.regular)
        DispatchQueue.main.async {
            NSApplication.shared.activate(ignoringOtherApps: true)
        }
    }

    var body: some Scene {
        WindowGroup("Plaud Note Manager") {
            ContentView()
                .frame(minWidth: 1280, minHeight: 800)
        }
        // Default matches Yohan's working window on the 14" MBP
        // (1608×854pt, ~1.88:1) — sidebar + list + detail + Work Sidebar
        // all visible without horizontal crowding.
        .defaultSize(width: WorkspaceLayout.windowWidth, height: WorkspaceLayout.windowHeight)
        .windowStyle(.hiddenTitleBar)
        .commands {
            CommandGroup(replacing: .appSettings) {
                // Standard macOS Settings shortcut. ⌘. also still works via
                // the toolbar gear button in ContentView.
                Button("Settings…") {
                    NotificationCenter.default.post(name: .openPlaudSettings,
                                                    object: nil)
                }
                .keyboardShortcut(",", modifiers: .command)
            }
            CommandGroup(after: .textEditing) {
                Button("Command Palette…") {
                    NotificationCenter.default.post(
                        name: .togglePlaudCommandPalette, object: nil
                    )
                }
                .keyboardShortcut("k", modifiers: .command)
            }
        }
    }
}
