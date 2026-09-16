import Darwin
import Foundation

/// The installed personal app uses its existing environment directly. Opening
/// a window must never resolve packages, contact a registry, or lock uv's cache.
enum PlaudCommand {
    static let projectRoot = NSString(string: "~/DEV/plaud-note-manager").expandingTildeInPath
    static let cliPath = [
        "\(NSHomeDirectory())/.local/bin", "/opt/homebrew/bin", "/usr/local/bin",
        "/usr/bin", "/bin", "/usr/sbin", "/sbin",
    ].joined(separator: ":")

    static func makeProcess(args: [String]) throws -> Process {
        let python = "\(projectRoot)/.venv/bin/python"
        guard FileManager.default.isExecutableFile(atPath: python) else {
            throw NSError(domain: "PlaudRuntime", code: 1, userInfo: [
                NSLocalizedDescriptionKey:
                    "Plaud Python environment is missing. Run uv sync in \(projectRoot), then reopen the app.",
            ])
        }
        let process = Process()
        process.currentDirectoryURL = URL(fileURLWithPath: projectRoot)
        process.environment = ProcessInfo.processInfo.environment.merging(
            ["PATH": cliPath, "PYTHONUNBUFFERED": "1"]
        ) { _, new in new }
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-m", "cli.main"] + args
        return process
    }

    static func timeout(for args: [String]) -> TimeInterval {
        switch args.first {
        case "auth", "models", "elevenlabs-status", "audio-url": return 30
        case "sync", "detail": return 120
        default: return 7200
        }
    }
}

struct PlaudProcessResult {
    let exitCode: Int32
    let stdout: String
    let stderr: String
}

/// Call off-main. Drain both pipes while the child runs, including while
/// feeding stdin: waiting for exit first deadlocks as soon as a pipe fills.
/// Nonblocking reads also prevent a grandchild holding an inherited pipe from
/// keeping a completed/timed-out command alive. Credentials stay in memory.
enum PlaudProcessRunner {
    static func run(_ process: Process, stdin: String? = nil,
                    timeout: TimeInterval) -> PlaudProcessResult {
        let out = Pipe(), err = Pipe(), input = Pipe()
        process.standardOutput = out
        process.standardError = err
        process.standardInput = input
        var outData = Data(), errData = Data()
        let inputData = Data((stdin ?? "").utf8)
        var inputOffset = 0
        var inputClosed = false
        defer {
            try? out.fileHandleForReading.close()
            try? err.fileHandleForReading.close()
            if !inputClosed { try? input.fileHandleForWriting.close() }
        }
        do { try process.run() } catch {
            return .init(exitCode: -1, stdout: "", stderr: "plaud CLI failed: \(error.localizedDescription)")
        }
        try? out.fileHandleForWriting.close()
        try? err.fileHandleForWriting.close()
        try? input.fileHandleForReading.close()
        let outFD = out.fileHandleForReading.fileDescriptor
        let errFD = err.fileHandleForReading.fileDescriptor
        let inFD = input.fileHandleForWriting.fileDescriptor
        for fd in [outFD, errFD, inFD] {
            _ = fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK)
        }
        _ = fcntl(inFD, F_SETNOSIGPIPE, 1)
        let started = ProcessInfo.processInfo.systemUptime
        var timedOut = false
        var terminatedAt: TimeInterval?
        var buffer = [UInt8](repeating: 0, count: 65_536)
        func drain(_ fd: Int32, into data: inout Data) {
            // Bound each turn so a continuously noisy child cannot starve
            // stderr, stdin, or the timeout watchdog.
            for _ in 0..<16 {
                let count = Darwin.read(fd, &buffer, buffer.count)
                guard count > 0 else { break }
                data.append(contentsOf: buffer.prefix(count))
            }
        }
        while true {
            drain(outFD, into: &outData)
            drain(errFD, into: &errData)
            if !inputClosed {
                if inputOffset < inputData.count {
                    let written = inputData.withUnsafeBytes { bytes in
                        Darwin.write(inFD, bytes.baseAddress!.advanced(by: inputOffset),
                                     min(65_536, inputData.count - inputOffset))
                    }
                    if written > 0 { inputOffset += written }
                    else if written < 0 && errno != EAGAIN && errno != EINTR {
                        inputOffset = inputData.count
                    }
                }
                if inputOffset == inputData.count {
                    try? input.fileHandleForWriting.close()
                    inputClosed = true
                }
            }
            if !process.isRunning { break }
            let now = ProcessInfo.processInfo.systemUptime
            if now - started >= timeout {
                timedOut = true
                if let terminatedAt {
                    if now - terminatedAt >= 1 { kill(process.processIdentifier, SIGKILL) }
                } else {
                    terminatedAt = now
                    process.terminate()
                }
            }
            Thread.sleep(forTimeInterval: 0.01)
        }
        process.waitUntilExit()
        drain(outFD, into: &outData)
        drain(errFD, into: &errData)
        return .init(
            exitCode: timedOut ? -1 : process.terminationStatus,
            stdout: String(decoding: outData, as: UTF8.self),
            stderr: timedOut ? "plaud command timed out after \(Int(timeout))s"
                : String(decoding: errData, as: UTF8.self)
        )
    }
}
