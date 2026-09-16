import AppKit
import SwiftUI

/// Widths are points, matching the user's 1608 × 854 working window.
/// Versioned preferences replace the old cramped defaults once, then keep
/// subsequent divider adjustments across launches.
enum WorkspaceLayout {
    static let windowWidth: CGFloat = 1608
    static let windowHeight: CGFloat = 854
    static let sidebarDefault: CGFloat = 280
    static let libraryDefault: CGFloat = 420
    static let workDefault: CGFloat = 350
    static let sidebarRange: ClosedRange<CGFloat> = 220...360
    static let libraryRange: ClosedRange<CGFloat> = 340...600
    static let workRange: ClosedRange<CGFloat> = 300...520
    static let detailMinimum: CGFloat = 400
    static let sidebarKey = "workspace.v2.sidebarWidth"
    static let libraryKey = "workspace.v2.libraryWidth"
    static let workKey = "workspace.v2.workWidth"

    static func clamped(_ width: CGFloat, to range: ClosedRange<CGFloat>,
                        fallback: CGFloat) -> CGFloat {
        guard width.isFinite, width > 0 else { return fallback }
        return min(range.upperBound, max(range.lowerBound, width))
    }

    static func outerWidths(available: CGFloat, sidebar: CGFloat,
                            library: CGFloat) -> (sidebar: CGFloat, library: CGFloat) {
        var side = clamped(sidebar, to: sidebarRange, fallback: sidebarDefault)
        var list = clamped(library, to: libraryRange, fallback: libraryDefault)
        // Reserve room for the transcript and a usable Work Sidebar on smaller
        // windows. Growing the window gives that space back to the detail pane.
        var excess = max(0, side + list + detailMinimum + workRange.lowerBound + 1 - available)
        let sideReduction = min(excess, side - sidebarRange.lowerBound)
        side -= sideReduction
        excess -= sideReduction
        list -= min(excess, list - libraryRange.lowerBound)
        return (side, list)
    }

    static func workWidth(available: CGFloat, preferred: CGFloat) -> CGFloat {
        let preferred = clamped(preferred, to: workRange, fallback: workDefault)
        return max(workRange.lowerBound, min(preferred, available - detailMinimum))
    }
}

/// NavigationSplitView's ideal widths are suggestions and macOS may restore
/// older native divider positions over them. Apply the current preferences
/// once after the public NSSplitView has laid out, then leave dragging to AppKit.
struct WorkspaceSplitWidths: NSViewRepresentable {
    enum Role { case navigation, work }
    let role: Role

    func makeCoordinator() -> Coordinator { Coordinator(role: role) }

    func makeNSView(context: Context) -> MarkerView {
        let view = MarkerView()
        view.onLayout = { [weak coordinator = context.coordinator] marker in
            coordinator?.attach(from: marker)
        }
        return view
    }

    func updateNSView(_ nsView: MarkerView, context: Context) {
        context.coordinator.attach(from: nsView)
    }

    static func dismantleNSView(_ nsView: MarkerView, coordinator: Coordinator) {
        nsView.onLayout = nil
        coordinator.detach()
    }

