import AppKit
import SwiftUI
import XCTest
@testable import PlaudNoteApp

final class IMETextFieldTests: XCTestCase {
    @MainActor
    func testChangePublishesMarkedFinalSyllableInsteadOfStaleFieldValue() {
        var saved = "이상"
        let coordinator = IMETextField.Coordinator(text: Binding(
            get: { saved }, set: { saved = $0 }
        ))
        let editor = NSTextView()
        editor.string = "이상"
        editor.setMarkedText("훈", selectedRange: NSRange(location: 1, length: 0),
                             replacementRange: NSRange(location: 2, length: 0))
        let field = EditingField(string: "이상")
        field.editor = editor
        XCTAssertTrue(editor.hasMarkedText())
        coordinator.controlTextDidChange(Notification(
            name: NSControl.textDidChangeNotification, object: field
        ))
        XCTAssertEqual(saved, "이상훈")
    }

    @MainActor
    func testEndEditingPublishesCommittedValueWithoutEditor() {
        var saved = "이상"
        let coordinator = IMETextField.Coordinator(text: Binding(
            get: { saved }, set: { saved = $0 }
        ))
        let field = NSTextField(string: "이상훈")
        coordinator.controlTextDidEndEditing(Notification(
            name: NSControl.textDidEndEditingNotification, object: field
        ))
        XCTAssertEqual(saved, "이상훈")
    }
}

private final class EditingField: NSTextField {
    var editor: NSTextView?
    override func currentEditor() -> NSText? { editor }
}
