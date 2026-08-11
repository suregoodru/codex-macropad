import XCTest
@testable import CodexMacropadCore

final class HIDResponseChannelTests: XCTestCase {
    func testReusesStableBufferAndResetsResponseBetweenRequests() {
        let channel = HIDResponseChannel(bufferCount: 33)
        let initialBuffer = channel.buffer

        channel.begin(expectedEcho: [0x07, 0x01])
        channel.receive([0x07, 0x01] + Array(repeating: 0, count: 30))
        XCTAssertNotNil(channel.response)

        channel.begin(expectedEcho: [0x07, 0x02])
        XCTAssertEqual(channel.buffer, initialBuffer)
        XCTAssertNil(channel.response)

        channel.receive([0x07, 0x01] + Array(repeating: 0, count: 30))
        XCTAssertNil(channel.response)

        channel.receive([0] + [0x07, 0x02] + Array(repeating: 0, count: 30))
        XCTAssertEqual(Array(channel.response?.prefix(2) ?? []), [0x07, 0x02])
    }
}
