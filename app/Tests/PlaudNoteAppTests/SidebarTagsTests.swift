import XCTest
@testable import PlaudNoteApp

final class SidebarTagsTests: XCTestCase {
    private let entries = [
        SidebarTags.Entry(tag: "AI/agent", count: 8),
        SidebarTags.Entry(tag: "AI", count: 4),
        SidebarTags.Entry(tag: "교육/강의", count: 3),
        SidebarTags.Entry(tag: "AI/model", count: 2),
    ]

    func testNestedGroupsStartCollapsedAndKeepPrefixCounts() {
        let rows = SidebarTags.rows(for: .init(
            entries: entries, pinned: [], alphabetical: false, nested: true,
            expanded: [], query: ""
        ))
        XCTAssertEqual(rows.map(\.tag), ["AI", "교육"])
        XCTAssertEqual(rows.map(\.count), [14, 3])
        XCTAssertTrue(rows.allSatisfy { $0.isParent && !$0.expanded })
    }

    func testExpansionUsesUniqueStableIDsAndPreservesExactChildFilter() {
        let rows = SidebarTags.rows(for: .init(
            entries: entries, pinned: [], alphabetical: false, nested: true,
            expanded: ["AI"], query: ""
        ))
        XCTAssertEqual(rows.map(\.id), ["prefix:AI", "tag:AI/agent", "tag:AI/model", "prefix:교육"])
        XCTAssertTrue(rows[1].indent)
        XCTAssertFalse(rows[1].isParent)
    }

    func testSearchFindsChildrenOfCollapsedGroupsBeyondInitialWindow() {
        let large = (0..<500).map { SidebarTags.Entry(tag: "group/항목-\($0)", count: 1) }
        let rows = SidebarTags.rows(for: .init(
            entries: large, pinned: [], alphabetical: false, nested: true,
            expanded: [], query: "항목-499"
        ))
        XCTAssertEqual(rows.map(\.tag), ["group", "group/항목-499"])
        XCTAssertTrue(rows[0].expanded)
        XCTAssertEqual(rows[0].count, 500)
    }

    func testPinnedTagsStayFirstWithoutDuplicatesAndFlatSearchUsesWholeIndex() {
        let rows = SidebarTags.rows(for: .init(
            entries: entries, pinned: ["AI/model", "AI/model"], alphabetical: true,
            nested: false, expanded: [], query: "AI"
        ))
        XCTAssertEqual(rows.map(\.tag), ["AI/model", "AI", "AI/agent"])
        XCTAssertTrue(rows[0].pinned)
        XCTAssertEqual(Set(rows.map(\.id)).count, rows.count)
    }
}
