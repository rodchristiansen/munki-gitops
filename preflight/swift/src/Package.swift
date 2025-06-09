// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Preflight",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "preflight", targets: ["Preflight"])
    ],
    targets: [
        .executableTarget(
            name: "Preflight",
            path: "Sources/Preflight"
        )
    ]
)