    final class MarkerView: NSView {
        var onLayout: ((NSView) -> Void)?
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            onLayout?(self)
        }
        override func layout() {
            super.layout()
            onLayout?(self)
        }
    }

    final class Coordinator: NSObject {
        private let role: Role
        private weak var split: NSSplitView?
        private var restored = false
        private var scheduled = false
        private var applying = false
        private var draggingDivider = false
        private var mouseMonitor: Any?

        init(role: Role) { self.role = role }

        func attach(from marker: NSView) {
            guard marker.window != nil, !restored, !scheduled else { return }
            let count = role == .navigation ? 3 : 2
            var ancestor = marker.superview
            while let view = ancestor {
                if let candidate = view as? NSSplitView,
                   candidate.isVertical, candidate.arrangedSubviews.count == count {
                    split = candidate
                    break
                }
                ancestor = view.superview
            }
            guard let split, split.bounds.width > 0 else { return }
            scheduled = true
            DispatchQueue.main.async { [weak self, weak split] in
                guard let self else { return }
                self.scheduled = false
                guard let split, split.window != nil, !self.restored else { return }
                self.apply(to: split)
                self.restored = true
                NotificationCenter.default.addObserver(
                    self, selector: #selector(self.resized(_:)),
                    name: NSSplitView.didResizeSubviewsNotification, object: split
                )
                self.mouseMonitor = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown, .leftMouseUp]) { [weak self] event in
                    guard let self, let split = self.split,
                          event.window === split.window else { return event }
                    if event.type == .leftMouseDown {
                        let point = split.convert(event.locationInWindow, from: nil)
                        self.draggingDivider = split.bounds.contains(point)
                            && split.arrangedSubviews.dropLast().contains { pane in
                                let x = pane.frame.maxX
                                return point.x >= x - 4 && point.x <= x + split.dividerThickness + 4
                            }
                    } else if self.draggingDivider {
                        self.persistWidths()
                        self.draggingDivider = false
                    }
                    return event
                }
            }
        }

        private func preference(_ key: String, fallback: CGFloat) -> CGFloat {
            (UserDefaults.standard.object(forKey: key) as? NSNumber)
                .map { CGFloat($0.doubleValue) } ?? fallback
        }

        private func apply(to split: NSSplitView) {
            applying = true
            defer { applying = false }
            split.layoutSubtreeIfNeeded()
            let available = split.bounds.width
                - split.dividerThickness * CGFloat(split.arrangedSubviews.count - 1)
            if role == .navigation {
                let widths = WorkspaceLayout.outerWidths(
                    available: available,
                    sidebar: preference(WorkspaceLayout.sidebarKey, fallback: WorkspaceLayout.sidebarDefault),
                    library: preference(WorkspaceLayout.libraryKey, fallback: WorkspaceLayout.libraryDefault)
                )
                split.setPosition(widths.sidebar, ofDividerAt: 0)
                split.setPosition(widths.sidebar + split.dividerThickness + widths.library,
                                  ofDividerAt: 1)
            } else {
                let width = WorkspaceLayout.workWidth(
                    available: available,
                    preferred: preference(WorkspaceLayout.workKey, fallback: WorkspaceLayout.workDefault)
                )
                split.setPosition(available - width, ofDividerAt: 0)
            }
        }

        @objc private func resized(_ notification: Notification) {
            persistWidths()
        }

        private func persistWidths() {
            // Only an actual divider drag can save a preference. Initial
            // layout, selection, and even a held click elsewhere cannot save
            // a temporarily compressed size over the preferred proportions.
            guard restored, !applying, draggingDivider, let split else { return }
            let panes = split.arrangedSubviews
            if role == .navigation, panes.count == 3 {
                save(panes[0].frame.width, key: WorkspaceLayout.sidebarKey,
                     range: WorkspaceLayout.sidebarRange)
                save(panes[1].frame.width, key: WorkspaceLayout.libraryKey,
                     range: WorkspaceLayout.libraryRange)
            } else if role == .work, panes.count == 2 {
                save(panes[1].frame.width, key: WorkspaceLayout.workKey,
                     range: WorkspaceLayout.workRange)
            }
        }

        private func save(_ width: CGFloat, key: String, range: ClosedRange<CGFloat>) {
            guard range.contains(width) else { return }
            if abs(UserDefaults.standard.double(forKey: key) - width) > 1 {
                UserDefaults.standard.set(Double(width), forKey: key)
            }
        }

        func detach() {
            NotificationCenter.default.removeObserver(self)
            if let mouseMonitor { NSEvent.removeMonitor(mouseMonitor) }
            mouseMonitor = nil
            draggingDivider = false
            split = nil
        }

        deinit {
            NotificationCenter.default.removeObserver(self)
            if let mouseMonitor { NSEvent.removeMonitor(mouseMonitor) }
        }
    }
}
