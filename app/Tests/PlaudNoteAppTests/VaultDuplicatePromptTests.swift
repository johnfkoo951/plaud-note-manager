import XCTest
@testable import PlaudNoteApp

final class VaultDuplicatePromptTests: XCTestCase {
    private let mainSend = VaultSendRequest(fileID: "f1", dest: "meetings")
    private let windowSend = VaultSendRequest(fileID: "f1", via: "claude-window")
    private let mainCompose = VaultComposeJob(fileID: "f1", template: "note-meeting",
                                              vault: "main", purpose: "")
    private let wikiCompose = VaultComposeJob(fileID: "f1", template: "note-lecture",
                                              vault: "wiki", purpose: "강의 재사용")

    private func note(_ name: String, edited: Bool = false) -> VaultExistingNote {
        VaultExistingNote(path: "/synthetic/vaults/Main/00. Inbox/\(name)", updatedAt: 1_760_000_000,
                          modifiedAfterSend: edited, obsidianURL: "obsidian://open?file=\(name)")
    }

    private func prompt(_ target: VaultRetryTarget, edited: Bool = false) -> VaultDuplicatePrompt {
        VaultDuplicatePrompt(target: target, existing: [note("20260914_회의.md", edited: edited),
                                                        note("20260914_회의 (2).md")])
    }

    func testMainSendAndComposeOfferEveryChoice() {
        for target in [VaultRetryTarget.send(mainSend), .compose(mainCompose)] {
            XCTAssertEqual(prompt(target).choices, [.openExisting, .overwrite, .createNew, .cancel])
            XCTAssertEqual(prompt(target).label(for: .createNew), "새로 만들기")
        }
    }

    func testWikiComposeOffersNoOverwriteAndExplainsNewVersion() {
        let wiki = prompt(.compose(wikiCompose))
        XCTAssertEqual(wiki.choices, [.openExisting, .createNew, .cancel])
        XCTAssertNil(wiki.onDuplicate(for: .overwrite), "the wiki is never overwritten from the UI")
        XCTAssertEqual(wiki.label(for: .createNew), "새 버전 만들기 (기존 버전은 그대로 둠)")
        XCTAssertTrue(wiki.message.contains("기존 버전은 보존됩니다"))
    }

    func testClaudeWindowOffersNoOverwrite() {
        let window = prompt(.send(windowSend), edited: true)
        XCTAssertEqual(window.choices, [.openExisting, .createNew, .cancel])
        XCTAssertNil(window.onDuplicate(for: .overwrite))
        XCTAssertTrue(window.message.contains("Claude Terminal"))
    }

    func testEditedNoteWarnsOnTheOverwriteButton() {
        let edited = prompt(.send(mainSend), edited: true)
        XCTAssertTrue(edited.overwritesEditedNote)
        XCTAssertEqual(edited.label(for: .overwrite), "덮어쓰기 — 볼트에서 수정된 노트를 덮어씀")
        XCTAssertTrue(edited.message.contains("수정 내용이 사라집니다"))

        let clean = prompt(.send(mainSend))
        XCTAssertFalse(clean.overwritesEditedNote)
        XCTAssertEqual(clean.label(for: .overwrite), "덮어쓰기")
        XCTAssertFalse(clean.message.contains("수정"))
    }

    func testOnlyTheNewestNoteDecidesTheWarning() {
        let olderEdited = VaultDuplicatePrompt(target: .send(mainSend),
                                               existing: [note("a.md"), note("b.md", edited: true)])
        XCTAssertFalse(olderEdited.overwritesEditedNote)
    }

    func testMessageNamesTheNewestNoteAndCountsTheRest() {
        XCTAssertTrue(prompt(.send(mainSend)).message.hasPrefix(
            "이 녹음의 노트가 이미 볼트에 있습니다: 20260914_회의.md 외 1개"))
    }

    func testArgvForEachChoice() {
        let send = prompt(.send(mainSend))
        XCTAssertNil(send.onDuplicate(for: .openExisting))
        XCTAssertNil(send.onDuplicate(for: .cancel), "Cancel writes nothing")
        XCTAssertEqual(send.target.arguments(onDuplicate: send.onDuplicate(for: .overwrite)!),
                       ["vault-send", "f1", "--to", "main", "--content", "integrated", "--via", "direct",
                        "--json", "--dest", "meetings", "--on-duplicate", "overwrite"])
        XCTAssertEqual(send.target.arguments(onDuplicate: send.onDuplicate(for: .createNew)!).suffix(2),
                       ["--on-duplicate", "new"])

        let wiki = prompt(.compose(wikiCompose))
        XCTAssertEqual(wiki.target.arguments(onDuplicate: wiki.onDuplicate(for: .createNew)!),
                       ["vault-compose", "--template", "note-lecture", "--to", "wiki",
                        "--collection-purpose", "강의 재사용", "--on-duplicate", "new",
                        "--json", "--", "f1"])
    }

    func testNoExistingNoteOffersNothingToOpenOrOverwrite() {
        let empty = VaultDuplicatePrompt(target: .send(mainSend), existing: [])
        XCTAssertEqual(empty.choices, [.createNew, .cancel])
        XCTAssertNil(empty.onDuplicate(for: .overwrite))
    }
}
