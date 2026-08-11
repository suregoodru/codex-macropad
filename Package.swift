// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "CodexMacropad",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "CodexMacropadCore", targets: ["CodexMacropadCore"]),
        .executable(name: "MacropadDisplay", targets: ["MacropadDisplay"]),
    ],
    targets: [
        .target(name: "CodexMacropadCore"),
        .executableTarget(
            name: "MacropadDisplay",
            dependencies: ["CodexMacropadCore"]
        ),
        .testTarget(
            name: "CodexMacropadCoreTests",
            dependencies: ["CodexMacropadCore"]
        ),
    ]
)
