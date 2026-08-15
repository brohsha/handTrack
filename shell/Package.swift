// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "HandTrackShell",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "HandTrackShell", path: "Sources/HandTrackShell")
    ]
)
