import XCTest
@testable import PlaudNoteApp

final class VaultComposeArgumentsTests: XCTestCase {
    private func job(_ fileID: String = "f1", template: String, vault: String,
                     purpose: String) -> VaultComposeJob {
        VaultComposeJob(fileID: fileID, template: template, vault: vault, purpose: purpose)
    }

    func testMainVaultOmitsCollectionPurpose() {
        XCTAssertEqual(
            VaultComposeArguments.build(job(template: "note-meeting", vault: "main", purpose: "ignored"),
                                        onDuplicate: "ask"),
            ["vault-compose", "--template", "note-meeting", "--to", "main",
             "--on-duplicate", "ask", "--json", "--", "f1"]
        )
    }

    func testWikiCarriesTrimmedCollectionPurpose() {
        XCTAssertEqual(
            VaultComposeArguments.build(job(template: "note-lecture", vault: "wiki",
                                            purpose: "  강의 설계 재사용 \n"),
                                        onDuplicate: "new"),
            ["vault-compose", "--template", "note-lecture", "--to", "wiki",
             "--collection-purpose", "강의 설계 재사용", "--on-duplicate", "new", "--json", "--", "f1"]
        )
    }

    func testPurposeThatLooksLikeAFlagStaysAValue() {
        let args = VaultComposeArguments.build(job(template: "note-general", vault: "wiki",
                                                   purpose: "--json"),
                                               onDuplicate: "ask")
        let at = try! XCTUnwrap(args.firstIndex(of: "--collection-purpose"))
        XCTAssertEqual(args[at + 1], "--json")
        XCTAssertEqual(Array(args.suffix(3)), ["--json", "--", "f1"])
    }

    func testFileIDFollowsOptionTerminator() {
        for vault in ["main", "wiki"] {
            let args = VaultComposeArguments.build(job("-x", template: "note-general", vault: vault,
                                                       purpose: "p"),
                                                   onDuplicate: "ask")
            XCTAssertEqual(Array(args.suffix(2)), ["--", "-x"])
            XCTAssertEqual(args.filter { $0 == "-x" }.count, 1)
        }
    }

    func testCanSubmitRequiresPurposeOnlyForWiki() {
        XCTAssertTrue(VaultComposeArguments.requiresPurpose(vault: "wiki"))
        XCTAssertFalse(VaultComposeArguments.requiresPurpose(vault: "main"))
        XCTAssertFalse(VaultComposeArguments.canSubmit(template: "note-meeting", vault: "wiki", purpose: "  \n"))
        XCTAssertTrue(VaultComposeArguments.canSubmit(template: "note-meeting", vault: "wiki", purpose: "p"))
        XCTAssertTrue(VaultComposeArguments.canSubmit(template: "note-meeting", vault: "main", purpose: ""))
        XCTAssertFalse(VaultComposeArguments.canSubmit(template: "", vault: "main", purpose: "p"))
        XCTAssertFalse(VaultComposeArguments.canSubmit(template: "note-meeting", vault: "", purpose: "p"))
    }

    func testInitialVaultDefaultsToMain() {
        XCTAssertEqual(VaultComposeArguments.initialVault(preset: nil, available: ["main", "wiki"]), "main")
        XCTAssertEqual(VaultComposeArguments.initialVault(preset: nil, available: ["wiki"]), "wiki")
        XCTAssertEqual(VaultComposeArguments.initialVault(preset: nil, available: []), "")
    }

    func testInitialVaultHonoursWikiPreset() {
        XCTAssertEqual(VaultComposeArguments.initialVault(preset: "wiki", available: ["main", "wiki"]), "wiki")
    }

    func testWikiPresetWithoutWikiDisablesSubmit() {
        // A wiki entry point never quietly becomes a main-vault compose.
        let vault = VaultComposeArguments.initialVault(preset: "wiki", available: ["main"])
        XCTAssertEqual(vault, "")
        XCTAssertFalse(VaultComposeArguments.canSubmit(template: "note-meeting", vault: vault, purpose: "p"))
        XCTAssertEqual(VaultComposeArguments.initialVault(preset: "wiki", available: []), "")
    }

    func testDecodesVaultsJSON() throws {
        let json = """
            [{"key": "main", "label": "Main vault", "name": "Main", "path": "/synthetic/vaults/Main"},
             {"key": "wiki", "label": "LLM Wiki", "name": "Wiki", "path": "/synthetic/vaults/Wiki"}]
            """
        let choices = try XCTUnwrap(VaultChoices.decode(Data(json.utf8)))
        XCTAssertEqual(choices.map(\.key), ["main", "wiki"])
        XCTAssertEqual(choices[1], VaultChoice(key: "wiki", label: "LLM Wiki", name: "Wiki",
                                               path: "/synthetic/vaults/Wiki"))
        XCTAssertEqual(choices[0].id, "main")
    }

