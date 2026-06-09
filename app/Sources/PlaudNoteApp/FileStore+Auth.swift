import Foundation

private struct WebAuthResult: Decodable {
    var status: String
    var detail: String?
}

extension FileStore {
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
            lastCommandError = nil
            await refreshAuth(live: true)
            await sync(showError: false)
            return true
        case "missing_required":
            lastCommandError = "Plaud Web Login에서 필요한 인증 헤더나 쿠키를 아직 찾지 못했습니다."
            return false
        case "live_auth_failed":
            lastCommandError = "Plaud가 캡처된 세션을 거부했습니다. Web Session을 지우고 다시 로그인해주세요."
            return false
        default:
            lastCommandError = "Plaud Web Login 인증에 실패했습니다 (\(result.status))\(suffix)"
            return false
        }
    }
}
