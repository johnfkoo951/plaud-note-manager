import Foundation

/// `plaud config-glossary --json`: where the glossary and roster notes resolve.
struct GlossaryInfo: Decodable, Equatable {
    var glossaryPath: String
    var glossaryResolved: String
    var glossaryExists: Bool
    var glossaryDefault: String
    var contextPath: String
    var contextResolved: String
    var contextExists: Bool
    var contextDefault: String
    var autoUpdate: Bool

    enum CodingKeys: String, CodingKey {
        case glossaryPath = "glossary_path"
        case glossaryResolved = "glossary_resolved"
        case glossaryExists = "glossary_exists"
        case glossaryDefault = "glossary_default"
        case contextPath = "context_path"
        case contextResolved = "context_resolved"
        case contextExists = "context_exists"
        case contextDefault = "context_default"
        case autoUpdate = "auto_update"
    }

    static func decode(_ output: String) -> GlossaryInfo? {
        guard let start = output.firstIndex(of: "{") else { return nil }
        return try? JSONDecoder().decode(GlossaryInfo.self, from: Data(output[start...].utf8))
    }
}

/// Speaker naming and the glossary loop.
extension FileStore {

    /// Rename speakers across the recording — CMDS transcript and every final
    /// transcript — in one `plaud relabel` (no model call), then reload the
    /// detail pane so both tabs show the new names.
    func relabelSpeakers(_ fileID: String, mapping: [String: String]) async {
        let pairs = mapping
            .sorted { $0.key < $1.key }
            .compactMap { old, new -> String? in
                let name = new.trimmingCharacters(in: .whitespacesAndNewlines)
                return name.isEmpty || name == old ? nil : "\(old)=\(name)"
            }
        guard !pairs.isEmpty, !relabelingIDs.contains(fileID) else { return }
        relabelingIDs.insert(fileID)
        defer { relabelingIDs.remove(fileID) }
        _ = await runPlaudOutput(args: ["relabel", fileID] + pairs + ["--json"])
        if selectedID == fileID { reloadCmdsTranscript() }
    }

    /// Ask the model for speaker names (`plaud dual-speakers --refresh`); the
    /// proposal lands in the dual row the speaker bar reads.
    func proposeSpeakerNames(_ fileID: String) async {
        guard !speakerProposingIDs.contains(fileID) else { return }
        speakerProposingIDs.insert(fileID)
        defer { speakerProposingIDs.remove(fileID) }
        let output = await runPlaudOutput(
            args: ["dual-speakers", fileID, "--refresh", "--json"])
        if let start = output.firstIndex(of: "{"),
           let object = try? JSONSerialization.jsonObject(with: Data(output[start...].utf8))
            as? [String: Any],
           let reason = object["unavailable"] as? String, !reason.isEmpty {
            lastCommandError = "AI 추천을 받지 못했습니다 — \(reason)"
        }
        if selectedID == fileID { reloadCmdsTranscript() }
    }

    /// After a final transcript is made: add new names/terms to the glossary
    /// note. Runs in the background — the slot's spinner does not wait for it —
    /// and the CLI does nothing when auto-update is off or the recording was
    /// already harvested.
    func harvestGlossary(_ fileID: String) {
        Task {
            _ = await runPlaudOutput(
                args: ["glossary-harvest", fileID, "--auto", "--json"], showError: false)
        }
    }

    func glossaryInfo() async -> GlossaryInfo? {
        GlossaryInfo.decode(await runPlaudOutput(args: ["config-glossary", "--json"]))
    }

    /// nil leaves a setting alone; "" resets a path to its default.
    func setGlossary(path: String? = nil, context: String? = nil,
                     autoUpdate: Bool? = nil) async -> GlossaryInfo? {
        var args = ["config-glossary", "--json"]
        if let path { args += ["--path", path] }
        if let context { args += ["--context", context] }
        if let autoUpdate { args += ["--auto", autoUpdate ? "on" : "off"] }
        return GlossaryInfo.decode(await runPlaudOutput(args: args))
    }
}
