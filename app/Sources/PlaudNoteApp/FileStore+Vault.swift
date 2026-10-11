import AppKit
import Foundation

/// A note the CLI already wrote for the recording in the target vault — the
/// `existing` list of a `duplicate` result, newest first.
struct VaultExistingNote: Decodable, Equatable {
    var path: String
    var updatedAt: Int?
    /// The file changed after the app wrote it (edited in Obsidian).
    var modifiedAfterSend: Bool?
    var obsidianURL: String?

    enum CodingKeys: String, CodingKey {
        case path
        case updatedAt = "updated_at"
        case modifiedAfterSend = "modified_after_send"
        case obsidianURL = "obsidian_url"
    }

    var filename: String { (path as NSString).lastPathComponent }
}

/// Decoded `plaud vault-send --json` result. Note bodies never travel through
/// this payload — the CLI writes the file and reports only where it landed.
/// `vault-compose --json` (ComposeResult) decodes into it unchanged.
struct VaultSendOutcome: Decodable, Equatable {
    var status: String
    var detail: String?
    var path: String?
    var vault: String?
    var dest: String?
    var content: String?
    var via: String?
    var obsidianURL: String?
    /// `created` | `overwritten` | `skipped` on `ok`.
    var action: String?
    /// Earlier notes for the recording; filled on `duplicate`.
    var existing: [VaultExistingNote]?
    /// The recording this result belongs to. Set by the app, not decoded.
    var fileID: String = ""

    enum CodingKeys: String, CodingKey {
        case status, detail, path, vault, dest, content, via, action, existing
        case obsidianURL = "obsidian_url"
    }

    var noteFilename: String {
        (path as NSString?)?.lastPathComponent ?? ""
    }

    /// Whether the Actions strip shows this result while `selectedID` is
    /// selected: only a successful write, and only under its own recording
    /// (a compose can finish minutes after the user moved on).
    func showsFeedback(selectedID: String) -> Bool {
        status == "ok" && !noteFilename.isEmpty && !fileID.isEmpty && fileID == selectedID
    }

    /// Tag after the filename in the Actions strip; nil for a new note.
    var feedbackLabel: String? {
        switch action {
        case "overwritten": return "덮어씀"
        case "skipped": return "이미 있음"
        default: return nil
        }
    }

    /// Alert text for a failed `vault-send`. nil for `ok`, and for
    /// `duplicate`, which the duplicate dialog answers instead.
    var sendFailureMessage: String? {
        switch status {
        case "ok", "duplicate":
            return nil
        case "no_content":
            return "보낼 결과물이 없습니다 — Work Sidebar에서 Summary/Integrated를 먼저 Generate 해주세요."
        case "no_vault":
            return "볼트가 설정되지 않았습니다 — Settings 또는 `plaud config-vault`로 경로를 지정해 주세요."
        default:
            let detail = detail?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let suffix = detail.isEmpty ? "" : " — \(detail)"
            return "볼트 전송에 실패했습니다 (\(status))\(suffix)"
        }
    }
}

/// One `vault-send`, kept whole so a duplicate prompt can re-run it unchanged.
struct VaultSendRequest: Equatable {
    var fileID: String
    var dest = ""
    var content = "integrated"
    var via = "direct"
    var model = ""
    var template = ""
    var withTranscript = false
}

/// argv for `plaud vault-send`. It always targets the main vault: the wiki is
/// reached only through `vault-compose --to wiki` with a collection purpose.
enum VaultSendArguments {
    static func build(_ request: VaultSendRequest, onDuplicate: String) -> [String] {
        var args = ["vault-send", request.fileID, "--to", "main", "--content", request.content,
                    "--via", request.via, "--json"]
        if !request.dest.isEmpty { args += ["--dest", request.dest] }
        if !request.model.isEmpty { args += ["--model", request.model] }
        if !request.template.isEmpty { args += ["--template", request.template] }
        if request.withTranscript { args.append("--with-transcript") }
        return args + ["--on-duplicate", onDuplicate]
    }
}

extension FileStore {
    /// Send a recording's generated content into the main Obsidian vault via
    /// `plaud vault-send`. Content defaults to the integrated output (with
    /// summary/plaud fallback in the CLI). `via`:
    ///   direct        instant, no AI
    ///   claude        headless `claude -p` reformat (slow — minutes)
    ///   claude-window interactive Terminal filing (returns immediately)
    @discardableResult
    func vaultSend(
        _ fileID: String,
        dest: String = "",
        content: String = "integrated",
        via: String = "direct",
        model: String = "",
        template: String = "",
        withTranscript: Bool = false
    ) async -> Bool {
        await vaultSend(VaultSendRequest(
            fileID: fileID, dest: dest, content: content, via: via,
            model: model, template: template, withTranscript: withTranscript
        ))
    }

