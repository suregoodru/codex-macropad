import Foundation
import XCTest
@testable import CodexMacropadCore

final class DisplayStateMonitorTests: XCTestCase {
    private let active = DisplaySnapshot(
        artist: "CODEX · 1 ACTIVE",
        title: "ANALYZE: macropad",
        status: .working,
        phase: .analyze,
        activeCount: 1
    )

    func testKeepsLastActiveSnapshotDuringInvalidReadGracePeriod() {
        let monitor = makeMonitor([
            .valid(active),
            .invalid,
            .invalid,
            .invalid,
        ])

        XCTAssertEqual(monitor.snapshot(at: date(100)), active)
        XCTAssertEqual(monitor.snapshot(at: date(101)), active)
        XCTAssertEqual(monitor.snapshot(at: date(102.999)), active)
        XCTAssertEqual(monitor.snapshot(at: date(103)), .inactive)
    }

    func testValidInactiveSnapshotBypassesGracePeriod() {
        let monitor = makeMonitor([
            .valid(active),
            .invalid,
            .valid(.inactive),
            .invalid,
        ])

        XCTAssertEqual(monitor.snapshot(at: date(100)), active)
        XCTAssertEqual(monitor.snapshot(at: date(101)), active)
        XCTAssertEqual(monitor.snapshot(at: date(101.5)), .inactive)
        XCTAssertEqual(monitor.snapshot(at: date(101.6)), .inactive)
    }

    func testValidRecoveryResetsInvalidReadGracePeriod() {
        let monitor = makeMonitor([
            .valid(active),
            .invalid,
            .valid(active),
            .invalid,
            .invalid,
        ])

        XCTAssertEqual(monitor.snapshot(at: date(100)), active)
        XCTAssertEqual(monitor.snapshot(at: date(101)), active)
        XCTAssertEqual(monitor.snapshot(at: date(102)), active)
        XCTAssertEqual(monitor.snapshot(at: date(103)), active)
        XCTAssertEqual(monitor.snapshot(at: date(104.999)), active)
    }

    private func makeMonitor(
        _ results: [DisplayStateReadResult]
    ) -> DisplayStateMonitor {
        var iterator = results.makeIterator()
        return DisplayStateMonitor(gracePeriod: 2) { _ in
            iterator.next() ?? .invalid
        }
    }

    private func date(_ seconds: TimeInterval) -> Date {
        Date(timeIntervalSince1970: seconds)
    }
}
