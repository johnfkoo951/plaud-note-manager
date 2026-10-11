import SwiftUI

/// Pick a `note-*` template and a vault, then `plaud vault-compose`. The wiki
/// needs a collection purpose. Sending dismisses at once: generation can take
/// minutes, so progress and the result appear in the Send to Vault spinner and
/// the Actions strip, failures in the global alert.
struct VaultComposeSheet: View {
    @ObservedObject var store: FileStore
    let request: VaultComposeRequest
    let dismiss: () -> Void

    @State private var loaded = false
    @State private var templates: [TemplateMeta] = []
    @State private var vaults = VaultChoices()
    @State private var template = ""
    @State private var vault = ""
    @State private var purpose = ""

    private var isWikiEntry: Bool { request.presetVault == "wiki" }
    private var isSending: Bool { store.vaultSendingIDs.contains(request.fileID) }
    private var canSend: Bool {
        loaded && !isSending
            && VaultComposeArguments.canSubmit(template: template, vault: vault, purpose: purpose)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(isWikiEntry ? "Wiki Raw Source로 넣기" : "템플릿으로 작성해서 보내기")
                .font(.headline)
            if loaded {
                templateRow
                vaultRow
                if VaultComposeArguments.requiresPurpose(vault: vault) {
                    purposeRow
                }
                caption("선택한 템플릿을 모델로 실행해 노트를 작성합니다 — 쿼터를 쓰고 몇 분 걸릴 수 "
                        + "있습니다. 이 녹음에 같은 템플릿의 요약이 이미 있으면 그것을 재사용합니다.")
            } else {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("템플릿과 볼트를 불러오는 중…")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("작성해서 보내기") { send() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!canSend)
            }
        }
        .padding(20)
        .frame(width: 520)
        .task {
            let loadedValues = await Task.detached(priority: .userInitiated) {
                (Database.shared.listTemplateInfos(), VaultChoices.load())
            }.value
            guard !Task.isCancelled else { return }
            let choices = TemplateMeta.composeChoices(loadedValues.0)
            templates = choices
            template = choices.isEmpty ? "" : TemplateMeta.initialSelection(choices: choices)
            vaults = loadedValues.1
            vault = VaultComposeArguments.initialVault(preset: request.presetVault,
                                                       available: vaults.keys)
            loaded = true
        }
    }

    @ViewBuilder
    private var templateRow: some View {
        HStack(alignment: .firstTextBaseline) {
            Text("Template").frame(width: 80, alignment: .leading)
            if templates.isEmpty {
                notice("작성에 쓸 note-* 템플릿이 없습니다 — templates/ 폴더를 확인해 주세요.")
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    Picker("", selection: $template) {
                        ForEach(templates) { meta in
                            Text(meta.name).tag(meta.name)
                        }
                    }
                    .labelsHidden()
                    if let meta = templates.first(where: { $0.name == template }),
                       !meta.description.isEmpty {
                        caption(meta.description)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var vaultRow: some View {
        HStack(alignment: .firstTextBaseline) {
            Text("Vault").frame(width: 80, alignment: .leading)
            VStack(alignment: .leading, spacing: 4) {
                if let error = vaults.error {
                    notice(error)
                } else if vaults.choices.isEmpty {
                    notice("볼트가 설정되지 않았습니다 — uv run plaud config-vault")
                } else {
                    Picker("", selection: $vault) {
                        ForEach(vaults.choices) { choice in
                            Text("\(choice.label) · \(choice.name)").tag(choice.key)
                        }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    if vault.isEmpty, isWikiEntry {
                        notice("위키 볼트를 찾지 못했습니다 — uv run plaud config-wiki-vault")
                    } else if vault == "main" {
                        caption("메인 볼트의 00. Inbox에 저장됩니다.")
                    } else if vault == "wiki" {
                        caption("전사 원문을 담은 불변 Raw Source로 10. Raw Sources 아래에 저장됩니다.")
                    }
                }
            }
        }
    }

    private var purposeRow: some View {
        HStack(alignment: .firstTextBaseline) {
            Text("수집 목적").frame(width: 80, alignment: .leading)
            VStack(alignment: .leading, spacing: 4) {
                IMETextField("왜 수집했고 어떻게 재사용할지", text: $purpose)
                    .frame(height: 22)
                caption("위키 Raw Source의 collectionPurpose로 기록됩니다. 비어 있으면 보낼 수 없습니다.")
            }
        }
    }

    private func send() {
        IMETextField.finishEditing()
        guard canSend else { return }
        let job = VaultComposeJob(fileID: request.fileID, template: template,
                                  vault: vault, purpose: purpose)
        Task { await store.vaultCompose(job) }
        dismiss()
    }

    private func caption(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func notice(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(.orange)
            .fixedSize(horizontal: false, vertical: true)
    }
}
