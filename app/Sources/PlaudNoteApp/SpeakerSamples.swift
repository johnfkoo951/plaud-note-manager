import Foundation

/// Deterministic hints for the dual-transcribe speaker confirmation sheet.
///
/// The LLM proposal is the nice path, but it fails whenever the provider is
/// unreachable — an exhausted subscription quota looks exactly like a revoked
/// token from here — and then the sheet used to offer three blank fields with
/// nothing to go on. Everything below is computed from transcripts already in
/// the database, so the sheet stays usable with no model at all.
///
/// Two independent hints:
///   1. `samples(...)` — what each ElevenLabs `speaker_N` actually said.
///   2. `plaudMatches(...)` — Plaud's own transcript often already carries real
///      names, so overlapping the two timelines names the speakers outright.
enum SpeakerSamples {

    // MARK: - Types

    struct Sample: Hashable {
        let timestamp: String
        let text: String
    }

    /// A Plaud speaker label that shares the timeline with a CMDS speaker.
    struct Match: Hashable {
        let name: String
        /// Fraction of this CMDS speaker's speaking time that overlaps `name`.
        let share: Double
    }

    // MARK: - Decoding

    /// ElevenLabs/CMDS segment. Every field is optional because a cached
    /// transcript may predate a field, and one bad row must not void the batch.
    private struct CmdsSeg: Decodable {
        let speaker: String?
        let start_ms: Int?
        let end_ms: Int?
        let content: String?
    }

    /// Plaud segment. Plaud uses `start_time`/`end_time`, and carries the
    /// display speaker in `speaker` with the raw label in `original_speaker`.
    private struct PlaudSeg: Decodable {
        let speaker: String?
        let original_speaker: String?
        let start_time: Int?
        let end_time: Int?
        let content: String?
    }

    private static func decode<T: Decodable>(_ json: String, as: [T].Type) -> [T] {
        guard let data = json.data(using: .utf8), !data.isEmpty else { return [] }
        return (try? JSONDecoder().decode([T].self, from: data)) ?? []
    }

    static func formatMinSec(_ seconds: Int) -> String {
        let s = max(0, seconds)
        return String(format: "%d:%02d", s / 60, s % 60)
    }

    // MARK: - 1. What each speaker said

