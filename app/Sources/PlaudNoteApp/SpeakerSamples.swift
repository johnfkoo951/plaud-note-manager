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
        let cmds = decode(cmdsSegmentsJSON, as: [CmdsSeg].self)
        let plaud = decode(plaudSegmentsJSON, as: [PlaudSeg].self)
        guard !cmds.isEmpty, !plaud.isEmpty else { return [:] }

        // overlap[cmdsSpeaker][plaudName] = milliseconds shared
        var overlap: [String: [String: Int]] = [:]
        var total: [String: Int] = [:]

        for c in cmds {
            guard let speaker = c.speaker, !speaker.isEmpty else { continue }
            let cStart = c.start_ms ?? 0
            let cEnd = max(cStart, c.end_ms ?? cStart)
            guard cEnd > cStart else { continue }
            total[speaker, default: 0] += cEnd - cStart

            for p in plaud {
                guard let raw = p.speaker ?? p.original_speaker, !raw.isEmpty else { continue }
                let name = raw.trimmingCharacters(in: .whitespaces)
                guard !isPlaceholderLabel(name) else { continue }
                let pStart = p.start_time ?? 0
                let pEnd = max(pStart, p.end_time ?? pStart)
                let shared = min(cEnd, pEnd) - max(cStart, pStart)
                if shared > 0 {
                    overlap[speaker, default: [:]][name, default: 0] += shared
                }
            }
        }

        var out: [String: Match] = [:]
        for (speaker, byName) in overlap {
            guard let best = byName.max(by: { $0.value < $1.value }) else { continue }
            let denominator = total[speaker] ?? 0
            guard denominator > 0 else { continue }
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
