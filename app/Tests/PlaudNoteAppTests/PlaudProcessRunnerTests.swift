import Foundation
import XCTest
@testable import PlaudNoteApp

final class PlaudProcessRunnerTests: XCTestCase {
    private func python(_ script: String) -> Process {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = ["-c", script]
        return process
    }

    func testDrainsBothPipesBeforeWaitingAndFeedsLargeInput() {
        let size = 1_048_576
        let result = PlaudProcessRunner.run(python("""
        import sys
        sys.stdout.write('o' * \(size)); sys.stdout.flush()
        sys.stderr.write('e' * \(size)); sys.stderr.flush()
        data = sys.stdin.read()
        print(len(data))
        """), stdin: String(repeating: "i", count: size), timeout: 10)
        XCTAssertEqual(result.exitCode, 0)
        XCTAssertEqual(result.stdout, String(repeating: "o", count: size) + "\(size)\n")
        XCTAssertEqual(result.stderr, String(repeating: "e", count: size))
    }

    func testTimeoutEscalatesWhenChildIgnoresTermination() {
        let start = Date()
        let result = PlaudProcessRunner.run(python("""
        import signal, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print('ready', flush=True)
        time.sleep(30)
        """), timeout: 0.5)
        XCTAssertEqual(result.exitCode, -1)
        XCTAssertTrue(result.stderr.contains("timed out"))
        XCTAssertLessThan(Date().timeIntervalSince(start), 5)
    }

    func testNonzeroExitPreservesSeparateStreams() {
        let result = PlaudProcessRunner.run(python("""
        import sys
        print('partial')
        print('failed', file=sys.stderr)
        sys.exit(7)
        """), timeout: 5)
        XCTAssertEqual(result.exitCode, 7)
        XCTAssertEqual(result.stdout, "partial\n")
        XCTAssertEqual(result.stderr, "failed\n")
    }

    func testRuntimeDoesNotResolvePackages() throws {
        let process = try PlaudCommand.makeProcess(args: ["auth", "--json"])
        XCTAssertTrue(process.executableURL!.path.hasSuffix(".venv/bin/python"))
        XCTAssertEqual(process.arguments, ["-m", "cli.main", "auth", "--json"])
    }
}
