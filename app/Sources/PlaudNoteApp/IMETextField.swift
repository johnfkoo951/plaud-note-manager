import AppKit
import SwiftUI

/// SwiftUI's TextField binding can lag behind the field editor's marked text.
/// Publish the editor's complete string, but never write back during composition.
struct IMETextField: NSViewRepresentable {
    let placeholder: String
    @Binding var text: String

    init(_ placeholder: String, text: Binding<String>) {
        self.placeholder = placeholder
        self._text = text
    }

    func makeCoordinator() -> Coordinator { Coordinator(text: $text) }

    func makeNSView(context: Context) -> NSTextField {
        let field = NSTextField(string: text)
        field.placeholderString = placeholder
        field.isEditable = true
        field.isSelectable = true
        field.bezelStyle = .roundedBezel
        field.font = .systemFont(ofSize: NSFont.systemFontSize)
        field.delegate = context.coordinator
        field.setContentHuggingPriority(.defaultLow, for: .horizontal)
        return field
    }

    func updateNSView(_ field: NSTextField, context: Context) {
        context.coordinator.text = $text
        field.isEnabled = context.environment.isEnabled
        if let editor = field.currentEditor() as? NSTextView, editor.hasMarkedText() { return }
        if field.stringValue != text { field.stringValue = text }
        field.placeholderString = placeholder
    }

    final class Coordinator: NSObject, NSTextFieldDelegate {
        var text: Binding<String>
        init(text: Binding<String>) { self.text = text }

        func controlTextDidChange(_ notification: Notification) { publish(notification) }
        func controlTextDidEndEditing(_ notification: Notification) { publish(notification) }

        private func publish(_ notification: Notification) {
            guard let field = notification.object as? NSTextField else { return }
            let value = field.currentEditor()?.string ?? field.stringValue
            if text.wrappedValue != value { text.wrappedValue = value }
        }
    }

    /// End marked-text composition before a save action snapshots its bindings.
    static func finishEditing() {
        guard let window = NSApp.keyWindow else { return }
        (window.firstResponder as? NSTextView)?.unmarkText()
        window.makeFirstResponder(nil)
    }
}
