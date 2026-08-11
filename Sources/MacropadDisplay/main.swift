import CodexMacropadCore
import Foundation

private let artistDataType: UInt8 = 0xAD
private let titleDataType: UInt8 = 0xAE

private enum DisplayError: LocalizedError {
    case invalidArguments(String)

    var errorDescription: String? {
        switch self {
        case .invalidArguments(let message):
            return message
        }
    }
}

private struct Options {
    let artist: String
    let title: String
    let duration: TimeInterval
    let interval: TimeInterval

    static func parse(_ arguments: [String]) throws -> Options {
        var values: [String: String] = [:]
        var index = 0
        while index < arguments.count {
            let key = arguments[index]
            guard key.hasPrefix("--"), index + 1 < arguments.count else {
                throw DisplayError.invalidArguments(
                    "Usage: MacropadDisplay --artist TEXT --title TEXT [--duration 15] [--interval 0.5]"
                )
            }
            values[key] = arguments[index + 1]
            index += 2
        }

        guard let artist = values["--artist"], let title = values["--title"] else {
            throw DisplayError.invalidArguments("Both --artist and --title are required")
        }
        let duration = try positiveNumber(values["--duration"] ?? "15", name: "--duration")
        let interval = try positiveNumber(values["--interval"] ?? "0.5", name: "--interval")
        return Options(artist: artist, title: title, duration: duration, interval: interval)
    }

    private static func positiveNumber(_ value: String, name: String) throws -> Double {
        guard let number = Double(value), number > 0 else {
            throw DisplayError.invalidArguments("\(name) must be a positive number")
        }
        return number
    }
}

private func parseDataType(_ value: String) throws -> UInt8 {
    let digits = value.lowercased().hasPrefix("0x") ? String(value.dropFirst(2)) : value
    guard let parsed = UInt8(digits, radix: 16) else {
        throw DisplayError.invalidArguments("Invalid data type: \(value)")
    }
    return parsed
}

private func hex(_ bytes: [UInt8]) -> String {
    bytes.map { String(format: "%02x", $0) }.joined()
}

private func packets(for snapshot: DisplaySnapshot) -> ([UInt8], [UInt8]) {
    (
        RawHIDPacket.text(dataType: artistDataType, value: snapshot.artist),
        RawHIDPacket.text(dataType: titleDataType, value: snapshot.title)
    )
}

private func value(after name: String, in arguments: [String]) -> String? {
    guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else {
        return nil
    }
    return arguments[index + 1]
}

private func encodeStateOnce(path: String) {
    let snapshot = DisplayStateStore(stateURL: URL(fileURLWithPath: path)).snapshot()
    let (artistPacket, titlePacket) = packets(for: snapshot)
    print(hex(artistPacket))
    print(hex(titlePacket))
}

private func encodeRGBProfile(_ arguments: [String]) throws {
    guard
        arguments.count == 4,
        let status = TaskStatus(rawValue: arguments[1]),
        let maxBrightness = UInt8(arguments[2])
    else {
        throw DisplayError.invalidArguments(
            "Usage: MacropadDisplay --encode-rgb-profile STATUS MAX_BRIGHTNESS EFFECTS"
        )
    }
    let effects = Set(
        try arguments[3].split(separator: ",").map { value -> UInt16 in
            guard let effect = UInt16(value) else {
                throw DisplayError.invalidArguments("Invalid RGB effect: \(value)")
            }
            return effect
        }
    )
    let mode = try RGBProfiles.mode(
        for: status,
        info: VialRGBInfo(version: 1, maxBrightness: maxBrightness),
        supportedEffects: effects
    )
    print(hex(VialRGBCodec.setModeRequest(mode)))
}

private func run(_ arguments: [String]) throws {
    if arguments.first == "--encode-only" {
        guard arguments.count == 3 else {
            throw DisplayError.invalidArguments(
                "Usage: MacropadDisplay --encode-only DATA_TYPE TEXT"
            )
        }
        print(hex(RawHIDPacket.text(dataType: try parseDataType(arguments[1]), value: arguments[2])))
        return
    }

    if arguments.first == "--encode-rgb-profile" {
        try encodeRGBProfile(arguments)
        return
    }

    if let statePath = value(after: "--state-file", in: arguments) {
        if arguments.contains("--encode-state-once") {
            encodeStateOnce(path: statePath)
        } else {
            try runStateFile(statePath, arguments: arguments)
        }
        return
    }

    let options = try Options.parse(arguments)
    let device = try MacropadDevice()
    let artistPacket = RawHIDPacket.text(dataType: artistDataType, value: options.artist)
    let titlePacket = RawHIDPacket.text(dataType: titleDataType, value: options.title)
    let deadline = Date().addingTimeInterval(options.duration)

    print("Sending \(options.artist) / \(options.title) to M4CR0Pad v3 for \(options.duration)s")
    repeat {
        try device.write(artistPacket)
        Thread.sleep(forTimeInterval: 0.02)
        try device.write(titlePacket)
        Thread.sleep(forTimeInterval: options.interval)
    } while Date() < deadline
    print("Finished")
}

do {
    try run(Array(CommandLine.arguments.dropFirst()))
} catch {
    FileHandle.standardError.write(Data("Error: \(error.localizedDescription)\n".utf8))
    exit(EXIT_FAILURE)
}
