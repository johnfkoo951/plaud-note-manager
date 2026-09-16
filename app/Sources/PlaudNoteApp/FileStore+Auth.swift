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
    /// Silently repair an expired/rejected session before bothering the user.
    ///
    /// The normal Keychain refresh owns the rotating token. If Plaud revokes
    /// that chain, a transient hidden WKWebView uses its persistent HttpOnly
    /// account session to mint a *new* workspace pair. Chrome profile scraping
    /// remains an explicit CLI fallback; it is not part of the app's automatic
    /// path because it can race a second browser-owned rotation.
    func selfHealAuthIfNeeded(_ status: AuthStatus) async {
        // Passive status polls cannot tear down an in-flight recovery or race
        // a cURL/Web Login write. Only terminal recovery events change phase.
        guard !interactiveLoginActive, authRecoveryPhase == .idle, !refreshingWorkspaceToken, !refreshingAuth else { return }
        guard AuthRecoveryPolicy.shouldAttempt(status) else {
            if status.state == "valid", status.autoRefreshReady {
                selfHealCredentialIssuedAt = nil
                authRecoveryRetryAfter = nil
            }
            return
        }
        if let retryAfter = authRecoveryRetryAfter, Date() < retryAfter { return }

        // One silent attempt per access-token generation. Passive 30-second
        // sync ticks must not create an endless WebView/login loop.
        let generation = status.issuedAt ?? status.expiresAt ?? 0
        if AuthRecoveryPolicy.shouldPromptForMissingSession(
            status, missingGeneration: authMissingSessionGeneration,
            promptedGeneration: authPromptedGeneration
        ) {
            requireInteractiveAuth("저장된 앱 로그인 세션이 없습니다. 자동 갱신 연결을 완료해 주세요.")
            return
        }
        guard selfHealCredentialIssuedAt != generation else { return }
        selfHealCredentialIssuedAt = generation

        if status.autoRefreshReady {
            guard let outcome = await runWorkspaceRefreshCommand() else {
                // Local CLI trouble is not evidence that login expired. Let a
                // later passive tick retry instead of opening auth UI.
                deferAuthRecovery("인증 갱신에 연결하지 못했습니다. 잠시 후 자동으로 재시도합니다.")
                return
            }
            switch outcome.status {
            case "ok", "fresh":
                lastCommandError = nil
                await refreshAuth(live: outcome.status == "ok")
                if outcome.status == "ok" { Task { await self.sync(showError: false) } }
                return
            case "unreachable":
                // Network outage: keep the account session untouched and retry
                // later. Re-login cannot fix an unreachable endpoint.
                deferAuthRecovery("인증 갱신에 연결하지 못했습니다. 잠시 후 자동으로 재시도합니다.")
                return
            case "rejected", "not_bootstrapped":
                break
            default:
                deferAuthRecovery("인증 갱신에 연결하지 못했습니다. 잠시 후 자동으로 재시도합니다.")
                return
            }
        }

        beginSilentWebRecovery()
    }

    /// Manual toolbar action runs the same ladder as passive self-heal. A dead
    /// refresh token no longer surfaces an intermediate failure alert; it
    /// immediately advances to the persistent account session.
    @discardableResult
    func refreshWorkspaceToken() async -> Bool {
        guard !interactiveLoginActive else {
            lastCommandError = "로그인 창에서 연결을 완료하거나 창을 닫은 뒤 다시 갱신해 주세요."
            return false
        }
        guard let outcome = await runWorkspaceRefreshCommand() else { return false }

        let detail = outcome.detail?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        switch outcome.status {
        case "ok":
            lastCommandError = nil
            await refreshAuth(live: true)
            Task { await self.sync(showError: false) }
            return true
        case "fresh":
            // Nothing changed server-side; just repaint the indicator.
            lastCommandError = nil
            await refreshAuth()
            return true
        case "not_bootstrapped":
            lastCommandError = nil
            beginSilentWebRecovery(force: true)
            return false
        case "rejected":
            lastCommandError = nil
            beginSilentWebRecovery(force: true)
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

    private func runWorkspaceRefreshCommand() async -> WSRefreshOutcome? {
        guard !refreshingWorkspaceToken else { return nil }
        refreshingWorkspaceToken = true
        defer { refreshingWorkspaceToken = false }

        let output = await runPlaudOutput(
            args: ["ws-refresh", "--json"], timeout: 30, showError: false
        )
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let data = trimmed.data(using: .utf8), !data.isEmpty,
              let outcome = try? JSONDecoder().decode(WSRefreshOutcome.self, from: data)
        else {
            lastCommandError = trimmed.isEmpty
                ? "토큰 갱신에 실패했습니다 — Plaud CLI에서 응답이 없습니다."
                : "토큰 갱신 응답을 해석하지 못했습니다: \(trimmed.prefix(200))"
            return nil
        }
        return outcome
    }

    func beginSilentWebRecovery(force: Bool = false) {
        guard !interactiveLoginActive else { return }
        guard authRecoveryPhase != .verifying else { return }
        guard force || authRecoveryPhase == .idle else { return }
        if let issuedAt = auth?.issuedAt {
            selfHealCredentialIssuedAt = issuedAt
        }
        authRecoveryRetryAfter = nil
        lastCommandError = nil
        authRecoveryStatus = "Recovering from the saved Plaud account session…"
        authRecoveryRequestID &+= 1
        authRecoveryPhase = .webSession
    }

    /// Claim exactly one capture and unmount WebKit before Keychain is written.
    func claimSilentWebCapture() -> Bool {
        guard authRecoveryPhase == .webSession else { return false }
        authRecoveryPhase = .verifying
        return true
    }

    /// Only a confirmed missing account session plus unusable access should
    /// interrupt the user. Access-only cURL users can continue using the app.
    func requireInteractiveAuth(_ detail: String? = nil) {
        let generation = auth?.issuedAt ?? auth?.expiresAt ?? 0
        authMissingSessionGeneration = generation
        let needsLogin = AuthRecoveryPolicy.needsInteractiveLogin(auth)
        authRecoveryStatus = needsLogin
            ? detail : "현재 접속은 유효합니다. 인증 창에서 자동 갱신을 한 번 연결해 주세요."
        lastCommandError = nil
        if needsLogin, authPromptedGeneration != generation {
            authPromptedGeneration = generation
            authRecoveryPhase = .needsInteractive
        } else {
            authRecoveryPhase = .idle
        }
    }

    /// Timeout, network and local-store errors never prove session expiry.
    func deferAuthRecovery(_ detail: String) {
        authRecoveryStatus = detail
        lastCommandError = nil
        selfHealCredentialIssuedAt = nil
        authRecoveryRetryAfter = Date().addingTimeInterval(AuthRecoveryPolicy.retryDelay)
        authRecoveryPhase = .idle
    }

    func markInteractiveAuthPresented() {
        if authRecoveryPhase == .needsInteractive {
            authRecoveryPhase = .idle
        }
    }

    /// Tier-1 recovery via `plaud auth-recover`: re-harvest workspaceList from
    /// a live web.plaud.ai session in the cmux browser — no password entry.
    /// Used when even the headless refresh chain is broken (rejected /
    /// not_bootstrapped). Falls back to the Web Login flow on failure.
    @discardableResult
    func recoverAuthViaBrowser() async -> Bool {
        guard !interactiveLoginActive else {
            lastCommandError = "열린 로그인 창을 먼저 닫은 뒤 Chrome 연결을 시도해 주세요."
            return false
        }
        guard !refreshingWorkspaceToken else { return false }
        refreshingWorkspaceToken = true
        defer { refreshingWorkspaceToken = false }

        let output = await runPlaudOutput(
            args: ["auth-recover", "--json", "--driver", "chrome-disk"],
            timeout: 35,  // reads the existing local Chrome session; never changes browser settings
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
            await refreshAuth()
            Task { await self.sync(showError: false) }
            return auth?.autoRefreshReady == true
        case "no_session":
            lastCommandError =
                "브라우저에 web.plaud.ai 로그인 세션이 없습니다 — Chrome(또는 cmux)에서 로그인한 뒤 다시 시도하거나, 아래 Web Login을 사용해 주세요."
            return false
        case "chrome_js_disabled":
            lastCommandError =
                "브라우저 세션에 접근하지 못했습니다. 아래 자동 갱신 연결에서 앱 전용 로그인을 사용할 수 있습니다."
            return false
        case "driver_unavailable":
            lastCommandError =
                "제어 가능한 브라우저(Chrome/cmux)가 없습니다 — Web Login으로 로그인해 주세요. (\(detail))"
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
        authStatusRequestID &+= 1
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
            await refreshAuth()
            Task { await self.sync(showError: false) }
            return true
        case "live_check_unavailable":
            await refreshAuth()
            lastCommandError = "네트워크 문제로 새 연결을 검증하지 못했습니다. 기존 인증은 유지됩니다. 잠시 후 다시 시도해 주세요."
            return false
        case "missing_required":
            lastCommandError = "Plaud Web Login에서 authorization 또는 x-device-id 헤더를 아직 찾지 못했습니다."
            return false
        case "live_auth_failed":
            // Plaud가 실제로 거부한 경우 (Keychain은 변경되지 않음).
            lastCommandError = "Plaud가 캡처된 세션을 거부했습니다. 기존 인증은 유지됩니다. 자동 갱신 연결을 다시 시도해 주세요."
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
