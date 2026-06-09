import AppKit
import Combine
import Foundation

private enum PlaudCommandError: LocalizedError {
    case missingUV([String])

    var errorDescription: String? {
        switch self {
        case .missingUV(let candidates):
            return "uv executable not found. Checked: \(candidates.joined(separator: ", "))"
        }
    }
}

/// Snapshot of Plaud credential health, mirroring the JSON emitted by
/// `plaud auth --json`. All fields are optional/tolerant so a partial or
/// malformed payload still decodes into *something* the UI can render.
struct AuthStatus: Codable, Equatable {
    var configured: Bool
    var state: String  // valid | expiring | expired | unconfigured | unknown
    var workspaceID: String?
    var memberID: String?
    var role: String?
    var issuedAt: Int?
    var expiresAt: Int?
    var secondsRemaining: Int?
    var remainingHuman: String?
    var liveOK: Bool?
    var detail: String

    enum CodingKeys: String, CodingKey {
        case configured
        case state
        case workspaceID = "workspace_id"
        case memberID = "member_id"
        case role
        case issuedAt = "issued_at"
        case expiresAt = "expires_at"
        case secondsRemaining = "seconds_remaining"
        case remainingHuman = "remaining_human"
        case liveOK = "live_ok"
        case detail
    }

    /// Fallback used when the CLI output can't be decoded — keeps the indicator
    /// alive (gray "unknown") instead of crashing or vanishing.
    static func unknown(detail: String) -> AuthStatus {
        AuthStatus(
            configured: false,
            state: "unknown",
            workspaceID: nil,
            memberID: nil,
            role: nil,
            issuedAt: nil,
            expiresAt: nil,
            secondsRemaining: nil,
            remainingHuman: nil,
            liveOK: nil,
            detail: detail
        )
    }
}

private enum PlaudCommand {
    static let projectRoot = NSString(string: "~/DEV/plaud-note-manager")
        .expandingTildeInPath

    static let uvCandidates = [
        "\(NSHomeDirectory())/.local/bin/uv",
        "/opt/homebrew/bin/uv",
        "/usr/local/bin/uv",
    ]

    static let cliPath = [
        "\(NSHomeDirectory())/.local/bin",
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ].joined(separator: ":")

    static func makeProcess(args: [String]) throws -> Process {
        guard let uvPath = uvExecutablePath() else {
            throw PlaudCommandError.missingUV(uvCandidates)
        }

        let process = Process()
        process.currentDirectoryURL = URL(fileURLWithPath: projectRoot)
        process.environment = ProcessInfo.processInfo.environment.merging(
            ["PATH": cliPath, "PYTHONUNBUFFERED": "1"]
        ) { _, new in new }
        process.executableURL = URL(fileURLWithPath: uvPath)
        process.arguments = ["run", "plaud"] + args
        return process
    }

    private static func uvExecutablePath() -> String? {
        uvCandidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }
}

@MainActor
final class FileStore: ObservableObject {
    /// The full library (trash included) loaded once from SQLite. The visible
    /// `files` list is derived from this synchronously, so folder-clicks and
    /// search keystrokes never hit the DB or take an async hop.
    private var masterFiles: [Database.MasterFile] = []
    @Published var files: [PlaudFileVM] = []
    @Published var folders: [FolderVM] = []
    @Published var categoryCounts: (all: Int, unfiled: Int, trash: Int) = (0, 0, 0)
    @Published var cacheStatus: (total: Int, cached: Int) = (0, 0)
    @Published var sidebar: SidebarItem = .allFiles
    @Published var search: String = ""
    @Published var selectedID: String?
    @Published var content: FileContentVM?
    @Published var noteMetadata: NoteMetadataVM?
    @Published var isSyncing: Bool = false
    @Published var lastSyncedAt: Date?
    @Published var deepSyncRunning: Bool = false
    @Published var lastCommandError: String?
    /// Latest Plaud credential health, refreshed at launch, after each sync,
    /// and on app activation. Drives the toolbar auth-status indicator.
    @Published var auth: AuthStatus?
    /// True while `refreshAuthCredentials()` parses the copied Plaud cURL and
    /// rewrites `.env`. Drives auth UI spinners and disabled
    /// state.
    @Published var refreshingAuth = false

