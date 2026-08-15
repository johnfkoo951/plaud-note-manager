import Foundation

private struct WebAuthResult: Decodable {
    var status: String
    var detail: String?
    var autoRefreshArmed: Bool?

    enum CodingKeys: String, CodingKey {
        case status
        case detail
        case autoRefreshArmed = "auto_refresh_armed"
    }
}

/// Minimal shape of `plaud ws-refresh --json`. Tokens never appear in it —
/// the command writes them straight to macOS Keychain.
private struct WSRefreshOutcome: Decodable {
    var status: String
    var detail: String?
}

extension FileStore {
    /// Mint a fresh 24h Plaud token headlessly via `plaud ws-refresh` — no
    /// browser, no cURL. Available once a workspace refresh token has been
    /// bootstrapped (embedded web login does this automatically).
    @discardableResult
    func refreshWorkspaceToken() async -> Bool {
        guard !refreshingWorkspaceToken else { return false }
        refreshingWorkspaceToken = true
        defer { refreshingWorkspaceToken = false }

        let output = await runPlaudOutput(
            args: ["ws-refresh", "--json"],
            timeout: 30,
            showError: false
        )
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let data = trimmed.data(using: .utf8), !data.isEmpty,
              let outcome = try? JSONDecoder().decode(WSRefreshOutcome.self, from: data)
        else {
            lastCommandError = trimmed.isEmpty
                ? "토큰 갱신에 실패했습니다 — Plaud CLI에서 응답이 없습니다."
                : "토큰 갱신 응답을 해석하지 못했습니다: \(trimmed.prefix(200))"
            return false
        }

        let detail = outcome.detail?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        switch outcome.status {
        case "ok":
            lastCommandError = nil
            await refreshAuth(live: true)
            await sync(showError: false)
            return true
        case "fresh":
            // Nothing changed server-side; just repaint the indicator.
            lastCommandError = nil
            await refreshAuth()
            return true
        case "not_bootstrapped":
            lastCommandError =
                "자동 갱신이 아직 설정되지 않았습니다 — Plaud Web Login으로 한 번 로그인해 주세요."
            return false
        case "rejected":
            await refreshAuth()  // disarmed server-side; repaint auto-refresh row
            lastCommandError =
                "Plaud가 리프레시 토큰을 거부했습니다. Plaud Web Login으로 다시 로그인해 주세요."
            return false
        case "unreachable":
            lastCommandError = "Plaud에 연결하지 못했습니다 — 네트워크를 확인해 주세요. (\(detail))"
            return false
        default:
            let suffix = detail.isEmpty ? "" : " — \(detail)"
            lastCommandError = "토큰 갱신에 실패했습니다 (\(outcome.status))\(suffix)"
            return false
        }
    }

    /// Tier-1 recovery via `plaud auth-recover`: re-harvest workspaceList from
    /// a live web.plaud.ai session in the cmux browser — no password entry.
    /// Used when even the headless refresh chain is broken (rejected /
    /// not_bootstrapped). Falls back to the Web Login flow on failure.
    @discardableResult
    func recoverAuthViaBrowser() async -> Bool {
        guard !refreshingWorkspaceToken else { return false }
        refreshingWorkspaceToken = true
        defer { refreshingWorkspaceToken = false }

        let output = await runPlaudOutput(
            args: ["auth-recover", "--json"],
            timeout: 90,  // opens a browser surface + polls the SPA
            showError: false
        )
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let data = trimmed.data(using: .utf8), !data.isEmpty,
              let outcome = try? JSONDecoder().decode(WSRefreshOutcome.self, from: data)
        else {
            lastCommandError = trimmed.isEmpty
                ? "브라우저 세션 복구에 실패했습니다 — Plaud CLI에서 응답이 없습니다."
                : "브라우저 세션 복구 응답을 해석하지 못했습니다: \(trimmed.prefix(200))"
            return false
        }

        let detail = outcome.detail?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        switch outcome.status {
        case "ok":
            lastCommandError = nil
            await refreshAuth(live: true)
            await sync(showError: false)
            return true
        case "no_session":
            lastCommandError =
                "cmux 브라우저에 web.plaud.ai 로그인 세션이 없습니다 — 열린 창에서 한 번 로그인한 뒤 다시 시도하거나, 아래 Web Login을 사용해 주세요."
            return false
        case "driver_unavailable":
            lastCommandError =
                "cmux 브라우저를 사용할 수 없습니다 — Web Login으로 로그인해 주세요. (\(detail))"
            return false
        default:
            let suffix = detail.isEmpty ? "" : " — \(detail)"
            lastCommandError = "브라우저 세션 복구에 실패했습니다 (\(outcome.status))\(suffix)"
            return false
        }
    }

