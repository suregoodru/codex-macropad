import Foundation

public struct VialRGBInfo: Equatable, Sendable {
    public let version: UInt16
    public let maxBrightness: UInt8

    public init(version: UInt16, maxBrightness: UInt8) {
        self.version = version
        self.maxBrightness = maxBrightness
    }
}

public struct VialRGBMode: Codable, Equatable, Sendable {
    public let effect: UInt16
    public let speed: UInt8
    public let hue: UInt8
    public let saturation: UInt8
    public let brightness: UInt8

    public init(
        effect: UInt16,
        speed: UInt8,
        hue: UInt8,
        saturation: UInt8,
        brightness: UInt8
    ) {
        self.effect = effect
        self.speed = speed
        self.hue = hue
        self.saturation = saturation
        self.brightness = brightness
    }
}

public enum VialRGBError: LocalizedError, Equatable {
    case invalidResponseLength(Int)
    case unexpectedResponse(expected: [UInt8], actual: [UInt8])
    case unsupportedVersion(UInt16)

    public var errorDescription: String? {
        switch self {
        case .invalidResponseLength(let length):
            return "VialRGB response has \(length) bytes; expected 32"
        case .unexpectedResponse(let expected, let actual):
            return "VialRGB response echo \(actual) does not match \(expected)"
        case .unsupportedVersion(let version):
            return "Unsupported VialRGB protocol version \(version)"
        }
    }
}

public enum VialRGBCodec {
    public static let reportByteCount = 32

    private static let getCommand: UInt8 = 0x08
    private static let setCommand: UInt8 = 0x07
    private static let infoSubcommand: UInt8 = 0x40
    private static let modeSubcommand: UInt8 = 0x41
    private static let supportedSubcommand: UInt8 = 0x42

    public static func getInfoRequest() -> [UInt8] {
        padded([getCommand, infoSubcommand])
    }

    public static func parseInfoResponse(_ response: [UInt8]) throws -> VialRGBInfo {
        try validate(response, echo: [getCommand, infoSubcommand])
        return VialRGBInfo(
            version: UInt16(response[2]) | UInt16(response[3]) << 8,
            maxBrightness: response[4]
        )
    }

    public static func getModeRequest() -> [UInt8] {
        padded([getCommand, modeSubcommand])
    }

    public static func parseModeResponse(_ response: [UInt8]) throws -> VialRGBMode {
        try validate(response, echo: [getCommand, modeSubcommand])
        return VialRGBMode(
            effect: UInt16(response[2]) | UInt16(response[3]) << 8,
            speed: response[4],
            hue: response[5],
            saturation: response[6],
            brightness: response[7]
        )
    }

    public static func getSupportedRequest(after effect: UInt16) -> [UInt8] {
        padded([
            getCommand,
            supportedSubcommand,
            UInt8(effect & 0x00FF),
            UInt8(effect >> 8),
        ])
    }

    public static func parseSupportedResponse(_ response: [UInt8]) throws -> [UInt16] {
        try validate(response, echo: [getCommand, supportedSubcommand])
        var effects: [UInt16] = []
        var index = 2
        while index + 1 < response.count {
            let effect = UInt16(response[index]) | UInt16(response[index + 1]) << 8
            if effect == 0xFFFF {
                break
            }
            if !effects.contains(effect) {
                effects.append(effect)
            }
            index += 2
        }
        return effects
    }

    public static func setModeRequest(_ mode: VialRGBMode) -> [UInt8] {
        padded([
            setCommand,
            modeSubcommand,
            UInt8(mode.effect & 0x00FF),
            UInt8(mode.effect >> 8),
            mode.speed,
            mode.hue,
            mode.saturation,
            mode.brightness,
        ])
    }

    public static func validateSetModeResponse(_ response: [UInt8]) throws {
        try validate(response, echo: [setCommand, modeSubcommand])
    }

    private static func padded(_ prefix: [UInt8]) -> [UInt8] {
        prefix + [UInt8](repeating: 0, count: reportByteCount - prefix.count)
    }

    private static func validate(_ response: [UInt8], echo: [UInt8]) throws {
        guard response.count == reportByteCount else {
            throw VialRGBError.invalidResponseLength(response.count)
        }
        let actual = Array(response.prefix(echo.count))
        guard actual == echo else {
            throw VialRGBError.unexpectedResponse(expected: echo, actual: actual)
        }
    }
}

public enum RGBProfiles {
    private static let solidEffect: UInt16 = 2
    private static let breathingEffect: UInt16 = 6
    private static let mediumSpeed: UInt8 = 128

    public static func mode(
        for status: TaskStatus,
        info: VialRGBInfo,
        supportedEffects: Set<UInt16>
    ) throws -> VialRGBMode {
        guard info.version == 1 else {
            throw VialRGBError.unsupportedVersion(info.version)
        }

        let degrees: Double
        let brightnessFraction: Double
        let preferredEffect: UInt16
        switch status {
        case .working:
            degrees = 240
            brightnessFraction = 0.35
            preferredEffect = solidEffect
        case .approval:
            degrees = 40
            brightnessFraction = 0.70
            preferredEffect = breathingEffect
        case .done:
            degrees = 120
            brightnessFraction = 0.60
            preferredEffect = breathingEffect
        }

        let effect = supportedEffects.contains(preferredEffect)
            ? preferredEffect
            : solidEffect
        return VialRGBMode(
            effect: effect,
            speed: mediumSpeed,
            hue: UInt8((degrees / 360.0 * 255.0).rounded()),
            saturation: 255,
            brightness: UInt8((Double(info.maxBrightness) * brightnessFraction).rounded())
        )
    }
}