    private var cloudSyncTimer: Timer?
    private var cloudSyncInterval: TimeInterval = 30
    private var cancellables: Set<AnyCancellable> = []
    private var lifecycleObservers: [NSObjectProtocol] = []
    private var pendingDetailFetch: Set<String> = []
    /// Generation counter to coalesce bursts of reload requests: only the
    /// latest off-main fetch is allowed to publish its results.
    private var reloadGeneration: UInt64 = 0

    var selectedFile: PlaudFileVM? { files.first { $0.id == selectedID } }

    private struct CommandResult {
        let exitCode: Int32
        let stdout: String
        let stderr: String

        var ok: Bool { exitCode == 0 }

        var failureMessage: String {
            let rawBody = [stderr, stdout]
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .first { !$0.isEmpty } ?? "No output"
            let body = Self.conciseBody(from: rawBody)
            return "Exit \(exitCode): \(body)"
        }

        private static func conciseBody(from raw: String) -> String {
            let cleaned = stripANSI(raw)
            let lines = cleaned
                .components(separatedBy: .newlines)
                .map { trimTraceLine($0) }
                .filter { !$0.isEmpty }
            if cleaned.localizedCaseInsensitiveContains("workspace token expired") {
                return "Plaud auth expired. Use auth > Authenticate with Plaud, then retry."
            }

            if cleaned.localizedCaseInsensitiveContains("traceback")
                || cleaned.localizedCaseInsensitiveContains("most recent call last") {
                if let explicit = lines.reversed().first(where: isUsefulErrorLine) {
                    return explicit
                }
                if cleaned.contains("httpx") || cleaned.contains("httpcore") {
                    return "Plaud network request failed. Check your internet connection or Plaud session, then retry."
                }
                return lines.suffix(4).joined(separator: "\n")
            }

            let body = lines.joined(separator: "\n")
            if body.count <= 1200 { return body }
            return String(body.prefix(1200)) + "\n…"
        }

        private static func isUsefulErrorLine(_ line: String) -> Bool {
            let lower = line.lowercased()
            if lower.contains("plaud network error")
                || lower.contains("plaud content network error")
                || lower.contains("plaud audio network error")
                || lower.contains("plaud http")
                || lower.contains("plaud api error")
                || lower.contains("missing plaud credentials") {
                return true
            }
            return line.range(
                of: #"([A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Timeout)):"#,
                options: .regularExpression
            ) != nil
        }

        private static func trimTraceLine(_ line: String) -> String {
            line
                .replacingOccurrences(of: "│", with: " ")
                .replacingOccurrences(of: "┃", with: " ")
                .trimmingCharacters(in: .whitespacesAndNewlines)
        }

        private static func stripANSI(_ text: String) -> String {
            let pattern = #"\u{001B}\[[0-?]*[ -/]*[@-~]"#
            guard let regex = try? NSRegularExpression(pattern: pattern) else {
                return text
            }
            let range = NSRange(text.startIndex..<text.endIndex, in: text)
            return regex.stringByReplacingMatches(
                in: text,
                options: [],
                range: range,
                withTemplate: ""
            )
        }
    }

    init() {
        DatabaseWatcher.shared.start()
        reload()

        NotificationCenter.default.publisher(for: .plaudDBChanged)
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.reload() }
            .store(in: &cancellables)

        NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)
            .sink { [weak self] _ in
                Task { @MainActor in await self?.sync(showError: false) }
            }
            .store(in: &cancellables)

        // Folder selection and search filter the already-loaded master list
        // synchronously — no DB hop, no async frame. A `@Published` sink fires
        // during `willSet`, so `self.sidebar`/`self.search` still hold the OLD
        // value here; we must use the value the publisher emits.
        $sidebar
            .sink { [weak self] newSidebar in
                self?.applyFilter(sidebar: newSidebar, search: self?.search ?? "")
            }
            .store(in: &cancellables)

        $search
            .removeDuplicates()
            .sink { [weak self] newSearch in
                self?.applyFilter(sidebar: self?.sidebar ?? .allFiles, search: newSearch)
            }
            .store(in: &cancellables)

        $selectedID
            .removeDuplicates()
            .sink { [weak self] id in self?.loadContent(for: id) }
            .store(in: &cancellables)

        registerLifecycleObservers()
        // Show the auth indicator immediately at launch with a cheap offline
        // check, before the (slower, network-bound) initial sync finishes.
        Task { await refreshAuth() }
        Task { await sync(showError: false) }
        startCloudPolling(every: 30)
    }

    deinit {
        cloudSyncTimer?.invalidate()
        let center = NotificationCenter.default
        for observer in lifecycleObservers {
            center.removeObserver(observer)
        }
    }

    /// Suspend the cloud-sync poll while the app is in the background and
    /// resume it on activation, so the 30s network poll doesn't run forever.
    private func registerLifecycleObservers() {
        guard lifecycleObservers.isEmpty else { return }
        let center = NotificationCenter.default
        lifecycleObservers.append(
            center.addObserver(forName: NSApplication.didResignActiveNotification,
                               object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.stopCloudPolling() }
            }
        )
        lifecycleObservers.append(
            center.addObserver(forName: NSApplication.didBecomeActiveNotification,
                               object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.startCloudPolling(every: self?.cloudSyncInterval ?? 30) }
            }
        )
    }

    func startCloudPolling(every seconds: TimeInterval) {
        cloudSyncInterval = seconds
        cloudSyncTimer?.invalidate()
        let t = Timer(timeInterval: seconds, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.sync(showError: false) }
        }
        RunLoop.main.add(t, forMode: .common)
        cloudSyncTimer = t
    }

    func stopCloudPolling() {
        cloudSyncTimer?.invalidate()
        cloudSyncTimer = nil
    }

    /// Refresh all list/count state from SQLite.
    ///
    /// The blocking SQLite queries run on a detached background task; only the
    /// resulting value-type snapshots are published back on the main actor, so
    /// SwiftUI updates stay safe and the main thread never stalls on a full
    /// query. Bursts of reload requests are coalesced via `reloadGeneration` —
    /// only the most recent fetch is allowed to publish, collapsing a flurry of
    /// `.plaudDBChanged` ticks into one visible update.
    ///
    /// Ordering note: callers that mutate SQLite first (e.g. `moveFile` ->
    /// `writeFileFolders`) complete their synchronous write before invoking
    /// `reload()`, so the off-main read launched here always observes that
    /// committed write — the optimistic update is never raced away.
    func reload() {
        reloadGeneration &+= 1
        let generation = reloadGeneration
        let selectedID = self.selectedID

        Task.detached(priority: .userInitiated) { [weak self] in
            let master = Database.shared.masterFiles()
            let folders = Database.shared.folders()
            let categoryCounts = Database.shared.categoryCounts()
            let cacheStatus = Database.shared.contentCacheStatus()
            let metadata = selectedID.map { Database.shared.noteMetadata(for: $0) }
            let transcript = selectedID.flatMap { Database.shared.cmdsTranscript(for: $0) }

            guard let strongSelf = self else { return }
            await MainActor.run {
                guard generation == strongSelf.reloadGeneration else { return }
                strongSelf.masterFiles = master
                strongSelf.folders = folders
                strongSelf.categoryCounts = categoryCounts
                strongSelf.cacheStatus = cacheStatus
                // Re-derive the visible list against the freshly-loaded master
                // set using the *current* sidebar/search.
                strongSelf.applyFilter(sidebar: strongSelf.sidebar,
                                       search: strongSelf.search)
                if let id = selectedID, id == strongSelf.selectedID {
                    strongSelf.loadContent(for: id)
                    strongSelf.noteMetadata = metadata
                    strongSelf.cmdsTranscript = transcript
                }
            }
        }
    }

    /// Derive the visible `files` list from the in-memory `masterFiles` master
    /// set. Reproduces the four sidebar cases exactly as `Database.files(for:)`
    /// did in SQL, plus a case-insensitive filename `contains(search)`.
    /// Runs synchronously on the main actor — no DB hop, same render pass.
    func applyFilter(sidebar: SidebarItem, search: String) {
        let trimmed = search.trimmingCharacters(in: .whitespacesAndNewlines)
        let needle = trimmed.lowercased()
        let result = masterFiles.compactMap { item -> PlaudFileVM? in
            let matchesSidebar: Bool
            switch sidebar {
            case .allFiles:
                matchesSidebar = !item.file.isTrash
            case .unfiled:
                matchesSidebar = !item.file.isTrash && item.folderIDs.isEmpty
            case .trash:
                matchesSidebar = item.file.isTrash
            case .folder(let id):
                matchesSidebar = !item.file.isTrash && item.folderIDs.contains(id)
            }
            guard matchesSidebar else { return nil }
            if !needle.isEmpty {
                guard (item.file.filename ?? "").lowercased().contains(needle)
                else { return nil }
            }
            return item.file
        }
        files = result
    }

    func sync(showError: Bool = true) async {
        guard !isSyncing else { return }
        isSyncing = true
        defer { isSyncing = false }
        await runPlaud(args: ["sync"], showError: showError)
        lastSyncedAt = Date()
        reload()
        // Cheap offline auth check after every sync — also covers launch and
        // app-activation, both of which route through `sync()`.
        await refreshAuth()
    }

    /// Refresh the cached Plaud auth status via `plaud auth --json`.
    ///
    /// Offline (`live == false`) is instant — it only decodes the local JWT, no
    /// network — so it's cheap enough to fire at launch, after each sync, and on
    /// app activation. Pass `live: true` for the "Verify now" button to also ping
    /// the API. Decode failures fall back to a `state:"unknown"` value rather
    /// than crashing or clearing the indicator.
    func refreshAuth(live: Bool = false) async {
        var args = ["auth", "--json"]
        if live { args.append("--live") }
        let output = await runPlaudOutput(args: args, showError: false)
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let data = trimmed.data(using: .utf8), !data.isEmpty else {
            auth = AuthStatus.unknown(detail: "No output from plaud auth.")
            return
        }
        if let decoded = try? JSONDecoder().decode(AuthStatus.self, from: data) {
            auth = decoded
        } else {
            auth = AuthStatus.unknown(
                detail: "Could not read auth status from Plaud CLI."
            )
        }
    }

    /// Minimal shape of `plaud refresh-auth --json`'s output. The credential
    /// itself is *never* in this payload — the command writes it straight to
    /// `.env` after parsing the copied Plaud cURL.
    private struct RefreshAuthResult: Decodable {
        let status: String
        let detail: String?
    }

    /// Refresh Plaud credentials from a Plaud API cURL. When `curlText` is nil,
    /// the CLI falls back to the macOS pasteboard for the lightweight toolbar
    /// flow. The full in-app auth sheet passes pasted text directly via stdin,
    /// so the user never needs to run terminal commands.
    ///
    /// On success we re-run a *live* `refreshAuth()` so the toolbar indicator
    /// reflects the new token, and a `sync()` so the library picks up anything
    /// that was previously blocked on auth. The token/cookie are never surfaced
    /// — we only read the `status`/`detail` fields the command prints.
    @discardableResult
    func refreshAuthCredentials(curlText: String? = nil) async -> Bool {
        guard !refreshingAuth else { return false }
        refreshingAuth = true
        defer { refreshingAuth = false }

        let cleanCurl = curlText?.trimmingCharacters(in: .whitespacesAndNewlines)
        var args = ["refresh-auth", "--json"]
        let stdinText: String?
        if let cleanCurl, !cleanCurl.isEmpty {
            args.append("--stdin")
            stdinText = cleanCurl
        } else {
            stdinText = nil
        }

        let output = await runPlaudOutput(
            args: args,
            stdin: stdinText,
            timeout: 20,
            showError: false
        )
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)

        guard let data = trimmed.data(using: .utf8), !data.isEmpty,
              let result = try? JSONDecoder().decode(RefreshAuthResult.self, from: data)
        else {
            lastCommandError = trimmed.isEmpty
                ? "인증 갱신에 실패했습니다 — Plaud CLI에서 응답이 없습니다."
                : "인증 갱신 응답을 해석하지 못했습니다: \(trimmed.prefix(200))"
            return false
        }

        let detail = result.detail?.trimmingCharacters(in: .whitespacesAndNewlines)
        let detailOrNil = (detail?.isEmpty ?? true) ? nil : detail

        switch result.status {
        case "ok":
            lastCommandError = nil
            // Update the indicator with a live check, then reload the library.
            await refreshAuth(live: true)
            await sync(showError: false)
            return true
        case "clipboard_empty":
            lastCommandError = detailOrNil
                ?? "Plaud API 요청을 cURL로 복사한 뒤 다시 눌러주세요."
            return false
        case "pbpaste_missing":
            lastCommandError = detailOrNil
                ?? "macOS 클립보드를 읽을 수 없습니다. 인증 창에 Plaud cURL을 직접 붙여넣어 주세요."
            return false
        default:
            // invalid_curl | anything else.
            let suffix = detailOrNil.map { " — \($0)" } ?? ""
            lastCommandError = "인증 갱신에 실패했습니다 (\(result.status))\(suffix)"
            return false
        }
    }

    @Published var classifyRunning: Bool = false
    @Published var classifyResult: String?

    /// Auto-classify recordings into the Plaud folder taxonomy and move them
    /// into the matched folders (`classify --apply`). The App's unique
    /// capability — the web client cannot do this. Reports a short summary of
    /// how many files were moved, then refreshes the library.
    func classify() async {
        guard !classifyRunning else { return }
        classifyRunning = true
        defer { classifyRunning = false }
        let output = await runPlaudOutput(args: ["classify", "--apply"])
        await sync(showError: false)
        // CLI prints one line per file; lines with `->` were actually moved.
        let lines = output
            .components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        let moved = lines.filter { $0.contains("->") }.count
        classifyResult = moved > 0
            ? "Auto-classified \(moved) recording\(moved == 1 ? "" : "s") into folders."
            : "No recordings matched a folder with enough confidence."
    }

    /// Background backfill of transcript/summary cache for every file.
    /// Long-running (15+ min for 1k files); UI stays responsive.
    func deepSync() async {
        guard !deepSyncRunning else { return }
        deepSyncRunning = true
        defer { deepSyncRunning = false }
        await runPlaud(args: ["sync-content"])
        reload()
    }

    /// Export (download) the audio file. Like `exportContent`, we let the user
    /// choose the destination so they get clear feedback on where the file
    /// landed. The CLI names the file itself inside the chosen directory, so we
    /// pick a directory, capture the saved path it prints, and reveal it in
    /// Finder.
    func download(_ fileID: String) async {
        let panel = NSOpenPanel()
        panel.title = "Choose where to save the audio"
        panel.prompt = "Save Here"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let dir = panel.url else { return }

        let output = await runPlaudOutput(args: ["download", fileID, dir.path])
        reload()

        // CLI prints `ok <path>`. Reveal the saved file (or the directory) so
        // the user knows exactly where it landed.
        let savedPath = output
            .components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .compactMap { line -> String? in
                guard let range = line.range(of: "ok ") else { return nil }
                return String(line[range.upperBound...])
                    .trimmingCharacters(in: .whitespacesAndNewlines)
            }
            .last
        let revealURL = savedPath.flatMap { $0.isEmpty ? nil : URL(fileURLWithPath: $0) } ?? dir
        NSWorkspace.shared.activateFileViewerSelecting([revealURL])
    }

    func sendToObsidian(_ fileID: String) async {
        await runPlaud(args: ["obsidian", fileID])
    }

    func addTag(_ rawTag: String, to fileID: String) async {
        let tag = rawTag.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !tag.isEmpty else { return }
        await runPlaud(args: ["tag-add", fileID, tag])
        noteMetadata = Database.shared.noteMetadata(for: fileID)
    }

    func removeTag(_ tag: String, from fileID: String) async {
        await runPlaud(args: ["tag-remove", fileID, tag])
        noteMetadata = Database.shared.noteMetadata(for: fileID)
    }

    @Published var metadataGeneratingIDs: Set<String> = []
    @Published var meetingNoteGeneratingIDs: Set<String> = []

    func generateMetadata(_ fileID: String, model: String = "claude",
                          modelID: String = "") async {
        metadataGeneratingIDs.insert(fileID)
        defer { metadataGeneratingIDs.remove(fileID) }
        var args = ["metadata-generate", fileID, "--model", model]
        if !modelID.isEmpty {
            args += ["--model-id", modelID]
        }
        await runPlaud(args: args)
        noteMetadata = Database.shared.noteMetadata(for: fileID)
        reload()
    }

    func writeMeetingNote(_ fileID: String, model: String = "claude",
                          modelID: String = "") async {
        meetingNoteGeneratingIDs.insert(fileID)
        defer { meetingNoteGeneratingIDs.remove(fileID) }
        var args = ["meeting-note", fileID, "--model", model]
        if !modelID.isEmpty {
            args += ["--model-id", modelID]
        }
        await runPlaud(args: args)
        noteMetadata = Database.shared.noteMetadata(for: fileID)
    }

    func setUsageStatus(_ fileID: String, status: String) async {
        await runPlaud(args: ["usage-status", fileID, status])
        noteMetadata = Database.shared.noteMetadata(for: fileID)
    }

    /// Force a re-fetch of file detail (transcript + summary + folder ids)
    /// from Plaud, bypassing the local cache. Used by the title refresh button.
    func refetchDetail(_ fileID: String) async {
        await runPlaud(args: ["detail", fileID])
        if selectedID == fileID {
            content = Database.shared.content(for: fileID)
        }
    }

    func deleteFolder(_ folderID: String) async {
        await runPlaud(args: ["folder-delete", folderID])
        await sync(showError: false)
    }

    func createFolder(name: String) async {
        await runPlaud(args: ["folder-create", name])
        await sync(showError: false)
    }

    func updateFolder(_ folderID: String, name: String?, color: String?, icon: String?) async {
        var args = ["folder-rename", folderID]
        if let n = name { args += ["--name", n] }
        if let c = color { args += ["--color", c] }
        if let i = icon { args += ["--icon", i] }
        await runPlaud(args: args)
        await sync(showError: false)
    }

    /// Move a file into one or more folders (empty list = unfiled).
    /// Optimistic: writes locally + reloads UI immediately, then fires the
    /// API call in the background so the user never waits on the network.
    func moveFile(_ fileID: String, toFolders folderIDs: [String]) {
        if let error = Database.shared.writeFileFolders(fileID, folderIDs: folderIDs) {
            // Local write failed — surface it and reconcile the UI back to the
            // last persisted state instead of showing a phantom optimistic move.
            lastCommandError = "Could not update folder locally: \(error.localizedDescription)"
            reload()
            return
        }
        reload()
        Task.detached(priority: .userInitiated) { [weak self] in
            var args = ["move", fileID]
            args.append(contentsOf: folderIDs)
            await self?.runPlaud(args: args)
        }
    }

    /// Folder ids the file currently belongs to, from the in-memory master
    /// list. Used by the multi-select "Move to folder" menu to seed checkmarks.
    func folderIDs(for fileID: String) -> Set<String> {
        masterFiles.first { $0.id == fileID }?.folderIDs ?? []
    }

    /// Toggle one folder's membership for a file and persist the full set.
    /// A file may live in multiple folders (Plaud's `filetag_id_list` is an
    /// array), so we add/remove the single id from the current set and write
    /// the whole array back via `moveFile`.
    func toggleFolder(_ fileID: String, folderID: String) {
        var ids = folderIDs(for: fileID)
        if ids.contains(folderID) {
            ids.remove(folderID)
        } else {
            ids.insert(folderID)
        }
        moveFile(fileID, toFolders: Array(ids))
    }

    func renameFile(_ fileID: String, to name: String) async {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        await runPlaud(args: ["rename", fileID, trimmed])
        await sync(showError: false)
    }

    func plaudWebURL(_ fileID: String) -> URL? {
        URL(string: "https://web.plaud.ai/file/\(fileID)")
    }

    func openInPlaudWeb(_ fileID: String) {
        guard let url = plaudWebURL(fileID) else { return }
        NSWorkspace.shared.open(url)
    }

    func copyPlaudWebURL(_ fileID: String) {
        guard let url = plaudWebURL(fileID) else { return }
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(url.absoluteString, forType: .string)
    }

    @Published var cmdsTranscript: String?
    @Published var transcribingIDs: Set<String> = []
    @Published var audioURL: URL?

    func reloadCmdsTranscript() {
        cmdsTranscript = selectedID.flatMap { Database.shared.cmdsTranscript(for: $0) }
    }

    func transcribeWithElevenLabs(_ fileID: String, numSpeakers: Int = 0) async {
        transcribingIDs.insert(fileID)
        defer { transcribingIDs.remove(fileID) }
        var args = ["cmds-transcribe", fileID]
        if numSpeakers > 0 {
            args += ["--num-speakers", String(numSpeakers)]
        }
        await runPlaud(args: args)
        reloadCmdsTranscript()
    }

    func relabelCmdsSpeakers(_ fileID: String, mapping: [String: String],
                             startSec: Double? = nil, endSec: Double? = nil) async {
        var args = ["cmds-relabel", fileID]
        for (k, v) in mapping where !v.isEmpty {
            args.append("\(k)=\(v)")
        }
        if let s = startSec { args += ["--start", String(s)] }
        if let e = endSec { args += ["--end", String(e)] }
        await runPlaud(args: args)
        reloadCmdsTranscript()
    }

    func addSpeaker(name: String, isSelf: Bool) async {
        var args = ["speaker-add", name]
        if isSelf { args.append("--self") }
        await runPlaud(args: args)
    }

    func deleteSpeaker(_ id: Int64) async {
        await runPlaud(args: ["speaker-delete", String(id)])
    }

    @Published var summarizingKeys: Set<String> = []

    func summarize(fileID: String, model: String, modelID: String = "", template: String) async {
        let outputModel = modelID.isEmpty ? model : modelID
        let key = "\(fileID)::\(model)::\(outputModel)::\(template)"
        summarizingKeys.insert(key)
        defer { summarizingKeys.remove(key) }
        var args = [
            "cmds-summarize", fileID,
            "--model", model,
            "--template", template,
        ]
        if !modelID.isEmpty {
            args += ["--model-id", modelID]
        }
        await runPlaud(args: args)
    }

    /// Run the integrated pipeline (final transcript + summary) for one slot.
    func integrate(fileID: String, model: String, modelID: String = "", template: String) async {
        let outputModel = modelID.isEmpty ? model : modelID
        let key = "\(fileID)::\(model)::\(outputModel)::\(template)"
        summarizingKeys.insert(key)
        defer { summarizingKeys.remove(key) }
        var args = [
            "cmds-integrate", fileID,
            "--model", model,
            "--template", template,
        ]
        if !modelID.isEmpty {
            args += ["--model-id", modelID]
        }
        await runPlaud(args: args)
    }

    func addSlot(name: String, model: String, modelID: String = "", template: String) async {
        var args = ["slot-add", name, model, template]
        if !modelID.isEmpty {
            args += ["--model-id", modelID]
        }
        await runPlaud(args: args)
    }

    func deleteSlot(_ name: String) async {
        await runPlaud(args: ["slot-delete", name])
    }

    func openProjectFolder(_ subpath: String) {
        let path = NSString(string: "~/DEV/plaud-note-manager/\(subpath)")
            .expandingTildeInPath
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }

    func openPath(_ path: String) {
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }

    func setBackend(_ model: String, _ backend: String) async {
        await runPlaud(args: ["config-backend", model, backend])
    }

    func setModelId(_ model: String, _ id: String) async {
        await runPlaud(args: ["config-model", model, id])
    }

    func setPath(_ kind: String, _ path: String) async {
        await runPlaud(args: ["config-path", kind, path])
    }

    /// Resolve a fresh signed audio URL via the CLI and stash it for AVPlayer.
    /// FileStore is already `@MainActor`, so we assign directly (no extra hop),
    /// and validate the CLI output before trusting it as a streamable URL.
    func loadAudioURL(_ fileID: String) async {
        let output = await runPlaudOutput(args: ["audio-url", fileID])
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              let url = URL(string: trimmed),
              let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https"
        else {
            lastCommandError = "Could not load audio URL from Plaud."
            return
        }
        audioURL = url
    }

    /// Shell out to `uv run plaud …` and return stdout.
    ///
    /// `timeout` (seconds) is an optional watchdog: when set, the process is
    /// terminated if it overruns. When `nil` (the default), behavior is
    /// unchanged — it waits indefinitely via `waitUntilExit()`, matching every
    /// existing caller.
    func runPlaudOutput(
        args: [String],
        stdin: String? = nil,
        timeout: TimeInterval? = nil,
        showError: Bool = true
    ) async -> String {
        let result = await Task.detached(priority: .userInitiated) { () -> CommandResult in
            let stdout = Pipe()
            let stderr = Pipe()
            let input = stdin.map { _ in Pipe() }
            do {
                let process = try PlaudCommand.makeProcess(args: args)
                process.standardOutput = stdout
                process.standardError = stderr
                if let input {
                    process.standardInput = input
                }
                try process.run()
                if let stdin,
                   let input,
                   let data = stdin.data(using: .utf8) {
                    input.fileHandleForWriting.write(data)
                    input.fileHandleForWriting.closeFile()
                }

                var timedOut = false
                if let timeout {
                    // Arm a watchdog that kills the process if it overruns, then
                    // wait. Reading the pipes *after* the process exits (or is
                    // killed) avoids a deadlock on a full pipe buffer for these
                    // small-output commands.
                    let watchdog = DispatchWorkItem {
                        if process.isRunning {
                            timedOut = true
                            process.terminate()
                        }
                    }
                    DispatchQueue.global().asyncAfter(
                        deadline: .now() + timeout, execute: watchdog
                    )
                    process.waitUntilExit()
                    watchdog.cancel()
                } else {
                    process.waitUntilExit()
                }

                let outData = stdout.fileHandleForReading.readDataToEndOfFile()
                let errData = stderr.fileHandleForReading.readDataToEndOfFile()
                if timedOut {
                    return CommandResult(
                        exitCode: -1,
                        stdout: String(data: outData, encoding: .utf8) ?? "",
                        stderr: "plaud command timed out after \(Int(timeout ?? 0))s"
                    )
                }
                return CommandResult(
                    exitCode: process.terminationStatus,
                    stdout: String(data: outData, encoding: .utf8) ?? "",
                    stderr: String(data: errData, encoding: .utf8) ?? ""
                )
            } catch {
                return CommandResult(
                    exitCode: -1,
                    stdout: "",
                    stderr: "plaud CLI failed: \(error.localizedDescription)"
                )
            }
        }.value
        if !result.ok && showError {
            lastCommandError = result.failureMessage
        }
        return result.stdout
    }

    func copyToClipboard(_ text: String) {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
    }

    func exportContent(_ fileID: String, kind: String) async {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "\(fileID)-\(kind).md"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        await runPlaud(args: ["export", fileID, kind, "--out", url.path])
    }

    private func loadContent(for id: String?) {
        guard let id else {
            content = nil
            noteMetadata = nil
            return
        }
        noteMetadata = Database.shared.noteMetadata(for: id)
        content = Database.shared.content(for: id)
        if content == nil && !pendingDetailFetch.contains(id) {
            pendingDetailFetch.insert(id)
            Task {
                await runPlaud(args: ["detail", id], showError: false)
                self.pendingDetailFetch.remove(id)
                self.content = Database.shared.content(for: id)
            }
        }
    }

    private func runPlaud(args: [String], showError: Bool = true) async {
        let result = await Task.detached(priority: .userInitiated) { () -> CommandResult in
            let stdout = Pipe()
            let stderr = Pipe()
            do {
                let process = try PlaudCommand.makeProcess(args: args)
                process.standardOutput = stdout
                process.standardError = stderr
                try process.run()
                process.waitUntilExit()
                let outData = stdout.fileHandleForReading.readDataToEndOfFile()
                let errData = stderr.fileHandleForReading.readDataToEndOfFile()
                return CommandResult(
                    exitCode: process.terminationStatus,
                    stdout: String(data: outData, encoding: .utf8) ?? "",
                    stderr: String(data: errData, encoding: .utf8) ?? ""
                )
            } catch {
                return CommandResult(
                    exitCode: -1,
                    stdout: "",
                    stderr: "plaud CLI failed: \(error.localizedDescription)"
                )
            }
        }.value
        if !result.ok && showError {
            lastCommandError = result.failureMessage
        }
    }
}
