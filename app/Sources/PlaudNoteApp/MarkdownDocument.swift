import Foundation

/// Parsed once when a document changes, away from SwiftUI's render path.
/// Blocks retain their inline attributes so hover and progress updates do not
/// run the Markdown parser again for every paragraph.
struct MarkdownDocument {
    enum Kind {
        case heading(Int, AttributedString)
        case bullet(AttributedString)
        case quote(AttributedString)
        case paragraph(AttributedString)
        case code(String)
        case rule
        case blank
    }

    struct Block: Identifiable {
        let id: Int
        let kind: Kind
    }

    let source: String
    let blocks: [Block]

    init(text: String) {
        source = text
        var result: [Block] = []
        var codeLines: [String] = []
        var inCode = false

        func append(_ kind: Kind) {
            result.append(Block(id: result.count, kind: kind))
        }

        for rawLine in text.components(separatedBy: .newlines) {
            let trimmed = rawLine.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("```") {
                if inCode {
                    append(.code(codeLines.joined(separator: "\n")))
                    codeLines.removeAll()
                }
                inCode.toggle()
                continue
            }
            if inCode {
                codeLines.append(rawLine)
            } else if trimmed.isEmpty {
                append(.blank)
            } else if trimmed == "---" || trimmed == "***" || trimmed == "___" {
                append(.rule)
            } else if let heading = Self.heading(trimmed) {
                append(.heading(heading.level, Self.inline(heading.text)))
            } else if trimmed.hasPrefix("- ") || trimmed.hasPrefix("* ") {
                append(.bullet(Self.inline(String(trimmed.dropFirst(2)))))
            } else if trimmed.hasPrefix(">") {
                append(.quote(Self.inline(
                    String(trimmed.dropFirst()).trimmingCharacters(in: .whitespaces)
                )))
            } else {
                append(.paragraph(Self.inline(rawLine)))
            }
        }
        if !codeLines.isEmpty { append(.code(codeLines.joined(separator: "\n"))) }
        blocks = result
    }

    private static func heading(_ line: String) -> (level: Int, text: String)? {
        let hashes = line.prefix { $0 == "#" }.count
        guard hashes > 0, hashes <= 6,
              line.dropFirst(hashes).first == " " else { return nil }
        return (hashes, String(line.dropFirst(hashes + 1))
            .trimmingCharacters(in: .whitespaces))
    }

    private static func inline(_ raw: String) -> AttributedString {
        // Preserve prose ranges ("3~5") without disabling intentional ~~strike~~.
        var cleaned = ""
        var index = raw.startIndex
        while index < raw.endIndex {
            if raw[index] == "~" {
                let next = raw.index(after: index)
                if next < raw.endIndex, raw[next] == "~" {
                    cleaned.append("~~")
                    index = raw.index(after: next)
                } else {
                    cleaned.append("\\~")
                    index = next
                }
            } else {
                cleaned.append(raw[index])
                index = raw.index(after: index)
            }
        }
        return (try? AttributedString(
            markdown: cleaned,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        )) ?? AttributedString(raw)
    }
}
