import Foundation
import XCTest
@testable import CodexMacropadCore

final class StatusRuntimePolicyTests: XCTestCase {
    private var temporaryDirectory: URL!
    private var recoveryStore: RGBRecoveryStore!
    private let identity = RGBDeviceIdentity(
        vendorID: 0xE126,
        productID: 0x0042,
        serial: "vial:f64c2b3c"
    )
    private let originalMode = VialRGBMode(
        effect: 3,
        speed: 4,
        hue: 5,
        saturation: 6,
        brightness: 7
    )

    override func setUpWithError() throws {
        temporaryDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: temporaryDirectory,
            withIntermediateDirectories: true
        )
        recoveryStore = RGBRecoveryStore(
            url: temporaryDirectory.appendingPathComponent("rgb-baseline.json")
        )
    }

    override func tearDownWithError() throws {
        if let temporaryDirectory {
            try? FileManager.default.removeItem(at: temporaryDirectory)
        }
    }

    func testCapturesOnceTransitionsWithoutRepeatedRGBAndRestores() throws {
        let device = FakeMacropad(identity: identity, currentMode: originalMode)
        let policy = StatusRuntimePolicy()
        let snapshots = [
            snapshot(.working),
            snapshot(.working),
            snapshot(.approval),
            snapshot(.done),
            DisplaySnapshot.inactive,
        ]

        let decisions = try snapshots.map {
            try policy.handle(
                snapshot: $0,
                device: device,
                recoveryStore: recoveryStore,
                diagnostic: { _ in }
            )
        }

        XCTAssertEqual(device.readModeCount, 5)
        XCTAssertEqual(
            device.setModes,
            [
                VialRGBMode(effect: 2, speed: 128, hue: 170, saturation: 255, brightness: 70),
                VialRGBMode(effect: 6, speed: 128, hue: 28, saturation: 255, brightness: 140),
                VialRGBMode(effect: 6, speed: 128, hue: 85, saturation: 255, brightness: 120),
                originalMode,
            ]
        )
        XCTAssertEqual(device.displayWrites.count, 4)
        XCTAssertEqual(device.clearCount, 1)
        XCTAssertEqual(decisions.last, .finished)
        XCTAssertNil(try recoveryStore.load())
    }

    func testCapturesExternalRGBChangeBeforeStatusTransitionAndRestoresIt() throws {
        let device = FakeMacropad(identity: identity, currentMode: originalMode)
        let policy = StatusRuntimePolicy()
        let userMode = VialRGBMode(
            effect: 13,
            speed: 21,
            hue: 34,
            saturation: 55,
            brightness: 89
        )

        _ = try policy.handle(
            snapshot: snapshot(.working),
            device: device,
            recoveryStore: recoveryStore,
            diagnostic: { _ in }
        )
        device.currentMode = userMode
        _ = try policy.handle(
            snapshot: snapshot(.approval),
            device: device,
            recoveryStore: recoveryStore,
            diagnostic: { _ in }
        )
        _ = try policy.handle(
            snapshot: .inactive,
            device: device,
            recoveryStore: recoveryStore,
            diagnostic: { _ in }
        )

        XCTAssertEqual(device.setModes.last, userMode)
        XCTAssertEqual(device.currentMode, userMode)
        XCTAssertNil(try recoveryStore.load())
    }

    func testReappliesDoneRGBAfterExternalOverwriteAndRestoresOverwriteAsBaseline() throws {
        let device = FakeMacropad(identity: identity, currentMode: originalMode)
        let policy = StatusRuntimePolicy()
        let entropyMode = VialRGBMode(
            effect: 13,
            speed: 21,
            hue: 34,
            saturation: 55,
            brightness: 89
        )
        let doneMode = VialRGBMode(
            effect: 6,
            speed: 128,
            hue: 85,
            saturation: 255,
            brightness: 120
        )

        _ = try policy.handle(
            snapshot: snapshot(.done),
            device: device,
            recoveryStore: recoveryStore,
            diagnostic: { _ in }
        )
        device.currentMode = entropyMode

        _ = try policy.handle(
            snapshot: snapshot(.done),
            device: device,
            recoveryStore: recoveryStore,
            diagnostic: { _ in }
        )

        XCTAssertEqual(device.currentMode, doneMode)
        XCTAssertEqual(try recoveryStore.load()?.rgbMode, entropyMode)

        _ = try policy.handle(
            snapshot: .inactive,
            device: device,
            recoveryStore: recoveryStore,
            diagnostic: { _ in }
        )

        XCTAssertEqual(device.currentMode, entropyMode)
        XCTAssertNil(try recoveryStore.load())
    }

    func testReusesCrashBaselineWithoutReadingNotificationColorAsOriginal() throws {
        let baseline = RGBBaseline(version: 1, device: identity, rgbMode: originalMode)
        try recoveryStore.saveIfAbsent(baseline)
        let device = FakeMacropad(
            identity: identity,
            currentMode: VialRGBMode(
                effect: 2,
                speed: 128,
                hue: 170,
                saturation: 255,
                brightness: 70
            )
        )
        let policy = StatusRuntimePolicy()

        _ = try policy.handle(
            snapshot: snapshot(.approval),
            device: device,
            recoveryStore: recoveryStore,
            diagnostic: { _ in }
        )

        XCTAssertEqual(device.readModeCount, 0)
        XCTAssertEqual(device.setModes.first?.effect, 6)
        XCTAssertEqual(try recoveryStore.load(), baseline)
    }

    func testRGBGetFailureDoesNotStopDisplayAndDisablesFurtherRGBReads() throws {
        let device = FakeMacropad(identity: identity, currentMode: originalMode)
        device.failInfoRead = true
        let policy = StatusRuntimePolicy()
        var diagnostics: [String] = []

        let first = try policy.handle(
            snapshot: snapshot(.working),
            device: device,
            recoveryStore: recoveryStore,
            diagnostic: { diagnostics.append($0) }
        )
        let second = try policy.handle(
            snapshot: snapshot(.working),
            device: device,
            recoveryStore: recoveryStore,
            diagnostic: { diagnostics.append($0) }
        )

        XCTAssertEqual(first, .continueAfter(0.5))
        XCTAssertEqual(second, .continueAfter(0.5))
        XCTAssertEqual(device.displayWrites.count, 2)
        XCTAssertEqual(device.infoReadCount, 1)
        XCTAssertTrue(device.setModes.isEmpty)
        XCTAssertEqual(diagnostics.count, 1)
    }

    func testTransientDisplayWriteFailureKeepsRuntimeAliveAndRetries() throws {
        let device = FakeMacropad(identity: identity, currentMode: originalMode)
        device.displayFailuresRemaining = 1
        let policy = StatusRuntimePolicy()
        var diagnostics: [String] = []

        let first = try policy.handle(
            snapshot: snapshot(.working),
            device: device,
            recoveryStore: recoveryStore,
            diagnostic: { diagnostics.append($0) }
        )
        let second = try policy.handle(
            snapshot: snapshot(.working),
            device: device,
            recoveryStore: recoveryStore,
            diagnostic: { diagnostics.append($0) }
        )

        XCTAssertEqual(first, .continueAfter(0.5))
        XCTAssertEqual(second, .continueAfter(0.5))
        XCTAssertEqual(device.displayWrites.count, 1)
        XCTAssertEqual(device.setModes.count, 1)
        XCTAssertEqual(diagnostics.count, 1)
    }

    func testFailedRestorePreservesRecoveryFile() throws {
        let baseline = RGBBaseline(version: 1, device: identity, rgbMode: originalMode)
        try recoveryStore.saveIfAbsent(baseline)
        let device = FakeMacropad(identity: identity, currentMode: originalMode)
        device.failSet = true
        let policy = StatusRuntimePolicy()
        var diagnostics: [String] = []

        let decision = try policy.handle(
            snapshot: .inactive,
            device: device,
            recoveryStore: recoveryStore,
            diagnostic: { diagnostics.append($0) }
        )

        XCTAssertEqual(decision, .finished)
        XCTAssertEqual(try recoveryStore.load(), baseline)
        XCTAssertEqual(device.clearCount, 1)
        XCTAssertEqual(diagnostics.count, 1)
    }

    func testInactiveWithoutRecoveryClearsAndFinishes() throws {
        let device = FakeMacropad(identity: identity, currentMode: originalMode)
        let policy = StatusRuntimePolicy()

        let decision = try policy.handle(
            snapshot: .inactive,
            device: device,
            recoveryStore: recoveryStore,
            diagnostic: { _ in }
        )

        XCTAssertEqual(decision, .finished)
        XCTAssertEqual(device.clearCount, 1)
        XCTAssertTrue(device.setModes.isEmpty)
    }

    func testMismatchedRecoveryDeviceIsPreservedAndNotApplied() throws {
        let otherIdentity = RGBDeviceIdentity(vendorID: 1, productID: 2, serial: "other")
        let baseline = RGBBaseline(version: 1, device: otherIdentity, rgbMode: originalMode)
        try recoveryStore.saveIfAbsent(baseline)
        let device = FakeMacropad(identity: identity, currentMode: originalMode)
        let policy = StatusRuntimePolicy()
        var diagnostics: [String] = []

        _ = try policy.handle(
            snapshot: .inactive,
            device: device,
            recoveryStore: recoveryStore,
            diagnostic: { diagnostics.append($0) }
        )

        XCTAssertTrue(device.setModes.isEmpty)
        XCTAssertEqual(try recoveryStore.load(), baseline)
        XCTAssertEqual(diagnostics.count, 1)
    }

    private func snapshot(_ status: TaskStatus) -> DisplaySnapshot {
        DisplaySnapshot(
            artist: "CODEX · 1 ACTIVE",
            title: "\(status.rawValue.uppercased()): macropad",
            status: status,
            activeCount: 1
        )
    }
}

