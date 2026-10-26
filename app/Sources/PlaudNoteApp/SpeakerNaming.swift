import SwiftUI

/// One suggested real name for a speaker label, and where it came from.
struct SpeakerSuggestion: Hashable {
    enum Source: String {
        /// Plaud's own transcript shares this stretch of time and names it.
        case plaud = "Plaud"
        /// The CMDS transcript, already renamed, shares this stretch of time.
        case cmds = "CMDS"
        /// The model's proposal (`plaud dual-speakers`).
        case ai = "AI"
    }

    let name: String
    /// 0…1 — share of speaking time for the timeline sources, the model's own
    /// confidence for `.ai`.
    let confidence: Double
    let source: Source

    var caption: String { "\(source.rawValue) \(Int((confidence * 100).rounded()))%" }
}

/// What the speaker bar shows for one displayed transcript.
struct SpeakerNamingVM: Hashable {
    struct Row: Hashable, Identifiable {
        let label: String
        /// The first thing this label said — enough to tell people apart.
        let sample: String
        let lineCount: Int
        /// Best first, one per name.
        let suggestions: [SpeakerSuggestion]

        var id: String { label }
        var isPlaceholder: Bool { SpeakerSamples.isPlaceholderLabel(label) }
    }

    var rows: [Row] = []
    var hasPlaceholders: Bool { rows.contains(where: \.isPlaceholder) }
}

/// Deterministic speaker-name suggestions. Needs no model: Plaud's transcript
/// usually already names people, and a renamed CMDS transcript names the same
/// stretches of time a final transcript still calls `speaker_0`.
enum SpeakerRecommender {

    static func naming(
        transcript: String,
        cmdsSegmentsJSON: String,
        plaudSegmentsJSON: String,
        proposal: [SpeakerProposalVM]
    ) -> SpeakerNamingVM {
        let target = SpeakerSamples.spans(transcript: transcript)
        let named: ([SpeakerSamples.Span]) -> [SpeakerSamples.Span] = {
            $0.filter { !SpeakerSamples.isPlaceholderLabel($0.speaker) }
        }
        let byPlaud = SpeakerSamples.overlapMatches(
            target: target, reference: named(SpeakerSamples.spans(plaudSegmentsJSON: plaudSegmentsJSON)))
        let byCmds = SpeakerSamples.overlapMatches(
            target: target, reference: named(SpeakerSamples.spans(cmdsSegmentsJSON: cmdsSegmentsJSON)))
        let byModel = Dictionary(
            proposal.filter { !$0.name.trimmingCharacters(in: .whitespaces).isEmpty }
                .map { ($0.speaker, $0) },
            uniquingKeysWith: { first, _ in first })

        var order: [String] = []
        var counts: [String: Int] = [:]
        var samples: [String: String] = [:]
        for (label, body) in labelledLines(transcript) {
            if counts[label] == nil { order.append(label) }
            counts[label, default: 0] += 1
            if samples[label] == nil, !body.isEmpty {
                samples[label] = body.count > 48 ? String(body.prefix(48)) + "…" : body
            }
        }

        let rows = order.map { label -> SpeakerNamingVM.Row in
            var found: [SpeakerSuggestion] = []
            if let m = byPlaud[label] { found.append(.init(name: m.name, confidence: m.share, source: .plaud)) }
            if let m = byCmds[label] { found.append(.init(name: m.name, confidence: m.share, source: .cmds)) }
            if let p = byModel[label] {
                found.append(.init(name: p.name.trimmingCharacters(in: .whitespaces),
                                   confidence: p.confidence, source: .ai))
            }
            var seen = Set<String>()
            let suggestions = found
                .sorted { $0.confidence > $1.confidence }
                .filter { $0.name != label && seen.insert($0.name).inserted }
            return SpeakerNamingVM.Row(label: label, sample: samples[label] ?? "",
                                       lineCount: counts[label] ?? 0, suggestions: suggestions)
        }
        return SpeakerNamingVM(rows: rows)
    }

    /// Which name each label's field starts out with. An empty string leaves
    /// that label as it is.
    ///
    /// TODO(human): this is the rule that decides what one click of 적용 does,
    /// so it is yours to set. Things to decide:
    ///   - a minimum confidence below which a field stays empty;
    ///   - which source wins when Plaud, CMDS and AI disagree;
    ///   - two labels suggesting the same name — diarization sometimes splits
    ///     one person in two (prefill both = merge them), but more often only
    ///     one of them is really that person.
    /// The placeholder below takes each label's top suggestion, nothing more.
    static func prefill(_ rows: [SpeakerNamingVM.Row]) -> [String: String] {
        var out: [String: String] = [:]
        for row in rows {
            out[row.label] = row.isPlaceholder ? (row.suggestions.first?.name ?? "") : row.label
        }
        return out
    }

