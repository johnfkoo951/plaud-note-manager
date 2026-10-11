import Foundation

/// A live-process snapshot, not persisted lifecycle status. Build once per list
/// render; each card performs a dictionary lookup, with no DB or disk access.
enum RecordingActivity {
    static func snapshot(dual: Set<String>, metadata: Set<String>, meeting: Set<String>,
                         vault: Set<String>, transcribing: Set<String>, relabeling: Set<String>,
                         fetching: Set<String>, summaryKeys: Set<String>) -> [String: [String]] {
        var result: [String: [String]] = [:]
        for (ids, label) in [(dual, "듀얼 전사·통합 중"), (transcribing, "음성 전사 중"),
                             (metadata, "메타데이터 생성 중"), (meeting, "회의록 생성 중"),
                             (vault, "볼트 전송 중"), (relabeling, "화자 이름 반영 중"),
                             (fetching, "녹음 데이터 수신 중")] {
            for id in ids { result[id, default: []].append(label) }
        }
        var counts: [String: Int] = [:]
        for key in summaryKeys {
            guard let separator = key.range(of: "::"), separator.lowerBound != key.startIndex else { continue }
            counts[String(key[..<separator.lowerBound]), default: 0] += 1
        }
        for (id, count) in counts {
            result[id, default: []].append(count == 1 ? "AI 요약·통합 생성 중" : "AI 요약·통합 \(count)개 생성 중")
        }
        return result
    }
}
