import AppKit
import Foundation

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
    /// The recording this result belongs to. Set by the app, not decoded.
    var fileID: String = ""

    enum CodingKeys: String, CodingKey {
        case status, detail, path, vault, dest, content, via
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
}

/// argv for `plaud vault-send`. It always targets the main vault: the wiki is
/// reached only through `vault-compose --to wiki` with a collection purpose.
enum VaultSendArguments {
    static func build(fileID: String, dest: String = "", content: String = "integrated",
                      via: String = "direct", model: String = "", template: String = "",
                      withTranscript: Bool = false) -> [String] {
        var args = ["vault-send", fileID, "--to", "main", "--content", content, "--via", via, "--json"]
        if !dest.isEmpty { args += ["--dest", dest] }
        if !model.isEmpty { args += ["--model", model] }
        if !template.isEmpty { args += ["--template", template] }
        if withTranscript { args.append("--with-transcript") }
        return args
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
        guard !fileID.isEmpty, !vaultSendingIDs.contains(fileID) else { return false }
        vaultSendingIDs.insert(fileID)
        defer { vaultSendingIDs.remove(fileID) }

        let args = VaultSendArguments.build(
            fileID: fileID, dest: dest, content: content, via: via,
            model: model, template: template, withTranscript: withTranscript
        )

        // Headless AI reformatting legitimately takes minutes; direct writes
        // are near-instant and claude-window returns as soon as Terminal opens.
        let timeout: TimeInterval = via == "claude" ? 960 : 60
        let output = await runPlaudOutput(args: args, timeout: timeout, showError: false)
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)

        guard let data = trimmed.data(using: .utf8), !data.isEmpty,
              var outcome = try? JSONDecoder().decode(VaultSendOutcome.self, from: data)
        else {
            lastCommandError = trimmed.isEmpty
                ? "볼트 전송에 실패했습니다 — Plaud CLI에서 응답이 없습니다."
                : "볼트 전송 응답을 해석하지 못했습니다: \(trimmed.prefix(200))"
            return false
        }

        switch outcome.status {
        case "ok":
            lastCommandError = nil
            if via != "claude-window" {
                outcome.fileID = fileID
                lastVaultSend = outcome
            }
            if selectedID == fileID {
                noteMetadata = Database.shared.noteMetadata(for: fileID)
            }
            return true
        case "no_content":
            lastCommandError =
                "보낼 결과물이 없습니다 — Work Sidebar에서 Summary/Integrated를 먼저 Generate 해주세요."
            return false
        case "no_vault":
            lastCommandError =
                "볼트가 설정되지 않았습니다 — Settings 또는 `plaud config-vault`로 경로를 지정해 주세요."
            return false
        default:
            let detail = outcome.detail?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let suffix = detail.isEmpty ? "" : " — \(detail)"
            lastCommandError = "볼트 전송에 실패했습니다 (\(outcome.status))\(suffix)"
            return false
        }
    }

    /// Run a template over a recording and file the note in a vault via
    /// `plaud vault-compose` — the only way the app writes to the wiki.
    /// Generation runs a model (minutes, quota) unless a cached summary for
    /// the template exists. Progress shows through `vaultSendingIDs`, the
    /// result in the Actions strip, failures in the global alert.
    @discardableResult
    func vaultCompose(_ fileID: String, template: String, vault: String,
                      purpose: String) async -> Bool {
        guard !fileID.isEmpty, !vaultSendingIDs.contains(fileID) else { return false }
        vaultSendingIDs.insert(fileID)
        defer { vaultSendingIDs.remove(fileID) }

        let args = VaultComposeArguments.build(
            fileID: fileID, template: template, vault: vault, purpose: purpose
        )
        // With --json every status exits 0; a non-zero exit is a crash or a
        // timeout, and showError puts its stderr tail in the alert.
        let output = await runPlaudOutput(args: args, timeout: 960, showError: true)
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

    /// Open a sent note in Obsidian via its obsidian:// URL.
    func openVaultNote(_ outcome: VaultSendOutcome) {
        if let raw = outcome.obsidianURL, let url = URL(string: raw) {
            NSWorkspace.shared.open(url)
        } else if let path = outcome.path {
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
        }
    }
}
