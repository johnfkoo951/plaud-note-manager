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
        case "live_check_unavailable":
            // 자격 증명은 .env에 저장됐지만, 네트워크 문제로 라이브 검증을 못 한 상태.
            // 파괴적이지 않은 경고이므로 재로그인을 요구하지 않는다. 저장은 됐으므로
            // 오프라인 상태 갱신과 라이브러리 reload로 UI에 반영한다.
            await refreshAuth(live: false)
            await sync(showError: false)
            lastCommandError =
                "저장했지만 네트워크 문제로 검증하지 못했습니다 — 연결을 확인해주세요."
            return false
        case "missing_required":
            lastCommandError = "Plaud Web Login에서 필요한 인증 헤더나 쿠키를 아직 찾지 못했습니다."
            return false
        case "live_auth_failed":
            // Plaud가 실제로 거부한 경우 (.env는 변경되지 않음).
            lastCommandError = "Plaud가 캡처된 세션을 거부했습니다. Web Session을 지우고 다시 로그인해주세요."
            return false
        case "write_failed":
            lastCommandError = ".env 파일을 쓰지 못했습니다\(suffix) — 디스크 권한을 확인해주세요."
            return false
        default:
            lastCommandError = "Plaud Web Login 인증에 실패했습니다 (\(result.status))\(suffix)"
            return false
        }
    }
}
