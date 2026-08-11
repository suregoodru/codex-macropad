import CodexMacropadCore
import Foundation

private final class RateLimitedDiagnostic {
    private let interval: TimeInterval
    private var lastMessage: String?
    private var lastDate = Date.distantPast

    init(interval: TimeInterval) {
        self.interval = interval
    }

    func write(_ message: String) {
        let now = Date()
        guard message != lastMessage || now.timeIntervalSince(lastDate) >= interval else {
            return
        }
        lastMessage = message
        lastDate = now
        FileHandle.standardError.write(Data("\(message)\n".utf8))
    }
}

func runStateFile(_ path: String, arguments: [String]) throws {
    let stateURL = URL(fileURLWithPath: path)
    let directory = stateURL.deletingLastPathComponent()
    guard let controllerLock = try ControllerLock.acquire(
        at: directory.appendingPathComponent("controller.lock")
    ) else {
        return
    }

    try withExtendedLifetime(controllerLock) {
        let store = DisplayStateStore(stateURL: stateURL)
        let monitor = DisplayStateMonitor(store: store, gracePeriod: 2)
        let recoveryStore = RGBRecoveryStore(
            url: directory.appendingPathComponent("rgb-baseline.json")
        )
        let device = try MacropadDevice()
        let policy = StatusRuntimePolicy()
        let diagnostic = RateLimitedDiagnostic(interval: 30)

        while true {
            let decision = try policy.handle(
                snapshot: monitor.snapshot(),
                device: device,
                recoveryStore: recoveryStore,
                diagnostic: diagnostic.write
            )
            switch decision {
            case .continueAfter(let interval):
                Thread.sleep(forTimeInterval: interval)
            case .finished:
                return
            }
        }
    }
}
