import SwiftUI

private struct OfficialConnection: Decodable {
    var installed: Bool
    var version: String?
    var authState: String?
    var detail: String?
    enum CodingKeys: String, CodingKey {
        case installed, version, detail
        case authState = "auth_state"
    }
}

private struct OfficialReadResult: Decodable {
    struct Note: Decodable {
        var title: String?
        var content: String?
    }
    var status: String
    var detail: String?
    var text: String?
    var notes: [Note]?
    var complete: Bool?
}

/// Official OAuth is an independent read surface; its output is a preview,
/// never a partial overwrite of the app's richer API-backed recording cache.
struct PlaudOfficialAccessView: View {
    @ObservedObject var store: FileStore
    @State private var connection: OfficialConnection?
    @State private var busy = false
    @State private var notice: String?
    @State private var preview: String?
    @State private var previewTitle = ""

    var body: some View {
        DisclosureGroup("공식 CLI · MCP 읽기 연결") {
            VStack(alignment: .leading, spacing: 10) {
                Text("폴더·제목 변경과 동기화는 Plaud Web API를 사용합니다. 공식 CLI는 요약·전사를 읽는 별도 연결이며, MCP는 AI 도구에서 검색·조회할 때 적합합니다.")
                    .font(AppUI.metaFont).foregroundStyle(.secondary)
                HStack {
                    Label(connectionLabel, systemImage: "terminal")
                    Spacer()
                    if busy { ProgressView().controlSize(.small) }
                    Button("연결 확인") { Task { await checkConnection(live: true) } }
                        .disabled(busy)
                }
                if let fileID = store.selectedID {
                    Text(store.selectedFile?.filename ?? "선택한 녹음")
                        .font(AppUI.metaFont).lineLimit(2)
                    HStack {
                        Button("공식 요약 읽기") { read(fileID, kind: "summary") }
                        Button("공식 전사 읽기") { read(fileID, kind: "transcript") }
                    }
                    .disabled(busy || connection?.installed != true)
                }
                if let notice {
                    Text(notice).font(AppUI.metaFont).foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                Text("공식 OAuth는 웹 세션과 별개입니다. CLI 로그인은 plaud login, MCP 로그인은 사용 중인 AI 도구에서 진행합니다.")
                    .font(AppUI.metaFont).foregroundStyle(.secondary)
                Link("공식 CLI 문서", destination: URL(string: "https://docs.plaud.ai/plaud-mcp-cli/cli")!)
            }
            .padding(.top, 10)
        }
        .task { await checkConnection(live: false) }
        .sheet(isPresented: Binding(get: { preview != nil }, set: { if !$0 { preview = nil } })) {
            VStack(alignment: .leading, spacing: 12) {
                Text(previewTitle).font(.headline).lineLimit(2)
                Text("공식 CLI 조회 결과 · 원본과 로컬 캐시는 유지됩니다.")
                    .font(.caption).foregroundStyle(.secondary)
                ScrollView {
                    Text(preview ?? "").frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
                HStack {
                    Spacer()
                    Button("닫기") { preview = nil }.keyboardShortcut(.cancelAction)
                }
            }.padding(20).frame(width: 720, height: 560)
        }
    }

    private var connectionLabel: String {
        guard let connection else { return "공식 CLI 확인 중…" }
        guard connection.installed else { return "공식 CLI가 설치되지 않았습니다" }
        let version = connection.version.map { " v\($0)" } ?? ""
        switch connection.authState {
        case "valid", "authenticated": return "공식 CLI\(version) · 연결 확인됨"
        case "unconfigured", "not_authenticated", "login_required", "needs_login": return "공식 CLI\(version) · 별도 로그인 필요"
        default: return "공식 CLI\(version) · 설치됨"
        }
    }

    @MainActor
    private func checkConnection(live: Bool) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        var args = ["official-status", "--json"]
        if live { args.append("--live") }
        let output = await store.runPlaudOutput(args: args, timeout: 25, showError: false)
        connection = output.data(using: .utf8).flatMap { try? JSONDecoder().decode(OfficialConnection.self, from: $0) }
        notice = connection?.detail ?? (connection == nil ? "공식 CLI 상태를 확인하지 못했습니다." : nil)
    }

    private func read(_ fileID: String, kind: String) {
        guard !busy else { return }
        let title = store.selectedFile?.filename ?? "선택한 녹음"
        busy = true
        notice = nil
        Task { @MainActor in
            defer { busy = false }
            let output = await store.runPlaudOutput(
                args: ["official-read", fileID, "--kind", kind, "--json"], timeout: 40, showError: false
            )
            guard let data = output.data(using: .utf8),
                  let result = try? JSONDecoder().decode(OfficialReadResult.self, from: data) else {
                notice = "공식 CLI의 읽기 응답을 확인하지 못했습니다."
                return
            }
            guard result.status == "ok" else {
                notice = result.detail ?? "공식 CLI 연결을 확인해 주세요."
                return
            }
            let text = kind == "summary" ? (result.notes ?? []).map {
                [$0.title, $0.content].compactMap { $0 }.joined(separator: "\n\n")
            }.joined(separator: "\n\n———\n\n") : (result.text ?? "")
            guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                notice = result.detail ?? "이 녹음에는 아직 조회할 본문이 없습니다."
                return
            }
            previewTitle = title
            preview = text
            if result.complete == false { notice = result.detail ?? "조회 가능한 본문만 표시했습니다." }
        }
    }
}
