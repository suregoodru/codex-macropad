import Darwin
import Foundation

@_silgen_name("flock")
private func systemFlock(_ descriptor: Int32, _ operation: Int32) -> Int32

public enum ControllerLockError: LocalizedError, Equatable {
    case openFailed(Int32)
    case lockFailed(Int32)

    public var errorDescription: String? {
        switch self {
        case .openFailed(let code):
            return "Unable to open controller lock (errno \(code))"
        case .lockFailed(let code):
            return "Unable to acquire controller lock (errno \(code))"
        }
    }
}

public final class ControllerLock: @unchecked Sendable {
    private let descriptor: Int32

    private init(descriptor: Int32) {
        self.descriptor = descriptor
    }

    public static func acquire(at url: URL) throws -> ControllerLock? {
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let descriptor = url.withUnsafeFileSystemRepresentation { path -> Int32 in
            guard let path else { return -1 }
            return Darwin.open(path, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        }
        guard descriptor >= 0 else {
            throw ControllerLockError.openFailed(errno)
        }

        guard systemFlock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            let code = errno
            Darwin.close(descriptor)
            if code == EWOULDBLOCK || code == EAGAIN {
                return nil
            }
            throw ControllerLockError.lockFailed(code)
        }
        return ControllerLock(descriptor: descriptor)
    }

    deinit {
        _ = systemFlock(descriptor, LOCK_UN)
        Darwin.close(descriptor)
    }
}
