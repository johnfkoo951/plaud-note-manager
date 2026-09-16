import Foundation

/// Access expiry and renewal availability are separate: an access-only cURL
/// can still work while the app quietly tries to attach its saved session.
enum AuthRecoveryPolicy {
    static func shouldAttempt(_ status: AuthStatus) -> Bool {
        guard status.configured else { return false }
        if ["expired", "expiring", "rejected"].contains(status.state) { return true }
        return status.state == "valid"
            && ["not_bootstrapped", "expired", "rejected"].contains(status.autoRefresh ?? "")
    }

    static func needsInteractiveLogin(_ status: AuthStatus?) -> Bool {
        guard let status else { return false }
        return ["expired", "rejected", "unconfigured"].contains(status.state)
    }

    static let retryDelay: TimeInterval = 300

    static func shouldPromptForMissingSession(
        _ status: AuthStatus, missingGeneration: Int?, promptedGeneration: Int?
    ) -> Bool {
        let generation = status.issuedAt ?? status.expiresAt ?? 0
        return needsInteractiveLogin(status) && missingGeneration == generation
            && promptedGeneration != generation
    }
}
