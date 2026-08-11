import CodexMacropadCore
import CoreFoundation
import Foundation
import IOKit.hid

private let vendorID = 0xE126
private let productID = 0x0042
private let rawHIDUsagePage = 0xFF60
private let rawHIDUsage = 0x61
private let artistDataType: UInt8 = 0xAD
private let titleDataType: UInt8 = 0xAE

enum MacropadDeviceError: LocalizedError {
    case deviceNotFound
    case managerOpenFailed(IOReturn)
    case deviceOpenFailed(IOReturn)
    case writeFailed(IOReturn)
    case exchangeTimedOut
    case invalidInputReport(Int)
    case runLoopUnavailable

    var errorDescription: String? {
        switch self {
        case .deviceNotFound:
            return "M4CR0Pad v3 Raw HID interface was not found"
        case .managerOpenFailed(let code):
            return "Unable to open HID manager (IOReturn \(code))"
        case .deviceOpenFailed(let code):
            return "Unable to open M4CR0Pad v3 (IOReturn \(code))"
        case .writeFailed(let code):
            return "Unable to write Raw HID report (IOReturn \(code))"
        case .exchangeTimedOut:
            return "M4CR0Pad did not answer the VialRGB command"
        case .invalidInputReport(let count):
            return "M4CR0Pad returned an invalid \(count)-byte input report"
        case .runLoopUnavailable:
            return "Unable to create a run loop for the M4CR0Pad response"
        }
    }
}

private func hidInputReportCallback(
    context: UnsafeMutableRawPointer?,
    result: IOReturn,
    sender: UnsafeMutableRawPointer?,
    type: IOHIDReportType,
    reportID: UInt32,
    report: UnsafeMutablePointer<UInt8>,
    reportLength: CFIndex
) {
    guard result == kIOReturnSuccess, let context, reportLength > 0 else {
        return
    }
    let channel = Unmanaged<HIDResponseChannel>.fromOpaque(context)
        .takeUnretainedValue()
    channel.receive(
        Array(UnsafeBufferPointer(start: report, count: Int(reportLength)))
    )
}

final class MacropadDevice: MacropadIO {
    private let manager: IOHIDManager
    private let device: IOHIDDevice
    private let responseChannel = HIDResponseChannel(
        bufferCount: VialRGBCodec.reportByteCount + 1
    )
    let identity: RGBDeviceIdentity

    init() throws {
        manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
        let matching: [String: Any] = [
            kIOHIDVendorIDKey: vendorID,
            kIOHIDProductIDKey: productID,
            kIOHIDPrimaryUsagePageKey: rawHIDUsagePage,
            kIOHIDPrimaryUsageKey: rawHIDUsage,
        ]
        IOHIDManagerSetDeviceMatching(manager, matching as CFDictionary)

        let managerResult = IOHIDManagerOpen(manager, IOOptionBits(kIOHIDOptionsTypeNone))
        guard managerResult == kIOReturnSuccess else {
            throw MacropadDeviceError.managerOpenFailed(managerResult)
        }
        guard
            let devices = IOHIDManagerCopyDevices(manager) as? Set<IOHIDDevice>,
            let matchingDevice = devices.first
        else {
            IOHIDManagerClose(manager, IOOptionBits(kIOHIDOptionsTypeNone))
            throw MacropadDeviceError.deviceNotFound
        }
        device = matchingDevice

        let deviceResult = IOHIDDeviceOpen(device, IOOptionBits(kIOHIDOptionsTypeNone))
        guard deviceResult == kIOReturnSuccess else {
            IOHIDManagerClose(manager, IOOptionBits(kIOHIDOptionsTypeNone))
            throw MacropadDeviceError.deviceOpenFailed(deviceResult)
        }
        IOHIDDeviceRegisterInputReportCallback(
            device,
            responseChannel.buffer,
            responseChannel.bufferCount,
            hidInputReportCallback,
            Unmanaged.passUnretained(responseChannel).toOpaque()
        )

        let serial = IOHIDDeviceGetProperty(device, kIOHIDSerialNumberKey as CFString)
            as? String ?? "e126:0042"
        identity = RGBDeviceIdentity(
            vendorID: vendorID,
            productID: productID,
            serial: serial
        )
    }

