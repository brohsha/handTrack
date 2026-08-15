import Foundation
import SwiftUI

/// The single source of truth every part of the Shell reads from and nothing but
/// `EngineProcess` writes to. Menu bar, Panel and Countdown overlay all observe this;
/// none of them talk to each other.
///
/// Deliberately mirrors what the Engine reports rather than tracking intent, so the
/// interface can never claim Tracking is on while the camera is shut.
@MainActor
final class AppState: ObservableObject {

    enum Status: Equatable {
        /// Hard Off — camera released, light out.
        case off
        /// `start` sent, camera warming up (~1s).
        case starting
        /// Tracking.
        case on
        /// Disable Fist held past the Dwell; Countdown running.
        case countingDown(secondsLeft: Int)
        /// Engine failed twice, or a permission is missing.
        case failed(reason: String)
    }

    @Published private(set) var status: Status = .off
    @Published private(set) var cameraOpen = false

    /// The last non-fatal complaint from the Engine, or nil. This is the only channel a
    /// silent failure has: Accessibility denial does not raise, so the Engine noticing
    /// that a cursor move went nowhere arrives here or nowhere at all.
    @Published private(set) var lastEngineError: String?

    /// False until both Camera and Accessibility are granted. Gates the first-run screen.
    @Published var permissionsSatisfied = false

    /// User's Toggle Shortcut. Defaults to ⌃⌥⌘H, persisted in UserDefaults.
    @Published var shortcut: Shortcut = .default

    @Published var launchAtLogin = false

    var isTracking: Bool {
        switch status {
        case .on, .countingDown: return true
        case .off, .starting, .failed: return false
        }
    }

    func apply(status newStatus: Status) { status = newStatus }
    func apply(cameraOpen open: Bool) { cameraOpen = open }
    func apply(engineError message: String?) { lastEngineError = message }
}

/// A recorded key combination, stored as a Carbon key code plus modifier flags so it
/// can be handed straight to `RegisterEventHotKey`.
/// `Shortcut.default` and `displayString` live in Hotkey.swift, next to the Carbon
/// constants they are built from.
struct Shortcut: Equatable, Codable {
    var keyCode: UInt32
    var modifiers: UInt32
}
