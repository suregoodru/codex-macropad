import Foundation

public final class DisplayStateMonitor {
    public typealias Reader = (Date) -> DisplayStateReadResult

    private let gracePeriod: TimeInterval
    private let reader: Reader
    private var lastValidSnapshot: DisplaySnapshot?
    private var invalidSince: Date?

    public convenience init(
        store: DisplayStateStore,
        gracePeriod: TimeInterval = 2
    ) {
        self.init(gracePeriod: gracePeriod) { date in
            store.read(at: date)
        }
    }

    public init(
        gracePeriod: TimeInterval = 2,
        reader: @escaping Reader
    ) {
        self.gracePeriod = gracePeriod
        self.reader = reader
    }

    public func snapshot(at date: Date = Date()) -> DisplaySnapshot {
        switch reader(date) {
        case .valid(let snapshot):
            lastValidSnapshot = snapshot
            invalidSince = nil
            return snapshot

        case .invalid:
            guard let previous = lastValidSnapshot, previous.isActive else {
                return .inactive
            }

            let startedAt = invalidSince ?? date
            invalidSince = startedAt
            guard date.timeIntervalSince(startedAt) < gracePeriod else {
                return .inactive
            }
            return previous
        }
    }
}
