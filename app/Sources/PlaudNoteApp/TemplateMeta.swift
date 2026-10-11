import SwiftUI

/// One prompt template under `templates/`, read straight from its file.
///
/// The front matter contract (written and validated by the Python side):
///
///     ---
///     name: note-meeting
///     description: <one line>
///     kind: summary | integrated | internal   # missing → summary
///     deprecated: true | false                # optional
///     replaced_by: <template name>            # optional
///     ---
///
///     <body>
///
/// `name` (and `id`) is the file's basename, which is what slots reference;
/// the `name:` key inside the file is not used for identity.
struct TemplateMeta: Hashable, Identifiable {
    enum Kind: String {
        case summary
        case integrated
        case internalUse = "internal"
    }

    let name: String
    let description: String
    let kind: Kind
    let deprecated: Bool
    let replacedBy: String
    /// Everything after the closing `---` line, unchanged. Shipped files keep
    /// one blank line there, so this usually starts with `\n`.
    let body: String

    var id: String { name }

    /// `cmds-meeting (deprecated → note-meeting)` for a deprecated template,
    /// otherwise the bare name.
    var pickerLabel: String {
        guard deprecated else { return name }
        return replacedBy.isEmpty
            ? "\(name) (deprecated)"
            : "\(name) (deprecated → \(replacedBy))"
    }

    /// Templates a slot may use: everything except `internal` ones, by name.
    static func slotChoices(_ all: [TemplateMeta]) -> [TemplateMeta] {
        all.filter { $0.kind != .internalUse }.sorted { $0.name < $1.name }
    }

    /// Viewer order: the purpose-specific `note-*` templates first, then the
    /// rest, each group by name.
    static func viewerOrder(_ all: [TemplateMeta]) -> [TemplateMeta] {
        let sorted = all.sorted { $0.name < $1.name }
        return sorted.filter { $0.name.hasPrefix("note-") }
            + sorted.filter { !$0.name.hasPrefix("note-") }
    }

    // MARK: - Parsing

    /// Pure parse of one template file. The front matter is the text after a
    /// leading `---\n` up to the first `\n---\n` (the same bounds as the
    /// Python loader's regex); without it the whole file is the body.
    /// Works on UTF-8 bytes so the body is returned byte for byte.
    static func parse(name: String, raw: String) -> TemplateMeta {
        let bytes = Array(raw.utf8)
        let open = Array("---\n".utf8)
        let close = Array("\n---\n".utf8)

        guard bytes.starts(with: open),
              let closeAt = firstIndex(of: close, in: bytes, from: open.count)
        else {
            return TemplateMeta(name: name, description: "", kind: .summary,
                                deprecated: false, replacedBy: "", body: raw)
        }

        let fields = frontMatterFields(Array(bytes[open.count..<closeAt]))
        let body = String(decoding: bytes[(closeAt + close.count)...], as: UTF8.self)
        return TemplateMeta(
            name: name,
            description: fields["description"] ?? "",
            kind: kind(from: fields["kind"]),
            deprecated: ["true", "yes", "1"].contains((fields["deprecated"] ?? "").lowercased()),
            replacedBy: fields["replaced_by"] ?? "",
            body: body
        )
    }

    private static func firstIndex(of needle: [UInt8], in haystack: [UInt8],
                                   from start: Int) -> Int? {
        guard haystack.count >= needle.count, start <= haystack.count - needle.count
        else { return nil }
        for i in start...(haystack.count - needle.count)
        where haystack[i..<(i + needle.count)].elementsEqual(needle) {
            return i
        }
        return nil
    }

    /// `key: value` lines starting at column 0; the first occurrence of a key
    /// wins. Indented lines (nested YAML) and `#` comment lines are ignored.
    private static func frontMatterFields(_ bytes: [UInt8]) -> [String: String] {
        var fields: [String: String] = [:]
        for lineBytes in bytes.split(separator: UInt8(ascii: "\n"),
                                     omittingEmptySubsequences: true) {
            var line = String(decoding: lineBytes, as: UTF8.self)
            if line.hasSuffix("\r") { line.removeLast() }
            guard let first = line.first, first != " ", first != "\t", first != "#",
                  let colon = line.firstIndex(of: ":")
            else { continue }
            let key = line[..<colon].trimmingCharacters(in: .whitespaces)
            guard !key.isEmpty, fields[key] == nil else { continue }
            fields[key] = cleanValue(String(line[line.index(after: colon)...]))
        }
        return fields
    }

