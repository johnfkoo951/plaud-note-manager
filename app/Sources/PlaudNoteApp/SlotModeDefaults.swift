import Foundation

/// How a slot's template kind drives the inspector: which mode a slot opens
/// in, what Generate All does with it, and which mode may run it.
///
/// A `nil` kind means the slot's template is not on disk (renamed or
/// deleted); every rule then falls back to the behaviour from before kinds
/// existed.
enum SlotModeDefaults {
    enum BatchAction: Equatable {
        case summarize, integrate, skip
    }

    /// Default mode when the user has not picked one for this slot.
    static func opensIntegrated(kind: TemplateMeta.Kind?, hasIntegratedOutput: Bool) -> Bool {
        switch kind {
        case .integrated: return true
        case .summary, .internalUse: return false
        case nil: return hasIntegratedOutput
        }
    }

    /// What Generate All runs for a slot. Internal prompts (classify,
    /// metadata, …) are never run from a slot.
    static func batchAction(kind: TemplateMeta.Kind?) -> BatchAction {
        switch kind {
        case .integrated: return .integrate
        case .summary: return .summarize
        case .internalUse: return .skip
        case nil: return .integrate
        }
    }

    /// Whether the per-slot Generate may run in the slot's current mode.
    static func canGenerate(integratedMode: Bool, kind: TemplateMeta.Kind?) -> Bool {
        switch kind {
        case .integrated: return integratedMode
        case .summary: return !integratedMode
        case .internalUse: return false
        case nil: return true
        }
    }

    /// Tooltip for a disabled Generate, naming the mode that can run it.
    static func generateBlockedHelp(kind: TemplateMeta.Kind?) -> String {
        switch kind {
        case .integrated: return "integrated 템플릿 — Integrated 모드에서 생성하세요"
        case .summary: return "summary 템플릿 — Summary 모드에서 생성하세요"
        case .internalUse: return "내부용(internal) 템플릿은 슬롯에서 생성하지 않습니다"
        case nil: return ""
        }
    }
}
