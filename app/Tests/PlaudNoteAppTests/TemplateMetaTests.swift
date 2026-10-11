import XCTest
@testable import PlaudNoteApp

final class TemplateMetaTests: XCTestCase {
    private func meta(_ frontMatter: String, body: String = "\nBody\n",
                      name: String = "sample") -> TemplateMeta {
        TemplateMeta.parse(name: name, raw: "---\n\(frontMatter)\n---\n\(body)")
    }

    func testParsesEveryKey() {
        let t = meta("""
            name: something-else
            description: Meeting minutes — decisions and action items.
            kind: integrated
            deprecated: false
            replaced_by: note-meeting
            """, name: "note-sample")
        XCTAssertEqual(t.name, "note-sample", "identity is the file name, not the name: key")
        XCTAssertEqual(t.id, "note-sample")
        XCTAssertEqual(t.description, "Meeting minutes — decisions and action items.")
        XCTAssertEqual(t.kind, .integrated)
        XCTAssertFalse(t.deprecated)
        XCTAssertEqual(t.replacedBy, "note-meeting")
        XCTAssertEqual(t.body, "\nBody\n")
    }

    func testMissingKindIsSummary() {
        let t = meta("name: x\ndescription: d")
        XCTAssertEqual(t.kind, .summary)
        XCTAssertFalse(t.deprecated)
        XCTAssertEqual(t.replacedBy, "")
    }

    func testKindQuotedAliasedAndCased() {
        XCTAssertEqual(meta("kind: \"integrated\"").kind, .integrated)
        XCTAssertEqual(meta("kind: integrate").kind, .integrated)
        XCTAssertEqual(meta("kind: 'Integrated'  # merged output").kind, .integrated)
        XCTAssertEqual(meta("kind: summarize").kind, .summary)
        XCTAssertEqual(meta("kind: summary").kind, .summary)
    }

    func testKindInternal() {
        XCTAssertEqual(meta("kind: internal").kind, .internalUse)
        XCTAssertEqual(TemplateMeta.Kind.internalUse.rawValue, "internal")
    }

    func testUnknownKindIsSummary() {
        XCTAssertEqual(meta("kind: transcript").kind, .summary)
        XCTAssertEqual(meta("kind:").kind, .summary)
    }

    func testDeprecatedWithReplacement() {
        let t = meta("deprecated: yes\nreplaced_by: note-meeting", name: "cmds-meeting")
        XCTAssertTrue(t.deprecated)
        XCTAssertEqual(t.replacedBy, "note-meeting")
        XCTAssertTrue(meta("deprecated: TRUE").deprecated)
        XCTAssertTrue(meta("deprecated: 1").deprecated)
        XCTAssertFalse(meta("deprecated: no").deprecated)
        XCTAssertFalse(meta("deprecated: maybe").deprecated)
    }

    func testNoFrontMatterKeepsWholeFileAsBody() {
        let raw = "Summarise this:\n{transcript}\n"
        let t = TemplateMeta.parse(name: "plain", raw: raw)
        XCTAssertEqual(t.description, "")
        XCTAssertEqual(t.kind, .summary)
        XCTAssertFalse(t.deprecated)
        XCTAssertEqual(t.body, raw)
    }

    func testUnterminatedOrCRLFFrontMatterIsNotFrontMatter() {
        // Same bounds as the Python loader: a leading "---\n" and a closing "\n---\n".
        let open = "---\ndescription: never closed\n\nBody\n"
        XCTAssertEqual(TemplateMeta.parse(name: "x", raw: open).body, open)
        XCTAssertEqual(TemplateMeta.parse(name: "x", raw: open).description, "")
        let crlf = "---\r\ndescription: d\r\n---\r\nBody\r\n"
        XCTAssertEqual(TemplateMeta.parse(name: "x", raw: crlf).body, crlf)
    }

