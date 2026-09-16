import Foundation

/// The inspector renders this in-memory snapshot. Markdown file access belongs
/// in its background load task, never in a SwiftUI body or clipboard action.
struct SlotOutputSnapshot {
    struct Output {
        let summary: String?
        let integrated: [Database.IntegratedKind: String]
        var hasIntegrated: Bool { integrated[.all] != nil }
    }

    let fileID: String
    let outputs: [String: Output]

    func output(for slotID: String, selectedID: String?) -> Output? {
        guard selectedID == fileID else { return nil }
        return outputs[slotID]
    }

    static func load(fileID: String, slots: [Database.Slot]) -> SlotOutputSnapshot {
        var outputs: [String: Output] = [:]
        guard !fileID.isEmpty else {
            return SlotOutputSnapshot(fileID: fileID, outputs: outputs)
        }
        for slot in slots {
            guard !Task.isCancelled else { break }
            let summary = Database.shared.summaryBody(
                fileID: fileID, model: slot.outputModel, template: slot.template
            )
            var integrated: [Database.IntegratedKind: String] = [:]
            for kind: Database.IntegratedKind in [.all, .transcript, .summary] {
                integrated[kind] = Database.shared.integratedBody(
                    fileID: fileID, model: slot.outputModel,
                    template: slot.template, kind: kind
                )
            }
            outputs[slot.id] = Output(summary: summary, integrated: integrated)
        }
        return SlotOutputSnapshot(fileID: fileID, outputs: outputs)
    }
}
