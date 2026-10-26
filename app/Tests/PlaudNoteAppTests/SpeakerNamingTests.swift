import XCTest

@testable import PlaudNoteApp

final class SpeakerNamingTests: XCTestCase {

    // A final transcript still numbered, the Plaud transcript already named.
    private let final = """
    [00:00:00] speaker_0: 생각해 보니까 잘하고 싶어졌어.
    [00:00:09] speaker_1: 너무 잘했다니까.
    [00:00:11] speaker_0: 욕심도 생기고.
    [00:00:16] speaker_1: 아이들 양육도 더 잘하고 싶고.
    """

    private let plaud = """
    [
      {"start_time": 0,     "end_time": 9000,  "speaker": "홍길동", "content": "…"},
      {"start_time": 9000,  "end_time": 11000, "speaker": "김철수", "content": "…"},
      {"start_time": 11000, "end_time": 16000, "speaker": "홍길동", "content": "…"},
      {"start_time": 16000, "end_time": 21000, "speaker": "김철수", "content": "…"}
    ]
    """

    func testTimestampsInEveryShapeTheTranscriptsUse() {
        XCTAssertEqual(SpeakerSamples.parseTimestamp("00:00:09"), 9000)
        XCTAssertEqual(SpeakerSamples.parseTimestamp("1:02:03"), 3_723_000)
        XCTAssertEqual(SpeakerSamples.parseTimestamp("2:03"), 123_000)
        XCTAssertNil(SpeakerSamples.parseTimestamp("ab:cd"))
        XCTAssertNil(SpeakerSamples.parseTimestamp("1:2:3:4"))
    }

    func testTranscriptLinesRunUntilTheNextLineCapped() {
        let spans = SpeakerSamples.spans(transcript: final + "\n[00:05:00] speaker_0: 한참 뒤.",
                                         maxLineMs: 30_000)
        XCTAssertEqual(spans.map(\.speaker), ["speaker_0", "speaker_1", "speaker_0", "speaker_1", "speaker_0"])
        XCTAssertEqual(spans[0].endMs, 9000)
        XCTAssertEqual(spans[3].endMs, 16000 + 30_000, "a long pause is not credited to the last speaker")
        XCTAssertEqual(spans[4].endMs, 300_000 + 30_000)
    }

    func testFinalLabelsAreNamedFromPlaudsTimeline() {
        let vm = SpeakerRecommender.naming(transcript: final, cmdsSegmentsJSON: "[]",
                                           plaudSegmentsJSON: plaud, proposal: [])
        XCTAssertEqual(vm.rows.map(\.label), ["speaker_0", "speaker_1"])
        XCTAssertEqual(vm.rows[0].suggestions.first?.name, "홍길동")
        XCTAssertEqual(vm.rows[0].suggestions.first?.source, .plaud)
        XCTAssertEqual(vm.rows[1].suggestions.first?.name, "김철수")
        XCTAssertEqual(vm.rows[0].lineCount, 2)
        XCTAssertEqual(vm.rows[0].sample, "생각해 보니까 잘하고 싶어졌어.")
        XCTAssertTrue(vm.hasPlaceholders)
    }

    func testRenamedCmdsTranscriptNamesTheFinalToo() {
        let cmds = """
        [{"start_ms": 0, "end_ms": 9000, "speaker": "홍길동", "content": "…"},
         {"start_ms": 9000, "end_ms": 11000, "speaker": "김철수", "content": "…"}]
        """
        let vm = SpeakerRecommender.naming(transcript: final, cmdsSegmentsJSON: cmds,
                                           plaudSegmentsJSON: "[]", proposal: [])
        XCTAssertEqual(vm.rows[0].suggestions.first?.source, .cmds)
        XCTAssertEqual(vm.rows[0].suggestions.first?.name, "홍길동")
    }

    func testSameNameFromTwoSourcesIsListedOnce() {
        let proposal = [SpeakerProposalVM(speaker: "speaker_0", name: "홍길동",
                                          confidence: 0.6, evidence: "")]
        let vm = SpeakerRecommender.naming(transcript: final, cmdsSegmentsJSON: "[]",
                                           plaudSegmentsJSON: plaud, proposal: proposal)
        XCTAssertEqual(vm.rows[0].suggestions.map(\.name), ["홍길동"])
        XCTAssertEqual(vm.rows[0].suggestions.first?.source, .plaud, "the stronger source wins")
    }

    func testPlaudPlaceholderLabelsAreNeverSuggested() {
        let numbered = plaud.replacingOccurrences(of: "홍길동", with: "Speaker 1")
        let vm = SpeakerRecommender.naming(transcript: final, cmdsSegmentsJSON: "[]",
                                           plaudSegmentsJSON: numbered, proposal: [])
        XCTAssertTrue(vm.rows[0].suggestions.isEmpty)
    }

    func testNamedLabelsAreNotPlaceholdersAndApplySendsOnlyChanges() {
        let named = final.replacingOccurrences(of: "speaker_0", with: "홍길동")
            .replacingOccurrences(of: "speaker_1", with: "김철수")
        let vm = SpeakerRecommender.naming(transcript: named, cmdsSegmentsJSON: "[]",
                                           plaudSegmentsJSON: plaud, proposal: [])
        XCTAssertFalse(vm.hasPlaceholders)
        XCTAssertTrue(vm.rows.allSatisfy { $0.suggestions.isEmpty }, "a name is not suggested to itself")

        let labels = vm.rows.map(\.label)
        XCTAssertEqual(SpeakerRecommender.mapping(names: ["홍길동": "홍길동", "김철수": " "],
                                                  labels: labels), [:])
        // A swap is two changes sent together.
        XCTAssertEqual(SpeakerRecommender.mapping(names: ["홍길동": "김철수", "김철수": "홍길동"],
                                                  labels: labels),
                       ["홍길동": "김철수", "김철수": "홍길동"])
    }
}