    /// First `maxPerSpeaker` non-empty utterances per speaker, in transcript
    /// order. Mirrors `core/dual_speakers._cmds_sample` so the sheet shows the
    /// same evidence the model was given.
    static func samples(
        segmentsJSON: String,
        maxPerSpeaker: Int = 3,
        maxChars: Int = 120
    ) -> [String: [Sample]] {
        var out: [String: [Sample]] = [:]
        for seg in decode(segmentsJSON, as: [CmdsSeg].self) {
            guard let speaker = seg.speaker, !speaker.isEmpty else { continue }
            let text = (seg.content ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { continue }
            var bucket = out[speaker] ?? []
            guard bucket.count < maxPerSpeaker else { continue }
            bucket.append(
                Sample(
                    timestamp: formatMinSec((seg.start_ms ?? 0) / 1000),
                    text: text.count > maxChars ? String(text.prefix(maxChars)) + "…" : text
                )
            )
            out[speaker] = bucket
        }
        return out
    }

    // MARK: - 2. Who Plaud thinks that is

    /// Match each CMDS speaker to the Plaud speaker label sharing the most
    /// speaking time.
    ///
    /// Plaud diarizes the same audio and its labels are frequently already real
    /// names, so the overlap is usually decisive. Returns only matches at or
    /// above `minShare`; a speaker whose time is split evenly across several
    /// Plaud labels yields nothing rather than a coin-flip suggestion.
    ///
    /// Plaud's own placeholder labels ("Speaker 3") are skipped — echoing one
    /// back as a suggested real name would be worse than staying silent.
    static func plaudMatches(
        cmdsSegmentsJSON: String,
        plaudSegmentsJSON: String,
        minShare: Double = 0.34
    ) -> [String: Match] {
        overlapMatches(
            target: spans(cmdsSegmentsJSON: cmdsSegmentsJSON),
            reference: spans(plaudSegmentsJSON: plaudSegmentsJSON).filter {
                !isPlaceholderLabel($0.speaker)
            },
            minShare: minShare
        )
    }

    // MARK: - 3. Timelines of any transcript

    /// A stretch of speech attributed to one label.
    struct Span: Hashable {
        let speaker: String
        let startMs: Int
        let endMs: Int
    }

    static func spans(cmdsSegmentsJSON: String) -> [Span] {
        decode(cmdsSegmentsJSON, as: [CmdsSeg].self).compactMap { seg in
            guard let speaker = seg.speaker?.trimmingCharacters(in: .whitespaces),
                  !speaker.isEmpty else { return nil }
            let start = seg.start_ms ?? 0
            return Span(speaker: speaker, startMs: start, endMs: max(start, seg.end_ms ?? start))
        }
    }

    /// Plaud carries the display speaker in `speaker`, the raw label in
    /// `original_speaker`.
    static func spans(plaudSegmentsJSON: String) -> [Span] {
        decode(plaudSegmentsJSON, as: [PlaudSeg].self).compactMap { seg in
            guard let raw = seg.speaker ?? seg.original_speaker else { return nil }
            let speaker = raw.trimmingCharacters(in: .whitespaces)
            guard !speaker.isEmpty else { return nil }
            let start = seg.start_time ?? 0
            return Span(speaker: speaker, startMs: start, endMs: max(start, seg.end_time ?? start))
        }
    }

    /// Spans of a `[HH:MM:SS] label: …` transcript — the final transcript, or
    /// the CMDS one as the app formats it. A line carries only its start, so it
    /// runs until the next line starts — capped: a line is judged by its
    /// opening seconds, which is where its attribution is most reliable, and a
    /// long pause or monologue does not outweigh many short lines.
    static func spans(transcript: String, maxLineMs: Int = 12_000) -> [Span] {
        var starts: [(speaker: String, ms: Int)] = []
        for line in transcript.split(separator: "\n", omittingEmptySubsequences: true) {
            guard line.first == "[", let close = line.firstIndex(of: "]"),
                  let ms = parseTimestamp(line[line.index(after: line.startIndex)..<close])
            else { continue }
            let rest = line[line.index(after: close)...]
            guard let colon = rest.firstIndex(of: ":") else { continue }
            let speaker = rest[..<colon].trimmingCharacters(in: .whitespaces)
            guard !speaker.isEmpty else { continue }
            starts.append((speaker, ms))
        }
        return starts.enumerated().map { index, item in
            let next = index + 1 < starts.count ? starts[index + 1].ms : item.ms + maxLineMs
            let end = min(max(next, item.ms), item.ms + maxLineMs)
            return Span(speaker: item.speaker, startMs: item.ms, endMs: end)
        }
    }

    /// `1:02:03`, `02:03` or `2:03` → milliseconds; nil for anything else.
    static func parseTimestamp<S: StringProtocol>(_ raw: S) -> Int? {
        let parts = raw.trimmingCharacters(in: .whitespaces).split(separator: ":")
        guard (1...3).contains(parts.count) else { return nil }
        var seconds = 0.0
        for part in parts {
            guard let value = Double(part) else { return nil }
            seconds = seconds * 60 + value
        }
        return Int(seconds * 1000)
    }

    /// For each label in `target`, the `reference` label sharing the most of
    /// its speaking time — when that share reaches `minShare`. A speaker split
    /// evenly across several reference labels yields nothing rather than a
    /// coin flip.
    static func overlapMatches(
        target: [Span],
        reference: [Span],
        minShare: Double = 0.34
    ) -> [String: Match] {
        guard !target.isEmpty, !reference.isEmpty else { return [:] }
        let sortedRef = reference.sorted { $0.startMs < $1.startMs }

        // overlap[targetLabel][referenceName] = milliseconds shared
        var overlap: [String: [String: Int]] = [:]
        var total: [String: Int] = [:]
        for span in target where span.endMs > span.startMs {
            total[span.speaker, default: 0] += span.endMs - span.startMs
            for ref in sortedRef {
                if ref.startMs >= span.endMs { break }
                let shared = min(span.endMs, ref.endMs) - max(span.startMs, ref.startMs)
                if shared > 0 {
                    overlap[span.speaker, default: [:]][ref.speaker, default: 0] += shared
                }
            }
        }

        var out: [String: Match] = [:]
        for (speaker, byName) in overlap {
            guard let best = byName.max(by: { $0.value < $1.value }),
                  let denominator = total[speaker], denominator > 0 else { continue }
            let share = Double(best.value) / Double(denominator)
            if share >= minShare {
                out[speaker] = Match(name: best.key, share: min(1.0, share))
            }
        }
        return out
    }

    /// `speaker_0`, `Speaker 3`, `화자 2` — a diarizer's placeholder, not a name.
    static func isPlaceholderLabel(_ name: String) -> Bool {
        let lowered = name.lowercased().trimmingCharacters(in: .whitespaces)
        guard !lowered.isEmpty else { return true }
        for prefix in ["speaker_", "speaker ", "spk_", "spk ", "화자"] where lowered.hasPrefix(prefix) {
            let rest = lowered.dropFirst(prefix.count).trimmingCharacters(in: .whitespaces)
            if rest.isEmpty || Int(rest) != nil { return true }
        }
        return false
    }
}
