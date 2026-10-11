import XCTest
@testable import PlaudNoteApp

final class VaultSendArgumentsTests: XCTestCase {
    func testVaultSendAlwaysTargetsMain() {
        let args = VaultSendArguments.build(fileID: "f1")
        XCTAssertEqual(args, ["vault-send", "f1", "--to", "main", "--content", "integrated",
                              "--via", "direct", "--json"])
        XCTAssertFalse(args.contains("wiki"))
    }

    func testVaultSendOptionalFlagsKeepTodaysOrder() {
        XCTAssertEqual(
            VaultSendArguments.build(fileID: "f1", dest: "meetings", content: "summary",
                                     via: "claude", model: "m", template: "t",
                                     withTranscript: true),
            ["vault-send", "f1", "--to", "main", "--content", "summary", "--via", "claude", "--json",
             "--dest", "meetings", "--model", "m", "--template", "t", "--with-transcript"]
        )
    }
}