    /// `(label, body)` for every `[ts] label: body` line.
    static func labelledLines(_ transcript: String) -> [(String, String)] {
        transcript.split(separator: "\n", omittingEmptySubsequences: true).compactMap { line in
            guard line.first == "[", let close = line.firstIndex(of: "]") else { return nil }
            let rest = line[line.index(after: close)...]
            guard let colon = rest.firstIndex(of: ":") else { return nil }
            let label = rest[..<colon].trimmingCharacters(in: .whitespaces)
            guard !label.isEmpty else { return nil }
            let body = rest[rest.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            return (label, body)
        }
    }

    /// The pairs Apply sends: changed, non-empty names only.
    static func mapping(names: [String: String], labels: [String]) -> [String: String] {
        var out: [String: String] = [:]
        for label in labels {
            let name = (names[label] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if !name.isEmpty, name != label { out[label] = name }
        }
        return out
    }
}

/// Name every speaker of a recording in one go.
///
/// Sits above a transcript (Final or CMDS). Suggested names are filled in;
/// one Apply renames the speakers across the CMDS transcript and every final
/// transcript at once (`plaud relabel`, no model call). It stays available
/// after naming, so a wrong name — or two swapped ones — can be fixed later.
struct SpeakerNamingBar: View {
    @ObservedObject var store: FileStore
    let fileID: String
    let transcript: String

    @State private var naming = SpeakerNamingVM()
    @State private var names: [String: String] = [:]
    @State private var roster: [Database.Speaker] = []
    @State private var editing = false

    private struct LoadKey: Equatable {
        let fileID: String
        let transcript: String
        let proposal: [SpeakerProposalVM]
    }

    private var proposal: [SpeakerProposalVM] {
        store.dualState?.fileID == fileID ? (store.dualState?.proposal ?? []) : []
    }

    private var labels: [String] { naming.rows.map(\.label) }
    private var pending: [String: String] { SpeakerRecommender.mapping(names: names, labels: labels) }
    private var isApplying: Bool { store.relabelingIDs.contains(fileID) }
    private var isProposing: Bool { store.speakerProposingIDs.contains(fileID) }

    /// The model only proposes for CMDS speaker numbers, and only helps when
    /// the timelines left a numbered speaker without any suggestion.
    private var canAskModel: Bool {
        naming.rows.contains { $0.isPlaceholder && $0.suggestions.isEmpty }
            && store.cmdsTranscript?.isEmpty == false
    }

    var body: some View {
        Group {
            if naming.rows.isEmpty {
                EmptyView()
            } else if editing || naming.hasPlaceholders {
                editor
            } else {
                summary
            }
        }
        .task(id: LoadKey(fileID: fileID, transcript: transcript, proposal: proposal)) {
            let key = LoadKey(fileID: fileID, transcript: transcript, proposal: proposal)
            let loaded = await Task.detached(priority: .userInitiated) {
                let segments = Database.shared.speakerSegmentsJSON(for: key.fileID)
                return (
                    SpeakerRecommender.naming(transcript: key.transcript,
                                              cmdsSegmentsJSON: segments.cmds,
                                              plaudSegmentsJSON: segments.plaud,
                                              proposal: key.proposal),
                    Database.shared.savedSpeakers()
                )
            }.value
            guard !Task.isCancelled else { return }
            let labelsChanged = loaded.0.rows.map(\.label) != labels
            naming = loaded.0
            roster = loaded.1
            if labelsChanged {
                names = SpeakerRecommender.prefill(loaded.0.rows)
                editing = false
            } else {
                // A new proposal arrived: fill only the fields still empty.
                for (label, name) in SpeakerRecommender.prefill(loaded.0.rows)
                where (names[label] ?? "").isEmpty {
                    names[label] = name
                }
            }
        }
    }

    // MARK: Collapsed

    private var summary: some View {
        HStack(spacing: 6) {
            Image(systemName: "person.2")
                .foregroundStyle(.secondary)
            Text("화자")
                .font(AppUI.sectionFont)
                .foregroundStyle(.secondary)
            Text(labels.joined(separator: " · "))
                .font(AppUI.metaFont)
                .lineLimit(1)
                .truncationMode(.tail)
            Spacer(minLength: 4)
            Button("이름 바꾸기") { editing = true }
                .buttonStyle(.borderless)
                .font(AppUI.metaFont)
                .help("이름을 고치거나 서로 바꿉니다. CMDS 전사와 모든 최종본에 함께 반영됩니다.")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: AppUI.tightRadius))
    }

    // MARK: Expanded

    private var editor: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: "person.2.badge.gearshape")
                    .foregroundStyle(AppUI.accentPink)
                Text("화자 이름")
                    .font(AppUI.sectionFont)
                Spacer(minLength: 4)
                if isProposing {
                    ProgressView().controlSize(.small)
                    Text("AI가 추천하는 중…").font(AppUI.metaFont).foregroundStyle(.secondary)
                } else if canAskModel {
                    Button {
                        Task { await store.proposeSpeakerNames(fileID) }
                    } label: {
                        Label("AI 추천", systemImage: "sparkles")
                    }
                    .buttonStyle(.borderless)
                    .font(AppUI.metaFont)
                    .help("발화 내용과 용어집을 보고 모델이 이름을 추천합니다 (모델 호출 1회).")
                }
                if editing && !naming.hasPlaceholders {
                    Button("닫기") {
                        names = SpeakerRecommender.prefill(naming.rows)
                        editing = false
                    }
                    .buttonStyle(.borderless)
                    .font(AppUI.metaFont)
                }
                Button {
                    IMETextField.finishEditing()
                    let mapping = pending
                    Task { await store.relabelSpeakers(fileID, mapping: mapping) }
                } label: {
                    if isApplying {
                        ProgressView().controlSize(.small)
                    } else {
                        Text(pending.isEmpty ? "적용" : "적용 (\(pending.count))")
                    }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(pending.isEmpty || isApplying)
                .help("CMDS 전사와 이 녹음의 모든 최종본에서 화자 이름을 한 번에 바꿉니다 (모델 호출 없음).")
            }