    /// `onDuplicate` is `ask` for a first attempt: if the recording already
    /// has a note in the vault, nothing is written and the duplicate dialog
    /// asks what to do, then re-runs this request with the answer.
    @discardableResult
    func vaultSend(_ request: VaultSendRequest, onDuplicate: String = "ask") async -> Bool {
        let fileID = request.fileID
        guard !fileID.isEmpty, !vaultSendingIDs.contains(fileID) else { return false }
        vaultSendingIDs.insert(fileID)
        defer { vaultSendingIDs.remove(fileID) }

        // Headless AI reformatting legitimately takes minutes; direct writes
        // are near-instant and claude-window returns as soon as Terminal opens.
        let timeout: TimeInterval = request.via == "claude" ? 960 : 60
        let output = await runPlaudOutput(
            args: VaultSendArguments.build(request, onDuplicate: onDuplicate),
            timeout: timeout, showError: false
        )
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)

        guard let data = trimmed.data(using: .utf8), !data.isEmpty,
              var outcome = try? JSONDecoder().decode(VaultSendOutcome.self, from: data)
        else {
            lastCommandError = trimmed.isEmpty
                ? "볼트 전송에 실패했습니다 — Plaud CLI에서 응답이 없습니다."
                : "볼트 전송 응답을 해석하지 못했습니다: \(trimmed.prefix(200))"
            return false
        }
        if outcome.status == "duplicate" {
            // The dialog answers this; an alert as well would compete with it.
            pendingVaultDuplicate = VaultDuplicatePrompt(
                target: .send(request), existing: outcome.existing ?? []
            )
            return false
        }
        if let message = outcome.sendFailureMessage {
            lastCommandError = message
            return false
        }
        lastCommandError = nil
        if request.via != "claude-window" {
            outcome.fileID = fileID
            lastVaultSend = outcome
        }
        if selectedID == fileID {
            noteMetadata = Database.shared.noteMetadata(for: fileID)
        }
        return true
    }

    /// Run a template over a recording and file the note in a vault via
    /// `plaud vault-compose` — the only way the app writes to the wiki.
    /// Generation runs a model (minutes, quota) unless a cached summary for
    /// the template exists; a duplicate is reported before any generation.
    /// Progress shows through `vaultSendingIDs`, the result in the Actions
    /// strip, failures in the global alert.
    @discardableResult
    func vaultCompose(_ job: VaultComposeJob, onDuplicate: String = "ask") async -> Bool {
        let fileID = job.fileID
        guard !fileID.isEmpty, !vaultSendingIDs.contains(fileID) else { return false }
        vaultSendingIDs.insert(fileID)
        defer { vaultSendingIDs.remove(fileID) }

        // With --json every status exits 0; a non-zero exit is a crash or a
        // timeout, and showError puts its stderr tail in the alert.
        let output = await runPlaudOutput(
            args: VaultComposeArguments.build(job, onDuplicate: onDuplicate),
            timeout: 960, showError: true
        )
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)

        guard let data = trimmed.data(using: .utf8), !data.isEmpty,
              var outcome = try? JSONDecoder().decode(VaultSendOutcome.self, from: data)
        else {
            if lastCommandError == nil {
                lastCommandError = trimmed.isEmpty
                    ? "볼트 작성에 실패했습니다 — Plaud CLI에서 응답이 없습니다."
                    : "볼트 작성 응답을 해석하지 못했습니다: \(trimmed.prefix(200))"
            }
            return false
        }
        if outcome.status == "duplicate" {
            pendingVaultDuplicate = VaultDuplicatePrompt(
                target: .compose(job), existing: outcome.existing ?? []
            )
            return false
        }
        if let message = VaultComposeOutcome.errorMessage(status: outcome.status,
                                                          detail: outcome.detail) {
            lastCommandError = message
            return false
        }
        // Success leaves lastCommandError alone: clearing it here would
        // dismiss an unrelated alert raised while this ran.
        outcome.fileID = fileID
        lastVaultSend = outcome
        if selectedID == fileID {
            noteMetadata = Database.shared.noteMetadata(for: fileID)
        }
        return true
    }

    /// Carry out the answer to a duplicate prompt. Open and Cancel write
    /// nothing; Overwrite and New re-run the original request with that
    /// `--on-duplicate` value.
    func resolveVaultDuplicate(_ prompt: VaultDuplicatePrompt,
                               choice: VaultDuplicatePrompt.Choice) {
        pendingVaultDuplicate = nil
        if choice == .openExisting, let note = prompt.existing.first {
            openNote(obsidianURL: note.obsidianURL, path: note.path)
            return
        }
        guard let policy = prompt.onDuplicate(for: choice) else { return }
        Task {
            switch prompt.target {
            case .send(let request): await vaultSend(request, onDuplicate: policy)
            case .compose(let job): await vaultCompose(job, onDuplicate: policy)
            }
        }
    }

    /// Open a sent note in Obsidian via its obsidian:// URL.
    func openVaultNote(_ outcome: VaultSendOutcome) {
        openNote(obsidianURL: outcome.obsidianURL, path: outcome.path)
    }

    private func openNote(obsidianURL: String?, path: String?) {
        if let raw = obsidianURL, let url = URL(string: raw) {
            NSWorkspace.shared.open(url)
        } else if let path {
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
        }
    }
}