    /// Strips one pair of matching surrounding quotes, or else a trailing
    /// ` #comment`. A `#` inside quotes, or not preceded by whitespace
    /// (`C#`), is kept.
    private static func cleanValue(_ raw: String) -> String {
        let value = raw.trimmingCharacters(in: .whitespaces)
        if let quote = value.first, quote == "\"" || quote == "'" {
            let inner = value.dropFirst()
            if let end = inner.firstIndex(of: quote) {
                let tail = inner[inner.index(after: end)...]
                    .trimmingCharacters(in: .whitespaces)
                if tail.isEmpty || tail.hasPrefix("#") {
                    return String(inner[..<end])
                }
            }
        }
        if value.hasPrefix("#") { return "" }
        var cut = value.endIndex
        for marker in [" #", "\t#"] {
            if let r = value.range(of: marker), r.lowerBound < cut { cut = r.lowerBound }
        }
        return value[..<cut].trimmingCharacters(in: .whitespaces)
    }

    private static func kind(from raw: String?) -> Kind {
        switch (raw ?? "").lowercased() {
        case "integrated", "integrate": return .integrated
        case "internal": return .internalUse
        default: return .summary  // missing, "summary", "summarize", unknown
        }
    }

    // MARK: - Folder scan

    /// Reads every `*.md` in `directory`. Files that cannot be read as UTF-8
    /// are reported by name rather than silently dropped.
    static func scan(directory: URL) -> TemplateScan {
        guard let items = try? FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: nil
        ) else { return TemplateScan(folderMissing: true) }
        var scan = TemplateScan()
        for url in items where url.pathExtension == "md" {
            let name = url.deletingPathExtension().lastPathComponent
            if let raw = try? String(contentsOf: url, encoding: .utf8) {
                scan.templates.append(parse(name: name, raw: raw))
            } else {
                scan.unreadable.append(name)
            }
        }
        scan.templates.sort { $0.name < $1.name }
        scan.unreadable.sort()
        return scan
    }
}

/// Result of reading the templates folder.
struct TemplateScan: Equatable {
    var templates: [TemplateMeta] = []
    /// Basenames of `.md` files that could not be read as UTF-8 text.
    var unreadable: [String] = []
    var folderMissing = false
}

// MARK: - Settings viewer

/// Read-only browser for `templates/*.md`: names and descriptions on the
/// left, the selected template's exact body on the right. Loads off the main
/// thread whenever `reloadToken` changes.
struct TemplateViewer: View {
    var reloadToken = 0

    @State private var scan: TemplateScan?
    @State private var selection: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let scan {
                if scan.folderMissing {
                    notice("No templates folder found — expected templates/ in the project.")
                } else if scan.templates.isEmpty {
                    notice("The templates folder has no readable .md files.")
                } else {
                    browser(TemplateMeta.viewerOrder(scan.templates))
                }
                if !scan.unreadable.isEmpty {
                    notice("Could not read as UTF-8 text: "
                           + scan.unreadable.map { "\($0).md" }.joined(separator: ", "))
                }
            } else {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("Loading templates…").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .task(id: reloadToken) {
            let loaded = await Task.detached(priority: .userInitiated) {
                Database.shared.scanTemplates()
            }.value
            guard !Task.isCancelled else { return }
            scan = loaded
            if !loaded.templates.contains(where: { $0.name == selection }) {
                selection = TemplateMeta.viewerOrder(loaded.templates).first?.name
            }
        }
    }

    private func browser(_ ordered: [TemplateMeta]) -> some View {
        HStack(alignment: .top, spacing: 10) {
            List(selection: $selection) {
                ForEach(ordered) { template in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(verbatim: template.name)
                            .font(.system(size: 12, design: .monospaced))
                        if template.deprecated {
                            Text(verbatim: template.replacedBy.isEmpty
                                 ? "deprecated" : "deprecated → \(template.replacedBy)")
                                .font(.caption2)
                                .foregroundStyle(.orange)
                        }
                        if !template.description.isEmpty {
                            Text(verbatim: template.description)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                    }
                    .padding(.vertical, 2)
                    .tag(template.name)
                }
            }
            .frame(width: 240, height: 320)
            .cornerRadius(AppUI.tightRadius)

            ScrollView {
                Text(verbatim: displayBody(ordered.first { $0.name == selection }))
                    .font(.system(size: 11, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(AppUI.spacingS)
            }
            .frame(maxWidth: .infinity)
            .frame(height: 320)
            .background(Color(NSColor.textBackgroundColor))
            .cornerRadius(AppUI.tightRadius)
        }
    }

    /// Leading blank lines are trimmed for display only; the body itself is
    /// never modified.
    private func displayBody(_ template: TemplateMeta?) -> String {
        guard let template else { return "" }
        return String(template.body.drop(while: { $0 == "\n" || $0 == "\r\n" }))
    }

    private func notice(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(.orange)
            .fixedSize(horizontal: false, vertical: true)
    }
}