            ForEach(naming.rows) { row in
                speakerRow(row)
            }

            Text(caption)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(10)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: AppUI.tightRadius))
    }

    private var caption: String {
        if naming.rows.contains(where: { !$0.suggestions.isEmpty }) && naming.hasPlaceholders {
            return "추천 이름을 미리 채웠습니다. 확인하고 적용하면 한 번에 반영됩니다. 빈 칸은 번호를 그대로 둡니다."
        }
        if naming.hasPlaceholders {
            return "이름을 입력하거나 ▾에서 고르세요. 빈 칸은 번호를 그대로 둡니다."
        }
        return "같은 사람이면 같은 이름을, 뒤바뀌었으면 서로 바꿔 적으세요. 한 번에 반영됩니다."
    }

    private func speakerRow(_ row: SpeakerNamingVM.Row) -> some View {
        HStack(spacing: 8) {
            Text(row.label)
                .font(.system(size: 11.5, design: row.isPlaceholder ? .monospaced : .default))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
                .frame(width: 92, alignment: .leading)
            Image(systemName: "arrow.right")
                .font(.caption2)
                .foregroundStyle(.tertiary)
            IMETextField(row.isPlaceholder ? "이름" : row.label, text: binding(for: row.label))
                .textFieldStyle(.roundedBorder)
                .frame(width: 128)
            choiceMenu(row)
            if let best = row.suggestions.first {
                Text(best.caption)
                    .font(.system(size: 10.5, weight: .semibold))
                    .foregroundStyle(best.confidence >= 0.7 ? Color.green : .orange)
                    .help(row.suggestions.map(\.caption).joined(separator: " · "))
            }
            Text(row.sample.isEmpty ? "" : "“\(row.sample)”")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .lineLimit(1)
                .truncationMode(.tail)
                .help("\(row.lineCount)줄")
            Spacer(minLength: 0)
        }
    }

    private func choiceMenu(_ row: SpeakerNamingVM.Row) -> some View {
        Menu {
            if !row.suggestions.isEmpty {
                Section("추천") {
                    ForEach(row.suggestions, id: \.self) { s in
                        Button("\(s.name)  ·  \(s.caption)") { names[row.label] = s.name }
                    }
                }
            }
            if !roster.isEmpty {
                Section("저장된 화자") {
                    ForEach(roster) { sp in
                        Button(sp.isSelf ? "★ \(sp.name)" : sp.name) { names[row.label] = sp.name }
                    }
                }
            }
            Divider()
            Button(row.isPlaceholder ? "번호 그대로 두기" : "바꾸지 않기") {
                names[row.label] = row.isPlaceholder ? "" : row.label
            }
        } label: {
            Image(systemName: "chevron.down.circle")
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .help("추천·저장된 화자에서 고르기")
    }

    private func binding(for label: String) -> Binding<String> {
        Binding(
            get: { names[label] ?? "" },
            set: { names[label] = $0 }
        )
    }
}
