import Foundation

/// One row of `plaud vaults --json`: a configured vault that exists on disk.
/// `key` (`main` | `wiki`) is what `--to` takes — never pass `path`, which the
/// CLI would route as a main-vault send.
struct VaultChoice: Decodable, Identifiable, Hashable {
    let key: String
    let label: String
    let name: String
    let path: String
    var id: String { key }
}

/// The vault picker's source. An empty `choices` with no `error` means no
/// vault is configured; `error` means the CLI itself failed, timed out or
/// answered with something unreadable — a different message for the user.
struct VaultChoices: Equatable {
    var choices: [VaultChoice] = []
    var error: String?

    var keys: [String] { choices.map(\.key) }

    static func decode(_ data: Data) -> [VaultChoice]? {
        try? JSONDecoder().decode([VaultChoice].self, from: data)
    }

    /// Pure reading of one `vaults --json` run.
    static func interpret(exitCode: Int32, stdout: String, stderr: String) -> VaultChoices {
        guard exitCode == 0 else {
            let reason = stderr.split(whereSeparator: \.isNewline)
                .last { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
                .map { String($0.prefix(200)) } ?? "exit \(exitCode)"
            return VaultChoices(error: "볼트 목록을 불러오지 못했습니다 — \(reason)")
        }
        let trimmed = stdout.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let choices = decode(Data(trimmed.utf8)) else {
            return VaultChoices(error: "볼트 목록 응답을 해석하지 못했습니다: \(trimmed.prefix(200))")
        }
        return VaultChoices(choices: choices)
    }

    /// Runs `plaud vaults --json`. Blocks: call off the main thread.
    static func load() -> VaultChoices {
        let args = ["vaults", "--json"]
        do {
            let process = try PlaudCommand.makeProcess(args: args)
            let result = PlaudProcessRunner.run(process, timeout: PlaudCommand.timeout(for: args))
            return interpret(exitCode: result.exitCode, stdout: result.stdout, stderr: result.stderr)
        } catch {
            return VaultChoices(error: error.localizedDescription)
        }
    }
}

/// One submitted compose, kept whole so a duplicate prompt can re-run it.
/// (`VaultComposeRequest` only opens the sheet.)
struct VaultComposeJob: Equatable {
    let fileID: String
    let template: String
    let vault: String
    let purpose: String
}

/// argv and form rules for `plaud vault-compose`.
enum VaultComposeArguments {
    /// `vault-compose --template T --to V [--collection-purpose P]
    /// --on-duplicate D --json -- FILE_ID`. The purpose is sent only for the
    /// wiki, trimmed; `--` keeps a file id that starts with `-` from being
    /// read as an option.
    static func build(_ job: VaultComposeJob, onDuplicate: String) -> [String] {
        var args = ["vault-compose", "--template", job.template, "--to", job.vault]
        if requiresPurpose(vault: job.vault) {
            args += ["--collection-purpose", job.purpose.trimmingCharacters(in: .whitespacesAndNewlines)]
        }
        return args + ["--on-duplicate", onDuplicate, "--json", "--", job.fileID]
    }

    /// A wiki raw source must say why it was collected.
    static func requiresPurpose(vault: String) -> Bool { vault == "wiki" }

    static func canSubmit(template: String, vault: String, purpose: String) -> Bool {
        guard !template.isEmpty, !vault.isEmpty else { return false }
        return !requiresPurpose(vault: vault)
            || !purpose.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// The vault the sheet opens on. A preset that is not available gives ""
    /// (submit disabled): a wiki entry point must never quietly become a
    /// main-vault compose. Without a preset: main, else the first vault.
    static func initialVault(preset: String?, available: [String]) -> String {
        if let preset { return available.contains(preset) ? preset : "" }
        if available.contains("main") { return "main" }
        return available.first ?? ""
    }
}

/// User-facing text for `vault-compose --json` statuses.
enum VaultComposeOutcome {
    /// nil for `ok`, and for `duplicate`, which the duplicate dialog answers
    /// instead; otherwise a Korean message, with the CLI's detail appended
    /// when it has one.
    static func errorMessage(status: String, detail: String?) -> String? {
        let base: String
        switch status {
        case "ok", "duplicate":
            return nil
        case "invalid_option":
            base = "위키 Raw Source는 덮어쓸 수 없습니다 — 새 버전으로 만들어 주세요."
        case "needs_purpose":
            base = "위키 Raw Source에는 수집 목적이 필요합니다 — 왜 수집했고 어떻게 재사용할지 적어 주세요."
        case "no_vault":
            base = "볼트를 찾지 못했습니다 — `uv run plaud config-vault` 또는 "
                + "`uv run plaud config-wiki-vault`로 경로를 지정해 주세요."
        case "no_transcript":
            base = "전사본이 없습니다 — 녹음을 동기화하거나 전사를 먼저 실행해 주세요."
        case "model_failed":
            base = "모델 실행에 실패했습니다 — 로그인·쿼터·시간 초과를 확인해 주세요."
        case "schema_error":
            base = "작성된 노트가 볼트 스키마 검사를 통과하지 못해 저장하지 않았습니다."
        case "no_content":
            base = "작성 결과가 비어 있어 저장하지 않았습니다."
        case "write_failed":
            base = "볼트에 노트 파일을 쓰지 못했습니다."
        case "bad_template":
            base = "이 템플릿으로는 작성할 수 없습니다 — note-* 템플릿을 골라 주세요."
        default:
            base = "볼트 작성에 실패했습니다 (\(status))"
        }
        let trimmed = detail?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return trimmed.isEmpty ? base : "\(base) — \(trimmed)"
    }
}

/// Opens the compose sheet for one recording. `presetVault` is the vault
/// key the entry point asked for (`wiki` from the Wiki menu), or nil.
struct VaultComposeRequest: Identifiable, Equatable {
    let fileID: String
    let presetVault: String?
    var id: String { fileID + "::" + (presetVault ?? "") }
}
