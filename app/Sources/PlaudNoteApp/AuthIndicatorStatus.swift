import Foundation

/// Access validity and the ability to renew it are independent. A usable
/// 24-hour token must not look permanently connected when renewal is missing.
struct AuthIndicatorStatus {
    let accessState: String?
    let autoRefresh: String?
    let liveState: String?
    let liveOK: Bool?

    var usable: Bool { accessState == "valid" || accessState == "expiring" }
    var renewalReady: Bool { autoRefresh == "ready" || autoRefresh == "expiring" }
    var networkUnavailable: Bool { liveState == "unreachable" }
    var hasWarning: Bool { usable && (!renewalReady || networkUnavailable) }

    var accessLabel: String {
        switch accessState {
        case "valid": return "사용 가능"
        case "expiring": return "곧 만료"
        case "expired": return "만료"
        case "rejected": return "인증 거부"
        case "unconfigured": return "로그인 필요"
        default: return "확인 필요"
        }
    }

    var renewalWarning: String? {
        guard usable, !renewalReady else { return nil }
        switch autoRefresh {
        case "disabled": return "자동 갱신 꺼짐"
        case "store_unavailable": return "자동 갱신 확인 필요"
        case nil: return "자동 갱신 미확인"
        default: return "자동 갱신 미연결"
        }
    }

    var compactSuffix: String? {
        guard usable else { return nil }
        if networkUnavailable { return "연결 확인" }
        guard !renewalReady else { return nil }
        return autoRefresh == "disabled" ? "갱신 꺼짐" : "갱신 미연결"
    }

    var liveLabel: String? {
        switch liveState {
        case "ok": return "연결 확인됨"
        case "rejected": return "인증 거부됨"
        case "unreachable": return "네트워크 연결 불가"
        default:
            if liveOK == true { return "연결 확인됨" }
            // Older payloads conflated network failure with rejection. Only
            // the explicit new state can establish that credentials failed.
            return liveOK == false ? "연결 확인 실패 (원인 미확인)" : nil
        }
    }
}
