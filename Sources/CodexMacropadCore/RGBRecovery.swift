import Darwin
import Foundation

public struct RGBDeviceIdentity: Codable, Equatable, Sendable {
    public let vendorID: Int
    public let productID: Int
    public let serial: String

    public init(vendorID: Int, productID: Int, serial: String) {
        self.vendorID = vendorID
        self.productID = productID
        self.serial = serial
    }

    private enum CodingKeys: String, CodingKey {
        case vendorID = "vendor_id"
        case productID = "product_id"
        case serial
    }
}

public struct RGBBaseline: Codable, Equatable, Sendable {
    public let version: Int
    public let device: RGBDeviceIdentity
    public let rgbMode: VialRGBMode

    public init(version: Int, device: RGBDeviceIdentity, rgbMode: VialRGBMode) {
        self.version = version
        self.device = device
        self.rgbMode = rgbMode
    }

    private enum CodingKeys: String, CodingKey {
        case version
        case device
        case mode
        case speed
        case hue
        case saturation
        case brightness
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        version = try container.decode(Int.self, forKey: .version)
        device = try container.decode(RGBDeviceIdentity.self, forKey: .device)
        rgbMode = VialRGBMode(
            effect: try container.decode(UInt16.self, forKey: .mode),
            speed: try container.decode(UInt8.self, forKey: .speed),
            hue: try container.decode(UInt8.self, forKey: .hue),
            saturation: try container.decode(UInt8.self, forKey: .saturation),
            brightness: try container.decode(UInt8.self, forKey: .brightness)
        )
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(version, forKey: .version)
        try container.encode(device, forKey: .device)
        try container.encode(rgbMode.effect, forKey: .mode)
        try container.encode(rgbMode.speed, forKey: .speed)
        try container.encode(rgbMode.hue, forKey: .hue)
        try container.encode(rgbMode.saturation, forKey: .saturation)
        try container.encode(rgbMode.brightness, forKey: .brightness)
    }
}

public enum RGBRecoveryError: LocalizedError, Equatable {
    case unsupportedVersion(Int)
    case renameFailed(Int32)

    public var errorDescription: String? {
        switch self {
        case .unsupportedVersion(let version):
            return "Unsupported RGB recovery schema version \(version)"
        case .renameFailed(let code):
            return "Unable to install RGB recovery file (errno \(code))"
        }
    }
}

public struct RGBRecoveryStore: Sendable {
    private static let schemaVersion = 1
    private let url: URL

    public init(url: URL) {
        self.url = url
    }

    public func load() throws -> RGBBaseline? {
        guard FileManager.default.fileExists(atPath: url.path) else {
            return nil
        }
        let baseline = try JSONDecoder().decode(
            RGBBaseline.self,
            from: Data(contentsOf: url)
        )
        guard baseline.version == Self.schemaVersion else {
            throw RGBRecoveryError.unsupportedVersion(baseline.version)
        }
        return baseline
    }

    public func saveIfAbsent(_ baseline: RGBBaseline) throws {
        try save(baseline, replacingExisting: false)
    }

    public func saveReplacing(_ baseline: RGBBaseline) throws {
        try save(baseline, replacingExisting: true)
    }

    private func save(_ baseline: RGBBaseline, replacingExisting: Bool) throws {
        if !replacingExisting && FileManager.default.fileExists(atPath: url.path) {
            return
        }
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )

        let temporaryURL = url.deletingLastPathComponent().appendingPathComponent(
            ".\(url.lastPathComponent).\(UUID().uuidString)"
        )
        do {
            let data = try JSONEncoder().encode(baseline)
            guard FileManager.default.createFile(
                atPath: temporaryURL.path,
                contents: nil,
                attributes: [.posixPermissions: 0o600]
            ) else {
                throw CocoaError(.fileWriteUnknown)
            }
            let handle = try FileHandle(forWritingTo: temporaryURL)
            try handle.write(contentsOf: data)
            try handle.write(contentsOf: Data("\n".utf8))
            try handle.synchronize()
            try handle.close()

            if !replacingExisting && FileManager.default.fileExists(atPath: url.path) {
                try FileManager.default.removeItem(at: temporaryURL)
                return
            }
            let result: Int32 = temporaryURL.withUnsafeFileSystemRepresentation { source in
                url.withUnsafeFileSystemRepresentation { destination in
                    guard let source, let destination else { return Int32(-1) }
                    return Darwin.rename(source, destination)
                }
            }
            guard result == 0 else {
                throw RGBRecoveryError.renameFailed(errno)
            }
        } catch {
            try? FileManager.default.removeItem(at: temporaryURL)
            throw error
        }
    }

    public func removeAfterSuccessfulRestore() throws {
        guard FileManager.default.fileExists(atPath: url.path) else {
            return
        }
        try FileManager.default.removeItem(at: url)
    }
}