    deinit {
        IOHIDDeviceRegisterInputReportCallback(
            device,
            responseChannel.buffer,
            responseChannel.bufferCount,
            nil,
            nil
        )
        IOHIDDeviceClose(device, IOOptionBits(kIOHIDOptionsTypeNone))
        IOHIDManagerClose(manager, IOOptionBits(kIOHIDOptionsTypeNone))
    }

    func write(_ packet: [UInt8]) throws {
        let result = packet.withUnsafeBytes { bytes -> IOReturn in
            guard let baseAddress = bytes.bindMemory(to: UInt8.self).baseAddress else {
                return kIOReturnBadArgument
            }
            return IOHIDDeviceSetReport(
                device,
                kIOHIDReportTypeOutput,
                0,
                baseAddress,
                packet.count
            )
        }
        guard result == kIOReturnSuccess else {
            throw MacropadDeviceError.writeFailed(result)
        }
    }

    func exchange(_ request: [UInt8], timeout: TimeInterval = 0.5) throws -> [UInt8] {
        guard request.count == VialRGBCodec.reportByteCount else {
            throw MacropadDeviceError.invalidInputReport(request.count)
        }
        responseChannel.begin(expectedEcho: Array(request.prefix(2)))
        guard
            let runLoop = CFRunLoopGetCurrent(),
            let runLoopMode = CFRunLoopMode.defaultMode
        else {
            throw MacropadDeviceError.runLoopUnavailable
        }
        IOHIDDeviceScheduleWithRunLoop(device, runLoop, runLoopMode.rawValue)
        defer {
            IOHIDDeviceUnscheduleFromRunLoop(device, runLoop, runLoopMode.rawValue)
        }

        try write(request)
        let deadline = Date().addingTimeInterval(timeout)
        while responseChannel.response == nil, Date() < deadline {
            let remaining = max(0.001, deadline.timeIntervalSinceNow)
            CFRunLoopRunInMode(runLoopMode, min(remaining, 0.05), true)
        }
        guard let response = responseChannel.response else {
            throw MacropadDeviceError.exchangeTimedOut
        }
        return response
    }

    func writeDisplay(_ snapshot: DisplaySnapshot) throws {
        try write(RawHIDPacket.text(dataType: artistDataType, value: snapshot.artist))
        Thread.sleep(forTimeInterval: 0.02)
        try write(RawHIDPacket.text(dataType: titleDataType, value: snapshot.title))
    }

    func clearDisplay() throws {
        try write(RawHIDPacket.text(dataType: artistDataType, value: ""))
        Thread.sleep(forTimeInterval: 0.02)
        try write(RawHIDPacket.text(dataType: titleDataType, value: ""))
    }

    func readRGBInfo() throws -> VialRGBInfo {
        try VialRGBCodec.parseInfoResponse(exchange(VialRGBCodec.getInfoRequest()))
    }

    func readSupportedEffects() throws -> Set<UInt16> {
        var effects: Set<UInt16> = [0]
        var cursor: UInt16 = 0
        while cursor < 0xFFFF {
            let response = try exchange(VialRGBCodec.getSupportedRequest(after: cursor))
            let batch = try VialRGBCodec.parseSupportedResponse(response)
            effects.formUnion(batch)
            let hasSentinel = stride(from: 2, to: response.count - 1, by: 2)
                .contains { response[$0] == 0xFF && response[$0 + 1] == 0xFF }
            let nextCursor = batch.max() ?? cursor
            if hasSentinel || nextCursor <= cursor {
                break
            }
            cursor = nextCursor
        }
        return effects
    }

    func readRGBMode() throws -> VialRGBMode {
        try VialRGBCodec.parseModeResponse(exchange(VialRGBCodec.getModeRequest()))
    }

    func setRGBMode(_ mode: VialRGBMode) throws {
        let response = try exchange(VialRGBCodec.setModeRequest(mode))
        try VialRGBCodec.validateSetModeResponse(response)
    }
}
