import XCTest
@testable import PlaudNoteApp

final class PlaudCurlValidationTests: XCTestCase {
    private let valid = """
    curl 'https://api-apne1.plaud.ai/summary/community/templates/weekly_recommend' \\
      -H 'authorization: bearer header.payload.signature' \\
      -H 'x-device-id: device-123'
    """

    func testAcceptsChromePlaudCurl() {
        XCTAssertEqual(PlaudCurlValidator.inspect(valid), .ready)
    }

    func testAcceptsCurlExecutablePathAndLongHeaderOptions() {
        let input = """
        /usr/bin/curl.exe --url=https://api.plaud.ai/file/simple/web \\
          --header='Authorization: Bearer token' \\
          --header='X-Device-ID: device'
        """
        XCTAssertEqual(PlaudCurlValidator.inspect(input), .ready)
    }

    func testExplainsURLOnlyClipboard() {
        XCTAssertEqual(
            PlaudCurlValidator.inspect("https://api-apne1.plaud.ai/file/simple/web"),
            .urlOnly
        )
    }

    func testRejectsLookalikeHost() {
        let input = valid.replacingOccurrences(
            of: "api-apne1.plaud.ai",
            with: "api-attacker.plaud.ai.example.com"
        )
        XCTAssertEqual(PlaudCurlValidator.inspect(input), .wrongTarget)
    }

    func testReportsMissingHeadersSeparately() {
        let withoutAuthorization = """
        curl 'https://api-apne1.plaud.ai/file/simple/web' \\
          -H 'x-device-id: device-123'
        """
        XCTAssertEqual(
            PlaudCurlValidator.inspect(withoutAuthorization),
            .missingAuthorization
        )

        let withoutDevice = """
        curl 'https://api-apne1.plaud.ai/file/simple/web' \\
          -H 'authorization: bearer token'
        """
        XCTAssertEqual(PlaudCurlValidator.inspect(withoutDevice), .missingDeviceID)
    }
}
