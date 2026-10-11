import XCTest
@testable import PlaudNoteApp

final class SlotModeDefaultsTests: XCTestCase {
    func testIntegratedKindOpensIntegratedBeforeAnyOutputExists() {
        XCTAssertTrue(SlotModeDefaults.opensIntegrated(kind: .integrated, hasIntegratedOutput: false))
        XCTAssertTrue(SlotModeDefaults.opensIntegrated(kind: .integrated, hasIntegratedOutput: true))
    }

    func testDeclaredSummaryKindOpensSummaryEvenWithStaleFusedOutput() {
        XCTAssertFalse(SlotModeDefaults.opensIntegrated(kind: .summary, hasIntegratedOutput: true))
        XCTAssertFalse(SlotModeDefaults.opensIntegrated(kind: .summary, hasIntegratedOutput: false))
        XCTAssertFalse(SlotModeDefaults.opensIntegrated(kind: .internalUse, hasIntegratedOutput: true))
    }

    func testMissingTemplateFallsBackToTheOutputRule() {
        XCTAssertTrue(SlotModeDefaults.opensIntegrated(kind: nil, hasIntegratedOutput: true))
        XCTAssertFalse(SlotModeDefaults.opensIntegrated(kind: nil, hasIntegratedOutput: false))
    }

    func testBatchActionFollowsKindAndNeverRunsInternal() {
        XCTAssertEqual(SlotModeDefaults.batchAction(kind: .integrated), .integrate)
        XCTAssertEqual(SlotModeDefaults.batchAction(kind: .summary), .summarize)
        XCTAssertEqual(SlotModeDefaults.batchAction(kind: .internalUse), .skip)
        XCTAssertEqual(SlotModeDefaults.batchAction(kind: nil), .integrate,
                       "a template missing from disk keeps the old Generate All behaviour")
    }

    func testCanGenerateRejectsMismatchedModes() {
        XCTAssertTrue(SlotModeDefaults.canGenerate(integratedMode: true, kind: .integrated))
        XCTAssertFalse(SlotModeDefaults.canGenerate(integratedMode: false, kind: .integrated))
        XCTAssertTrue(SlotModeDefaults.canGenerate(integratedMode: false, kind: .summary))
        XCTAssertFalse(SlotModeDefaults.canGenerate(integratedMode: true, kind: .summary))
    }

    func testCanGenerateNilAlwaysAndInternalNever() {
        for integratedMode in [true, false] {
            XCTAssertTrue(SlotModeDefaults.canGenerate(integratedMode: integratedMode, kind: nil))
            XCTAssertFalse(SlotModeDefaults.canGenerate(integratedMode: integratedMode,
                                                        kind: .internalUse))
        }
    }

    func testBlockedHelpNamesTheModeThatCanRun() {
        XCTAssertTrue(SlotModeDefaults.generateBlockedHelp(kind: .integrated).contains("Integrated 모드"))
        XCTAssertTrue(SlotModeDefaults.generateBlockedHelp(kind: .summary).contains("Summary 모드"))
        XCTAssertTrue(SlotModeDefaults.generateBlockedHelp(kind: .internalUse).contains("internal"))
    }

    func testFallbackSlotIsNoteGeneral() {
        XCTAssertEqual(Database.fallbackSlots, [
            Database.Slot(name: "Default (Claude)", model: "claude", template: "note-general"),
        ])
    }
}
