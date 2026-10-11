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

extension SidebarTagsTests {
    func testDateDetectorPreservesSemanticNumberTags() {
        for tag in ["09-18", "2026-09-18", "2026/09/18", "2026.09.18", "0918", "#09-18"] {
            XCTAssertTrue(SidebarTags.isDateOrNumeric(tag), tag)
        }
        for tag in ["AI", "GPT-5", "1on1", "3D", "교육/2026", "2026/교육", "", "---"] {
            XCTAssertFalse(SidebarTags.isDateOrNumeric(tag), tag)
        }
    }

    func testKeywordsFirstAndDatesHiddenUnlessIncludedSearchedOrPinned() {
        let mixed = [SidebarTags.Entry(tag: "09-18", count: 100),
                     .init(tag: "2026", count: 50), .init(tag: "연구", count: 3),
                     .init(tag: "AI", count: 10)]
        var request = SidebarTags.Request(entries: mixed, pinned: [], alphabetical: false,
                                          nested: false, expanded: [], query: "")
        XCTAssertEqual(SidebarTags.rows(for: request).map(\.tag), ["AI", "연구"])
        request.showDateTags = true
        XCTAssertEqual(SidebarTags.rows(for: request).map(\.tag), ["AI", "연구", "09-18", "2026"])
        let searched = SidebarTags.Request(entries: mixed, pinned: [], alphabetical: false,
                                            nested: false, expanded: [], query: "09")
        XCTAssertEqual(SidebarTags.rows(for: searched).map(\.tag), ["09-18"])
        let pinned = SidebarTags.Request(entries: mixed, pinned: ["09-18"], alphabetical: true,
                                         nested: false, expanded: [], query: "")
        XCTAssertEqual(SidebarTags.rows(for: pinned).map(\.tag), ["09-18", "AI", "연구"])
    }

    func testNestedDateSearchRevealsHiddenPath() {
        let mixed = [SidebarTags.Entry(tag: "2026/09/18", count: 100),
                     .init(tag: "연구/AI", count: 3)]
        let normal = SidebarTags.Request(entries: mixed, pinned: [], alphabetical: false,
                                         nested: true, expanded: [], query: "")
        XCTAssertEqual(SidebarTags.rows(for: normal).map(\.tag), ["연구"])
        let searched = SidebarTags.Request(entries: mixed, pinned: [], alphabetical: false,
                                            nested: true, expanded: [], query: "09")
        XCTAssertEqual(SidebarTags.rows(for: searched).map(\.tag), ["2026", "2026/09/18"])
    }
}
