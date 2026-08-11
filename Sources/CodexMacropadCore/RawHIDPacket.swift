import Foundation

public enum RawHIDPacket {
    public static let byteCount = 32
    public static let maximumTextByteCount = 30

    public static func text(dataType: UInt8, value: String) -> [UInt8] {
        var textBytes: [UInt8] = []
        for character in value {
            let characterBytes = Array(String(character).utf8)
            guard textBytes.count + characterBytes.count <= maximumTextByteCount else {
                break
            }
            textBytes.append(contentsOf: characterBytes)
        }

        var packet = [UInt8](repeating: 0, count: byteCount)
        packet[0] = dataType
        packet[1] = UInt8(textBytes.count)
        packet.replaceSubrange(2..<(2 + textBytes.count), with: textBytes)
        return packet
    }
}
