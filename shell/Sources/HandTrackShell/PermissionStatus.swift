import Foundation

// How each permission state is described to the user.
//
// Both the first-run screen and the Panel's Settings tab show the same two grants, in
// different shapes: a large explanatory card during setup, a compact row afterwards.
// The shapes can differ, but the words must not — when each view carried its own copy
// they had already drifted, so the same denied camera was described two different ways
// depending on where you happened to be looking.

extension PermissionsChecker.CameraStatus {

    var isGranted: Bool { self == .granted }

    var summary: String {
        switch self {
        case .granted: return "Granted"
        case .notRequested: return "Not requested yet"
        case .denied: return "Denied — switch handTrack on under Privacy & Security › Camera"
        case .restricted: return "Blocked by a system policy"
        }
    }

    /// macOS only ever prompts once. After a denial the switch has to be found by hand,
    /// so offering "Allow…" a second time would be a button that cannot work.
    var actionTitle: String {
        self == .notRequested ? "Allow…" : "Open System Settings"
    }
}

extension PermissionsChecker.AccessibilityStatus {

    var isGranted: Bool { self == .granted }

    var summary: String {
        switch self {
        case .granted: return "Granted"
        case .notGranted: return "Missing — the cursor will not move"
        case .awaitingGrant: return "Waiting for the switch in System Settings…"
        // Named rather than described as simply missing: the grant may well be on
        // already, and only a relaunch will let this process see it.
        case .needsRestart: return "Granted? Relaunch handTrack to pick it up"
        }
    }

    var actionTitle: String { "Open System Settings" }
}
