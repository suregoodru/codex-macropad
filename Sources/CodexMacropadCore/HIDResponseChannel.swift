public final class HIDResponseChannel {
    public let buffer: UnsafeMutablePointer<UInt8>
    public let bufferCount: Int
    public private(set) var response: [UInt8]?

    private var expectedEcho: [UInt8] = []

    public init(bufferCount: Int) {
        precondition(bufferCount > 0)
        self.bufferCount = bufferCount
        buffer = .allocate(capacity: bufferCount)
        buffer.initialize(repeating: 0, count: bufferCount)
    }

    deinit {
        buffer.deinitialize(count: bufferCount)
        buffer.deallocate()
    }

    public func begin(expectedEcho: [UInt8]) {
        self.expectedEcho = expectedEcho
        response = nil
    }

    public func receive(_ rawReport: [UInt8]) {
        let payload: [UInt8]
        if rawReport.count == bufferCount, rawReport.first == 0 {
            payload = Array(rawReport.dropFirst())
        } else if rawReport.count == bufferCount - 1 {
            payload = rawReport
        } else {
            return
        }
        guard Array(payload.prefix(expectedEcho.count)) == expectedEcho else {
            return
        }
        response = payload
    }
}
