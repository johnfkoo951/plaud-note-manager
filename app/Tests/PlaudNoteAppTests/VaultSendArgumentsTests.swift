import XCTest
@testable import PlaudNoteApp

final class VaultSendArgumentsTests: XCTestCase {
    func testVaultSendAlwaysTargetsMainAndAsksOnDuplicate() {
        let args = VaultSendArguments.build(VaultSendRequest(fileID: "f1"), onDuplicate: "ask")
        XCTAssertEqual(args, ["vault-send", "f1", "--to", "main", "--content", "integrated",
                              "--via", "direct", "--json", "--on-duplicate", "ask"])
        XCTAssertFalse(args.contains("wiki"))
    }

    func testRetryKeepsEveryOriginalOptionInTodaysOrder() {
        let request = VaultSendRequest(fileID: "f1", dest: "meetings", content: "summary",
                                       via: "claude", model: "m", template: "t",
                                       withTranscript: true)
        XCTAssertEqual(
            VaultSendArguments.build(request, onDuplicate: "overwrite"),
            ["vault-send", "f1", "--to", "main", "--content", "summary", "--via", "claude", "--json",
             "--dest", "meetings", "--model", "m", "--template", "t", "--with-transcript",
             "--on-duplicate", "overwrite"]
        )
    }
}
