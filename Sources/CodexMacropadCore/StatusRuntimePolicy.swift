import Foundation

public protocol MacropadIO: AnyObject {
    var identity: RGBDeviceIdentity { get }
    func writeDisplay(_ snapshot: DisplaySnapshot) throws
    func clearDisplay() throws
    func readRGBInfo() throws -> VialRGBInfo
    func readSupportedEffects() throws -> Set<UInt16>
    func readRGBMode() throws -> VialRGBMode
    func setRGBMode(_ mode: VialRGBMode) throws
}

public enum RuntimeDecision: Equatable, Sendable {
    case continueAfter(TimeInterval)
    case finished
}

public enum StatusRuntimeError: LocalizedError, Equatable {
    case recoveryDeviceMismatch

    public var errorDescription: String? {
        switch self {
        case .recoveryDeviceMismatch:
            return "RGB recovery belongs to a different device"
        }
    }
}

public final class StatusRuntimePolicy {
    private var info: VialRGBInfo?
    private var supportedEffects: Set<UInt16>?
    private var lastAppliedStatus: TaskStatus?
    private var lastAppliedMode: VialRGBMode?
    private var rgbDisabled = false

    public init() {}

    public func handle(
        snapshot: DisplaySnapshot,
        device: MacropadIO,
        recoveryStore: RGBRecoveryStore,
        diagnostic: (String) -> Void
    ) throws -> RuntimeDecision {
        guard let status = snapshot.status else {
            try device.clearDisplay()
            restoreBaseline(
                device: device,
                recoveryStore: recoveryStore,
                diagnostic: diagnostic
            )
            return .finished
        }

        do {
            try device.writeDisplay(snapshot)
        } catch {
            diagnostic("Display update failed; retrying: \(error.localizedDescription)")
            return .continueAfter(0.5)
        }
        guard !rgbDisabled else {
            return .continueAfter(0.5)
        }

        do {
            try prepareRGB(device: device, recoveryStore: recoveryStore)
        } catch {
            rgbDisabled = true
            diagnostic("RGB disabled for this run: \(error.localizedDescription)")
            return .continueAfter(0.5)
        }

        if let info, let supportedEffects {
            do {
                let wasExternallyOverwritten = try captureExternalRGBChange(
                    device: device,
                    recoveryStore: recoveryStore
                )
                if lastAppliedStatus != status || wasExternallyOverwritten {
                    let mode = try RGBProfiles.mode(
                        for: status,
                        info: info,
                        supportedEffects: supportedEffects
                    )
                    try device.setRGBMode(mode)
                    lastAppliedStatus = status
                    lastAppliedMode = mode
                }
            } catch {
                diagnostic("RGB status update failed: \(error.localizedDescription)")
            }
        }
        return .continueAfter(0.5)
    }

    private func prepareRGB(
        device: MacropadIO,
        recoveryStore: RGBRecoveryStore
    ) throws {
        guard info == nil || supportedEffects == nil else {
            return
        }

        let savedBaseline = try recoveryStore.load()
        if let savedBaseline, savedBaseline.device != device.identity {
            throw StatusRuntimeError.recoveryDeviceMismatch
        }

        let currentInfo = try device.readRGBInfo()
        let currentSupportedEffects = try device.readSupportedEffects()
        _ = try RGBProfiles.mode(
            for: .working,
            info: currentInfo,
            supportedEffects: currentSupportedEffects
        )

        if savedBaseline == nil {
            let currentMode = try device.readRGBMode()
            try recoveryStore.saveIfAbsent(
                RGBBaseline(version: 1, device: device.identity, rgbMode: currentMode)
            )
        }
        info = currentInfo
        supportedEffects = currentSupportedEffects
    }

    private func captureExternalRGBChange(
        device: MacropadIO,
        recoveryStore: RGBRecoveryStore
    ) throws -> Bool {
        guard let lastAppliedMode else {
            return false
        }
        let currentMode = try device.readRGBMode()
        guard currentMode != lastAppliedMode else {
            return false
        }
        try recoveryStore.saveReplacing(
            RGBBaseline(version: 1, device: device.identity, rgbMode: currentMode)
        )
        return true
    }

    private func restoreBaseline(
        device: MacropadIO,
        recoveryStore: RGBRecoveryStore,
        diagnostic: (String) -> Void
    ) {
        do {
            guard var baseline = try recoveryStore.load() else {
                return
            }
            guard baseline.device == device.identity else {
                throw StatusRuntimeError.recoveryDeviceMismatch
            }
            if let lastAppliedMode {
                let currentMode = try device.readRGBMode()
                if currentMode != lastAppliedMode {
                    baseline = RGBBaseline(
                        version: 1,
                        device: device.identity,
                        rgbMode: currentMode
                    )
                    try recoveryStore.saveReplacing(baseline)
                }
            }
            try device.setRGBMode(baseline.rgbMode)
            try recoveryStore.removeAfterSuccessfulRestore()
        } catch {
            diagnostic("RGB restore failed: \(error.localizedDescription)")
        }
    }
}
