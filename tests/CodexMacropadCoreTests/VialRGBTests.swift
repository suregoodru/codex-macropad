import XCTest
@testable import CodexMacropadCore

final class VialRGBTests: XCTestCase {
    func testEncodesGetRequestsAsPaddedReports() {
        XCTAssertEqual(
            VialRGBCodec.getInfoRequest(),
            [0x08, 0x40] + [UInt8](repeating: 0, count: 30)
        )
        XCTAssertEqual(
            VialRGBCodec.getModeRequest(),
            [0x08, 0x41] + [UInt8](repeating: 0, count: 30)
        )
        XCTAssertEqual(
            VialRGBCodec.getSupportedRequest(after: 0x1234),
            [0x08, 0x42, 0x34, 0x12] + [UInt8](repeating: 0, count: 28)
        )
    }

    func testEncodesSetModeInLittleEndian() {
        let mode = VialRGBMode(
            effect: 6,
            speed: 128,
            hue: 28,
            saturation: 255,
            brightness: 180
        )

        XCTAssertEqual(
            VialRGBCodec.setModeRequest(mode),
            [0x07, 0x41, 0x06, 0x00, 0x80, 0x1C, 0xFF, 0xB4]
                + [UInt8](repeating: 0, count: 24)
        )
    }

    func testParsesInfoModeAndSupportedResponses() throws {
        var info = [UInt8](repeating: 0, count: 32)
        info[0...4] = [0x08, 0x40, 0x01, 0x00, 200]
        XCTAssertEqual(
            try VialRGBCodec.parseInfoResponse(info),
            VialRGBInfo(version: 1, maxBrightness: 200)
        )

        var mode = [UInt8](repeating: 0, count: 32)
        mode[0...7] = [0x08, 0x41, 0x06, 0x00, 128, 28, 255, 180]
        XCTAssertEqual(
            try VialRGBCodec.parseModeResponse(mode),
            VialRGBMode(
                effect: 6,
                speed: 128,
                hue: 28,
                saturation: 255,
                brightness: 180
            )
        )

        var supported = [UInt8](repeating: 0xFF, count: 32)
        supported[0...7] = [0x08, 0x42, 0x02, 0x00, 0x06, 0x00, 0xFF, 0xFF]
        XCTAssertEqual(try VialRGBCodec.parseSupportedResponse(supported), [2, 6])
    }

    func testRejectsWrongLengthAndWrongEcho() {
        XCTAssertThrowsError(try VialRGBCodec.parseInfoResponse([0x08, 0x40]))

        var wrongEcho = [UInt8](repeating: 0, count: 32)
        wrongEcho[0...1] = [0x08, 0x41]
        XCTAssertThrowsError(try VialRGBCodec.parseInfoResponse(wrongEcho))

        var wrongSetEcho = [UInt8](repeating: 0, count: 32)
        wrongSetEcho[0...1] = [0x07, 0x40]
        XCTAssertThrowsError(try VialRGBCodec.validateSetModeResponse(wrongSetEcho))
    }

    func testAcceptsEchoedSetModeResponse() throws {
        var response = [UInt8](repeating: 0, count: 32)
        response[0...1] = [0x07, 0x41]

        XCTAssertNoThrow(try VialRGBCodec.validateSetModeResponse(response))
    }

    func testBuildsStatusProfilesAgainstReportedMaximumBrightness() throws {
        let info = VialRGBInfo(version: 1, maxBrightness: 200)
        let supported: Set<UInt16> = [2, 6]

        XCTAssertEqual(
            try RGBProfiles.mode(for: .working, info: info, supportedEffects: supported),
            VialRGBMode(effect: 2, speed: 128, hue: 170, saturation: 255, brightness: 70)
        )
        XCTAssertEqual(
            try RGBProfiles.mode(for: .approval, info: info, supportedEffects: supported),
            VialRGBMode(effect: 6, speed: 128, hue: 28, saturation: 255, brightness: 140)
        )
        XCTAssertEqual(
            try RGBProfiles.mode(for: .done, info: info, supportedEffects: supported),
            VialRGBMode(effect: 6, speed: 128, hue: 85, saturation: 255, brightness: 120)
        )
    }

    func testFallsBackToSolidWhenBreathingIsUnsupported() throws {
        let info = VialRGBInfo(version: 1, maxBrightness: 200)

        let approval = try RGBProfiles.mode(
            for: .approval,
            info: info,
            supportedEffects: [2]
        )
        let done = try RGBProfiles.mode(for: .done, info: info, supportedEffects: [2])

        XCTAssertEqual(approval.effect, 2)
        XCTAssertEqual(approval.hue, 28)
        XCTAssertEqual(approval.brightness, 140)
        XCTAssertEqual(done.effect, 2)
        XCTAssertEqual(done.hue, 85)
        XCTAssertEqual(done.brightness, 120)
    }

    func testRejectsUnsupportedVialRGBVersion() {
        XCTAssertThrowsError(
            try RGBProfiles.mode(
                for: .working,
                info: VialRGBInfo(version: 2, maxBrightness: 200),
                supportedEffects: [2, 6]
            )
        )
    }

    func testNoEncoderCanProduceLightingSaveCommand() {
        let mode = VialRGBMode(effect: 2, speed: 0, hue: 0, saturation: 0, brightness: 0)
        let requests = [
            VialRGBCodec.getInfoRequest(),
            VialRGBCodec.getModeRequest(),
            VialRGBCodec.getSupportedRequest(after: 0),
            VialRGBCodec.setModeRequest(mode),
        ]

        XCTAssertTrue(requests.allSatisfy { $0.count == 32 && $0[0] != 0x09 })
    }
}