private enum FakeError: Error {
    case failed
}

private final class FakeMacropad: MacropadIO {
    let identity: RGBDeviceIdentity
    var currentMode: VialRGBMode
    var displayWrites: [DisplaySnapshot] = []
    var setModes: [VialRGBMode] = []
    var clearCount = 0
    var readModeCount = 0
    var infoReadCount = 0
    var displayFailuresRemaining = 0
    var failInfoRead = false
    var failSet = false

    init(identity: RGBDeviceIdentity, currentMode: VialRGBMode) {
        self.identity = identity
        self.currentMode = currentMode
    }

    func writeDisplay(_ snapshot: DisplaySnapshot) throws {
        if displayFailuresRemaining > 0 {
            displayFailuresRemaining -= 1
            throw FakeError.failed
        }
        displayWrites.append(snapshot)
    }

    func clearDisplay() throws {
        clearCount += 1
    }

    func readRGBInfo() throws -> VialRGBInfo {
        infoReadCount += 1
        if failInfoRead { throw FakeError.failed }
        return VialRGBInfo(version: 1, maxBrightness: 200)
    }

    func readSupportedEffects() throws -> Set<UInt16> {
        [2, 6]
    }

    func readRGBMode() throws -> VialRGBMode {
        readModeCount += 1
        return currentMode
    }

    func setRGBMode(_ mode: VialRGBMode) throws {
        if failSet { throw FakeError.failed }
        setModes.append(mode)
        currentMode = mode
    }
}