    @discardableResult
    func refreshAuthFromWebLogin(_ capture: PlaudWebAuthCapture) async -> Bool {
        guard !refreshingAuth else { return false }
        refreshingAuth = true
        defer { refreshingAuth = false }

        let payload: String
        do {
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .useDefaultKeys
            let data = try encoder.encode(capture)
            payload = String(data: data, encoding: .utf8) ?? "{}"
        } catch {
            lastCommandError = "Plaud Web Login 인증 정보를 저장용 JSON으로 만들지 못했습니다."
            return false
        }

        let output = await runPlaudOutput(
            args: ["web-auth", "--json", "--stdin"],
            stdin: payload,
            timeout: 40,
            showError: false
        )
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let data = trimmed.data(using: .utf8), !data.isEmpty,
              let result = try? JSONDecoder().decode(WebAuthResult.self, from: data)
        else {
            lastCommandError = trimmed.isEmpty
                ? "Plaud Web Login 인증 저장에 실패했습니다 — CLI 응답이 없습니다."
                : "Plaud Web Login 인증 응답을 해석하지 못했습니다: \(trimmed.prefix(200))"
            return false
        }

        let detail = result.detail?.trimmingCharacters(in: .whitespacesAndNewlines)
        let suffix = (detail?.isEmpty ?? true) ? "" : " — \(detail ?? "")"
        switch result.status {
        case "ok":
            guard result.autoRefreshArmed == true else {
                await refreshAuth(live: false)
                lastCommandError =
                    "Plaud 연결은 됐지만 자동 갱신 토큰을 아직 확인하지 못했습니다. 창을 닫지 말고 잠시 후 다시 시도해 주세요.\(suffix)"
                return false
            }
            lastCommandError = nil
            await refreshAuth(live: true)
            await sync(showError: false)
            return true
        case "live_check_unavailable":
            // 자격 증명은 Keychain에 저장됐지만, 네트워크 문제로 라이브 검증을 못 한 상태.
            // 파괴적이지 않은 경고이므로 재로그인을 요구하지 않는다. 저장은 됐으므로
            // 오프라인 상태 갱신과 라이브러리 reload로 UI에 반영한다.
            await refreshAuth(live: false)
            await sync(showError: false)
            lastCommandError =
                "저장했지만 네트워크 문제로 검증하지 못했습니다 — 연결을 확인해주세요."
            return false
        case "missing_required":
            lastCommandError = "Plaud Web Login에서 authorization 또는 x-device-id 헤더를 아직 찾지 못했습니다."
            return false
        case "live_auth_failed":
            // Plaud가 실제로 거부한 경우 (Keychain은 변경되지 않음).
            lastCommandError = "Plaud가 캡처된 세션을 거부했습니다. Web Session을 지우고 다시 로그인해주세요."
            return false
        case "write_failed":
            lastCommandError = "macOS Keychain에 인증 정보를 저장하지 못했습니다\(suffix)."
            return false
        default:
            lastCommandError = "Plaud Web Login 인증에 실패했습니다 (\(result.status))\(suffix)"
            return false
        }
    }
}
