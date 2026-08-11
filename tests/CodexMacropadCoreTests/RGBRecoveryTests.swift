import Foundation
import XCTest
@testable import CodexMacropadCore

final class RGBRecoveryTests: XCTestCase {
    private var temporaryDirectory: URL!
    private var baselineURL: URL!

    override func setUpWithError() throws {
        temporaryDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: temporaryDirectory,
            withIntermediateDirectories: true
        )
        baselineURL = temporaryDirectory.appendingPathComponent("rgb-baseline.json")
    }

    override func tearDownWithError() throws {
        if let temporaryDirectory {
            try? FileManager.default.removeItem(at: temporaryDirectory)
        }
    }

    func testPersistsFullBaselineOnceAndRemovesAfterRestore() throws {
        let baseline = makeBaseline(effect: 6)
        let differentBaseline = makeBaseline(effect: 2)
        let store = RGBRecoveryStore(url: baselineURL)

        try store.saveIfAbsent(baseline)
        try store.saveIfAbsent(differentBaseline)

        XCTAssertEqual(try store.load(), baseline)
        try store.removeAfterSuccessfulRestore()
        XCTAssertNil(try store.load())
    }

    func testUsesFlatVersionedRecoveryJSON() throws {
        let store = RGBRecoveryStore(url: baselineURL)
        try store.saveIfAbsent(makeBaseline(effect: 6))

        let value = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: baselineURL))
                as? [String: Any]
        )

        XCTAssertEqual(value["version"] as? Int, 1)
        XCTAssertEqual(value["mode"] as? Int, 6)
        XCTAssertEqual(value["speed"] as? Int, 44)
        XCTAssertEqual(value["hue"] as? Int, 55)
        XCTAssertEqual(value["saturation"] as? Int, 66)
        XCTAssertEqual(value["brightness"] as? Int, 77)
        XCTAssertNil(value["rgbMode"])
        let device = try XCTUnwrap(value["device"] as? [String: Any])
        XCTAssertEqual(device["vendor_id"] as? Int, 0xE126)
        XCTAssertEqual(device["product_id"] as? Int, 0x0042)
        XCTAssertEqual(device["serial"] as? String, "vial:f64c2b3c")
    }

    func testRejectsMalformedAndUnknownRecoverySchema() throws {
        let store = RGBRecoveryStore(url: baselineURL)

        try "not-json".write(to: baselineURL, atomically: true, encoding: .utf8)
        XCTAssertThrowsError(try store.load())

        try #"{"version":2,"device":{"vendor_id":57638,"product_id":66,"serial":"x"},"mode":2,"speed":1,"hue":2,"saturation":3,"brightness":4}"#
            .write(to: baselineURL, atomically: true, encoding: .utf8)
        XCTAssertThrowsError(try store.load())
    }

    func testPreservesDeviceIdentityForMismatchGuard() throws {
        let store = RGBRecoveryStore(url: baselineURL)
        try store.saveIfAbsent(makeBaseline(effect: 6))

        let loaded = try XCTUnwrap(store.load())

        XCTAssertNotEqual(
            loaded.device,
            RGBDeviceIdentity(vendorID: 1, productID: 2, serial: "other")
        )
    }

    func testAtomicSaveLeavesNoTemporaryFiles() throws {
        let store = RGBRecoveryStore(url: baselineURL)
        try store.saveIfAbsent(makeBaseline(effect: 6))

        let names = try FileManager.default.contentsOfDirectory(
            at: temporaryDirectory,
            includingPropertiesForKeys: nil
        ).map(\.lastPathComponent)

        XCTAssertEqual(names, ["rgb-baseline.json"])
    }

    func testControllerLockHasSingleOwnerAndCanBeReacquired() throws {
        let lockURL = temporaryDirectory.appendingPathComponent("controller.lock")
        var first: ControllerLock? = try ControllerLock.acquire(at: lockURL)

        XCTAssertNotNil(first)
        XCTAssertNil(try ControllerLock.acquire(at: lockURL))

        first = nil
        XCTAssertNotNil(try ControllerLock.acquire(at: lockURL))
        XCTAssertTrue(FileManager.default.fileExists(atPath: lockURL.path))
    }

    private func makeBaseline(effect: UInt16) -> RGBBaseline {
        RGBBaseline(
            version: 1,
            device: RGBDeviceIdentity(
                vendorID: 0xE126,
                productID: 0x0042,
                serial: "vial:f64c2b3c"
            ),
            rgbMode: VialRGBMode(
                effect: effect,
                speed: 44,
                hue: 55,
                saturation: 66,
                brightness: 77
            )
        )
    }
}