    func testNoVaultConfiguredIsNotACLIFailure() {
        let none = VaultChoices.interpret(exitCode: 0, stdout: "[]\n", stderr: "")
        XCTAssertEqual(none.choices, [])
        XCTAssertNil(none.error)

        let crash = VaultChoices.interpret(
            exitCode: 1, stdout: "",
            stderr: "Traceback (most recent call last):\n  ...\nValueError: boom\n\n")
        XCTAssertEqual(crash.choices, [])
        XCTAssertEqual(crash.error, "볼트 목록을 불러오지 못했습니다 — ValueError: boom")

        let timeout = VaultChoices.interpret(exitCode: -1, stdout: "",
                                             stderr: "plaud command timed out after 30s")
        XCTAssertTrue(timeout.error?.contains("timed out") == true)

        let silent = VaultChoices.interpret(exitCode: 2, stdout: "", stderr: "")
        XCTAssertEqual(silent.error, "볼트 목록을 불러오지 못했습니다 — exit 2")

        let garbage = VaultChoices.interpret(exitCode: 0, stdout: "not json", stderr: "")
        XCTAssertEqual(garbage.choices, [])
        XCTAssertNotNil(garbage.error)
    }

    func testComposeResultDecodesIntoVaultSendOutcome() throws {
        let json = """
            {"status": "ok", "detail": "raw source written", "template": "note-lecture",
             "path": "/synthetic/vaults/Wiki/10. Raw Sources/lecture/2026-09-14-x.md",
             "vault": "/synthetic/vaults/Wiki", "dest": "10. Raw Sources/lecture",
             "obsidian_url": "obsidian://open?vault=Wiki&file=x"}
            """
        let outcome = try JSONDecoder().decode(VaultSendOutcome.self, from: Data(json.utf8))
        XCTAssertEqual(outcome.status, "ok")
        XCTAssertEqual(outcome.noteFilename, "2026-09-14-x.md")
        XCTAssertNotNil(outcome.obsidianURL)
        XCTAssertEqual(outcome.fileID, "", "fileID is set by the app, never decoded")
    }

    func testCostCaptionNamesComposesOwnModelAndConditionalReuse() {
        let main = VaultComposeCaption.cost(vault: "main")
        XCTAssertTrue(main.contains("Metadata model"))
        XCTAssertFalse(main.contains("Cross-analysis"))

        let wiki = VaultComposeCaption.cost(vault: "wiki")
        XCTAssertTrue(wiki.contains("Cross-analysis model"))
        XCTAssertTrue(wiki.contains("비어 있으면 Metadata model"), "dual_model falls back to metadata_model")

        for caption in [main, wiki, VaultComposeCaption.cost(vault: "")] {
            // Reuse is conditional on compose's own model, as in _summarize_with.
            XCTAssertTrue(caption.contains("그 모델로 만든 같은 템플릿 요약이 있을 때만 재사용"), caption)
            XCTAssertTrue(caption.contains("새로 실행"), caption)
            XCTAssertTrue(caption.contains("슬롯이 다른 모델로 만든 요약은 재사용되지 않습니다"), caption)
            // The app-03 wording promised reuse of any summary for the template.
            XCTAssertFalse(caption.contains("요약이 이미 있으면 그것을 재사용"), caption)
        }
        XCTAssertNotEqual(main, wiki)
    }

    func testErrorMessageCoversEveryComposeStatus() {
        XCTAssertNil(VaultComposeOutcome.errorMessage(status: "ok", detail: "written"))
        let statuses = ["needs_purpose", "no_vault", "no_transcript", "model_failed",
                        "schema_error", "no_content", "write_failed", "bad_template",
                        "invalid_option"]
        var seen = Set<String>()
        for status in statuses {
            let message = VaultComposeOutcome.errorMessage(status: status, detail: nil)
            XCTAssertNotNil(message, status)
            XCTAssertFalse(message!.hasPrefix("볼트 작성에 실패했습니다 ("),
                           "\(status) has its own message")
            seen.insert(message!)
        }
        XCTAssertEqual(seen.count, statuses.count, "each status reads differently")
        XCTAssertEqual(VaultComposeOutcome.errorMessage(status: "wiki_unsupported", detail: nil),
                       "볼트 작성에 실패했습니다 (wiki_unsupported)")
        XCTAssertEqual(VaultComposeOutcome.errorMessage(status: "wiki_unsupported", detail: "  into the wiki  "),
                       "볼트 작성에 실패했습니다 (wiki_unsupported) — into the wiki")
        XCTAssertTrue(VaultComposeOutcome.errorMessage(status: "model_failed",
                                                       detail: "quota exceeded")!
            .hasSuffix(" — quota exceeded"))
        XCTAssertTrue(VaultComposeOutcome.errorMessage(status: "no_vault", detail: nil)!
            .contains("uv run plaud config-wiki-vault"))
    }

    func testFeedbackShowsOnlyForItsRecording() {
        var outcome = VaultSendOutcome(status: "ok", path: "/synthetic/vaults/Main/00. Inbox/n.md")
        outcome.fileID = "a"
        XCTAssertTrue(outcome.showsFeedback(selectedID: "a"))
        XCTAssertFalse(outcome.showsFeedback(selectedID: "b"))

        var unowned = outcome
        unowned.fileID = ""
        XCTAssertFalse(unowned.showsFeedback(selectedID: ""))
        XCTAssertFalse(unowned.showsFeedback(selectedID: "a"))

        var failed = outcome
        failed.status = "write_failed"
        XCTAssertFalse(failed.showsFeedback(selectedID: "a"))

        var nameless = outcome
        nameless.path = nil
        XCTAssertFalse(nameless.showsFeedback(selectedID: "a"))
    }
}
