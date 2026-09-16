import XCTest

@testable import PlaudNoteApp

final class SpeakerSamplesTests: XCTestCase {

    // MARK: - samples

    func testSamplesGroupsUtterancesPerSpeakerInOrder() {
        let json = """
        [
          {"start_ms": 0,     "end_ms": 2000,  "speaker": "speaker_0", "content": "안녕하세요"},
          {"start_ms": 2000,  "end_ms": 4000,  "speaker": "speaker_1", "content": "네 반갑습니다"},
          {"start_ms": 65000, "end_ms": 67000, "speaker": "speaker_0", "content": "시작하겠습니다"}
        ]
        """
        let out = SpeakerSamples.samples(segmentsJSON: json)
        XCTAssertEqual(out["speaker_0"]?.count, 2)
        XCTAssertEqual(out["speaker_0"]?.first?.text, "안녕하세요")
        XCTAssertEqual(out["speaker_0"]?.first?.timestamp, "0:00")
        XCTAssertEqual(out["speaker_0"]?.last?.timestamp, "1:05")
        XCTAssertEqual(out["speaker_1"]?.count, 1)
    }

    func testSamplesRespectsThePerSpeakerCap() {
        let segs = (0..<10).map {
            "{\"start_ms\": \($0 * 1000), \"speaker\": \"speaker_0\", \"content\": \"line \($0)\"}"
        }
        let out = SpeakerSamples.samples(segmentsJSON: "[\(segs.joined(separator: ","))]",
                                         maxPerSpeaker: 3)
        XCTAssertEqual(out["speaker_0"]?.count, 3)
        XCTAssertEqual(out["speaker_0"]?.last?.text, "line 2")
    }

    func testSamplesTruncatesLongUtterances() {
        let long = String(repeating: "가", count: 400)
        let json = "[{\"start_ms\": 0, \"speaker\": \"speaker_0\", \"content\": \"\(long)\"}]"
        let text = SpeakerSamples.samples(segmentsJSON: json, maxChars: 50)["speaker_0"]?
            .first?.text ?? ""
        XCTAssertTrue(text.hasSuffix("…"))
        XCTAssertEqual(text.count, 51)
    }

    func testSamplesSkipsBlankAndUnlabelledSegments() {
        let json = """
        [
          {"start_ms": 0, "speaker": "speaker_0", "content": "   "},
          {"start_ms": 1, "speaker": null,        "content": "orphan"},
          {"start_ms": 2, "speaker": "speaker_0", "content": "real"}
        ]
        """
        let out = SpeakerSamples.samples(segmentsJSON: json)
        XCTAssertEqual(out["speaker_0"]?.map(\.text), ["real"])
    }

    func testSamplesToleratesMissingFieldsAndBadJSON() {
        // A cached transcript may predate a field; one bad row must not void it.
        let partial = "[{\"speaker\": \"speaker_0\", \"content\": \"no timestamps\"}]"
        XCTAssertEqual(SpeakerSamples.samples(segmentsJSON: partial)["speaker_0"]?.first?.timestamp,
                       "0:00")
        XCTAssertTrue(SpeakerSamples.samples(segmentsJSON: "not json").isEmpty)
        XCTAssertTrue(SpeakerSamples.samples(segmentsJSON: "").isEmpty)
    }

    // MARK: - plaudMatches

    /// The real-world case: Plaud already diarized the same audio with real
    /// names, so overlapping timelines names the ElevenLabs speakers outright.
    func testPlaudMatchesNamesSpeakersFromOverlappingTimeline() {
        let cmds = """
        [
          {"start_ms": 0,     "end_ms": 10000, "speaker": "speaker_0", "content": "a"},
          {"start_ms": 10000, "end_ms": 20000, "speaker": "speaker_1", "content": "b"}
        ]
        """
        let plaud = """
        [
          {"start_time": 0,     "end_time": 10000, "speaker": "구요한", "content": "a"},
          {"start_time": 10000, "end_time": 20000, "speaker": "안창현", "content": "b"}
        ]
        """
        let out = SpeakerSamples.plaudMatches(cmdsSegmentsJSON: cmds, plaudSegmentsJSON: plaud)
        XCTAssertEqual(out["speaker_0"]?.name, "구요한")
        XCTAssertEqual(out["speaker_1"]?.name, "안창현")
        XCTAssertEqual(out["speaker_0"]?.share ?? 0, 1.0, accuracy: 0.001)
    }

    func testPlaudMatchesPicksTheDominantOverlap() {
        let cmds = "[{\"start_ms\": 0, \"end_ms\": 10000, \"speaker\": \"speaker_0\"}]"
        let plaud = """
        [
          {"start_time": 0,    "end_time": 8000,  "speaker": "구요한"},
          {"start_time": 8000, "end_time": 10000, "speaker": "안창현"}
        ]
        """
        let out = SpeakerSamples.plaudMatches(cmdsSegmentsJSON: cmds, plaudSegmentsJSON: plaud)
        XCTAssertEqual(out["speaker_0"]?.name, "구요한")
        XCTAssertEqual(out["speaker_0"]?.share ?? 0, 0.8, accuracy: 0.001)
    }

    func testPlaudMatchesStaysSilentWhenTheOverlapIsInconclusive() {
        // Evenly split across three labels — a coin flip is worse than nothing.
        let cmds = "[{\"start_ms\": 0, \"end_ms\": 9000, \"speaker\": \"speaker_0\"}]"
        let plaud = """
        [
          {"start_time": 0,    "end_time": 3000, "speaker": "A"},
          {"start_time": 3000, "end_time": 6000, "speaker": "B"},
          {"start_time": 6000, "end_time": 9000, "speaker": "C"}
        ]
        """
        let out = SpeakerSamples.plaudMatches(cmdsSegmentsJSON: cmds, plaudSegmentsJSON: plaud,
                                              minShare: 0.5)
        XCTAssertNil(out["speaker_0"])
    }

    func testPlaudMatchesIgnoresPlaudPlaceholderLabels() {
        // Suggesting "Speaker 4" as a real name is worse than suggesting nothing.
        let cmds = "[{\"start_ms\": 0, \"end_ms\": 10000, \"speaker\": \"speaker_0\"}]"
        let plaud = "[{\"start_time\": 0, \"end_time\": 10000, \"speaker\": \"Speaker 4\"}]"
        XCTAssertTrue(
            SpeakerSamples.plaudMatches(cmdsSegmentsJSON: cmds, plaudSegmentsJSON: plaud).isEmpty)
    }

    func testPlaudMatchesHandlesAMissingPlaudTranscript() {
        let cmds = "[{\"start_ms\": 0, \"end_ms\": 1000, \"speaker\": \"speaker_0\"}]"
        XCTAssertTrue(
            SpeakerSamples.plaudMatches(cmdsSegmentsJSON: cmds, plaudSegmentsJSON: "[]").isEmpty)
    }

    func testPlaceholderLabelDetection() {
        for placeholder in ["speaker_0", "Speaker 3", "화자 2", "SPK_1", "  ", "화자"] {
            XCTAssertTrue(SpeakerSamples.isPlaceholderLabel(placeholder), placeholder)
        }
        for name in ["구요한", "안창현", "Speaker Pelosi", "김화자"] {
            XCTAssertFalse(SpeakerSamples.isPlaceholderLabel(name), name)
        }
    }
}
