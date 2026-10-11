import Foundation

/// The send or compose a duplicate prompt re-runs with the user's answer.
enum VaultRetryTarget: Equatable {
    case send(VaultSendRequest)
    case compose(VaultComposeJob)

    var fileID: String {
        switch self {
        case .send(let request): return request.fileID
        case .compose(let job): return job.fileID
        }
    }

    /// A wiki raw source is immutable, and Claude Terminal filing writes the
    /// file itself; the CLI refuses `overwrite` for both.
    var allowsOverwrite: Bool {
        switch self {
        case .send(let request): return request.via != "claude-window"
        case .compose(let job): return job.vault != "wiki"
        }
    }

    var isWiki: Bool {
        if case .compose(let job) = self { return job.vault == "wiki" }
        return false
    }

    func arguments(onDuplicate: String) -> [String] {
        switch self {
        case .send(let request): return VaultSendArguments.build(request, onDuplicate: onDuplicate)
        case .compose(let job): return VaultComposeArguments.build(job, onDuplicate: onDuplicate)
        }
    }
}

/// Asked when a send or compose finds that the recording already has a note
/// in the vault. Nothing has been written at that point.
struct VaultDuplicatePrompt: Equatable, Identifiable {
    enum Choice: String, Identifiable {
        case openExisting, overwrite, createNew, cancel
        var id: String { rawValue }
    }

    let target: VaultRetryTarget
    /// Newest first; Overwrite rewrites the first one in place.
    let existing: [VaultExistingNote]

    var id: String { target.fileID }

    var title: String { "이미 볼트에 있는 녹음입니다" }

    var choices: [Choice] {
        var out: [Choice] = []
        if !existing.isEmpty { out.append(.openExisting) }
        if target.allowsOverwrite && !existing.isEmpty { out.append(.overwrite) }
        return out + [.createNew, .cancel]
    }

    /// The note Overwrite would replace was edited in the vault after it was sent.
    var overwritesEditedNote: Bool { existing.first?.modifiedAfterSend == true }

    func label(for choice: Choice) -> String {
        switch choice {
        case .openExisting:
            return "기존 노트 열기"
        case .overwrite:
            return overwritesEditedNote ? "덮어쓰기 — 볼트에서 수정된 노트를 덮어씀" : "덮어쓰기"
        case .createNew:
            return target.isWiki ? "새 버전 만들기 (기존 버전은 그대로 둠)" : "새로 만들기"
        case .cancel:
            return "취소"
        }
    }

    var message: String {
        var first = "이 녹음의 노트가 이미 볼트에 있습니다: \(existing.first?.filename ?? "")"
        if existing.count > 1 { first += " 외 \(existing.count - 1)개" }
        var lines = [first]
        if target.isWiki {
            lines.append("위키 Raw Source는 덮어쓸 수 없습니다. 새 버전을 만들면 기존 버전은 보존됩니다.")
        } else if !target.allowsOverwrite {
            lines.append("Claude Terminal 파일링은 덮어쓰기를 지원하지 않습니다. 새로 만들면 기존 노트는 그대로 둡니다.")
        } else if overwritesEditedNote {
            lines.append("가장 최근 노트는 보낸 뒤 볼트에서 수정되었습니다. 덮어쓰면 그 수정 내용이 사라집니다.")
        }
        return lines.joined(separator: "\n")
    }

    /// The `--on-duplicate` value a choice re-runs with; nil when it writes
    /// nothing (open, cancel) or is not allowed for this target.
    func onDuplicate(for choice: Choice) -> String? {
        switch choice {
        case .overwrite: return target.allowsOverwrite && !existing.isEmpty ? "overwrite" : nil
        case .createNew: return "new"
        case .openExisting, .cancel: return nil
        }
    }
}