    func testBodyIsKeptByteForByte() {
        let korean = String(repeating: "전사본에 없는 것을 만들지 않는다. 날짜·금액·인명은 특히 엄격하게 확인함. ", count: 12)
        let body = """

            # 입력

            관련 노트: [[Weekly Review]] · [[프로젝트/회의록|회의록]]
            \t- tab-indented line {transcript}
            \(korean)
            ---
            Part 2 after a horizontal rule, trailing spaces   \n
            """
        let t = meta("name: note-x\ndescription: d", body: body)
        XCTAssertEqual(t.body, body)
        XCTAssertEqual(Array(t.body.utf8), Array(body.utf8))
        XCTAssertTrue(t.body.hasPrefix("\n# 입력"))
        XCTAssertTrue(t.body.contains("\n---\nPart 2"), "a later --- line belongs to the body")
    }

    func testFirstOccurrenceOfDuplicateKeyWins() {
        let t = meta("description: first\nkind: internal\ndescription: second\nkind: summary")
        XCTAssertEqual(t.description, "first")
        XCTAssertEqual(t.kind, .internalUse)
    }

    func testValueQuotesAndComments() {
        XCTAssertEqual(meta("description: Weekly sync # owner note").description, "Weekly sync")
        XCTAssertEqual(meta("description: \"Use # for headings\"").description, "Use # for headings")
        XCTAssertEqual(meta("description: 'single quoted'").description, "single quoted")
        XCTAssertEqual(meta("description: C# notes").description, "C# notes")
        XCTAssertEqual(meta("description: \"unbalanced").description, "\"unbalanced")
        XCTAssertEqual(meta("description: \"a\" and \"b\"").description, "\"a\" and \"b\"")
    }

    func testOnlyColumnZeroKeysAreRead() {
        let t = meta("tags:\n  description: nested\n# description: commented\nkind: internal")
        XCTAssertEqual(t.description, "")
        XCTAssertEqual(t.kind, .internalUse)
    }

    func testPickerLabel() {
        let deprecated = meta("deprecated: true\nreplaced_by: note-meeting", name: "cmds-meeting")
        XCTAssertEqual(deprecated.pickerLabel, "cmds-meeting (deprecated → note-meeting)")
        XCTAssertEqual(meta("deprecated: true", name: "old").pickerLabel, "old (deprecated)")
        XCTAssertEqual(meta("description: d", name: "note-meeting").pickerLabel, "note-meeting")
    }

    func testSlotChoicesDropInternalAndSortByName() {
        let all = [
            meta("kind: summary", name: "note-meeting"),
            meta("kind: internal", name: "classify"),
            meta("kind: integrated", name: "default"),
            meta("kind: internal", name: "metadata"),
            meta("description: d", name: "cmds-lecture"),
        ]
        XCTAssertEqual(TemplateMeta.slotChoices(all).map(\.name),
                       ["cmds-lecture", "default", "note-meeting"])
    }

    func testViewerOrderPutsNoteTemplatesFirst() {
        let all = ["default", "note-meeting", "classify", "note-general"].map {
            meta("description: d", name: $0)
        }
        XCTAssertEqual(TemplateMeta.viewerOrder(all).map(\.name),
                       ["note-general", "note-meeting", "classify", "default"])
    }

    func testScanReadsMarkdownAndReportsUnreadableFiles() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("TemplateMetaTests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        try "---\ndescription: B\n---\n\nb".write(
            to: dir.appendingPathComponent("b.md"), atomically: true, encoding: .utf8)
        try "no front matter".write(
            to: dir.appendingPathComponent("a.md"), atomically: true, encoding: .utf8)
        try "not a template".write(
            to: dir.appendingPathComponent("notes.txt"), atomically: true, encoding: .utf8)
        // 0xC3 0x28 is an invalid UTF-8 sequence.
        try Data([0x2D, 0x2D, 0xC3, 0x28, 0x0A]).write(to: dir.appendingPathComponent("broken.md"))

        let scan = TemplateMeta.scan(directory: dir)
        XCTAssertFalse(scan.folderMissing)
        XCTAssertEqual(scan.templates.map(\.name), ["a", "b"])
        XCTAssertEqual(scan.templates.last?.description, "B")
        XCTAssertEqual(scan.unreadable, ["broken"])
    }

    func testScanReportsMissingFolder() {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("TemplateMetaTests-missing-\(UUID().uuidString)")
        let scan = TemplateMeta.scan(directory: dir)
        XCTAssertTrue(scan.folderMissing)
        XCTAssertEqual(scan.templates, [])
    }
}
