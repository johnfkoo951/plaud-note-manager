import Foundation

/// A flat, stable row collection avoids NSOutlineView recursively expanding
/// variable-length ForEach children during every sidebar selection update.
struct SidebarTags {
    struct Entry: Equatable {
        let tag: String
        let count: Int
    }

    struct Request: Equatable {
        let entries: [Entry]
        let pinned: [String]
        let alphabetical: Bool
        let nested: Bool
        let expanded: Set<String>
        let query: String
    }

    struct Row: Identifiable, Equatable {
        let tag: String
        let count: Int
        let pinned: Bool
        let isParent: Bool
        let expanded: Bool
        let indent: Bool
        var id: String { (isParent ? "prefix:" : "tag:") + tag }
        var title: String {
            indent ? String(tag.split(separator: "/").last ?? Substring(tag)) : tag
        }
    }

    static func rows(for request: Request) -> [Row] {
        let query = request.query.trimmingCharacters(in: .whitespacesAndNewlines)
        let counts = Dictionary(request.entries.map { ($0.tag, $0.count) },
                                uniquingKeysWith: { first, _ in first })
        let pinned = Set(request.pinned)
        var seen = Set<String>()
        var result = request.pinned.compactMap { tag -> Row? in
            guard seen.insert(tag).inserted,
                  query.isEmpty || tag.localizedStandardContains(query) else { return nil }
            return Row(tag: tag, count: counts[tag] ?? 0, pinned: true,
                       isParent: false, expanded: false, indent: false)
        }
        var rest = request.entries.filter { !pinned.contains($0.tag) }
        if request.alphabetical {
            rest.sort { $0.tag.localizedCaseInsensitiveCompare($1.tag) == .orderedAscending }
        }

        if !request.nested {
            result += rest.filter { query.isEmpty || $0.tag.localizedStandardContains(query) }
                .map { Row(tag: $0.tag, count: $0.count, pinned: false,
                           isParent: false, expanded: false, indent: false) }
            return result
        }

        var order: [String] = []
        var groups: [String: [Entry]] = [:]
        for entry in rest {
            let parent = String(entry.tag.split(separator: "/", maxSplits: 1).first
                                ?? Substring(entry.tag))
            if groups[parent] == nil { order.append(parent) }
            groups[parent, default: []].append(entry)
        }
        for parent in order {
            let members = groups[parent] ?? []
            let matching = members.filter { query.isEmpty || $0.tag.localizedStandardContains(query) }
            guard !matching.isEmpty else { continue }
            let children = members.filter { $0.tag != parent }
            let hasChildren = !children.isEmpty
            // Search reveals matching descendants even when their parent is closed.
            let expanded = !query.isEmpty || request.expanded.contains(parent)
            result.append(Row(tag: parent, count: members.reduce(0) { $0 + $1.count },
                              pinned: false, isParent: hasChildren,
                              expanded: hasChildren && expanded, indent: false))
            if hasChildren && expanded {
                result += matching.filter { $0.tag != parent }.map {
                    Row(tag: $0.tag, count: $0.count, pinned: false,
                        isParent: false, expanded: false, indent: true)
                }
            }
        }
        return result
    }
}
