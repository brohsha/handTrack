import AppKit
import SwiftUI

/// Everything the user can see or change, shown in the Panel the menu bar icon opens.
///
/// Reads state only from `AppState` and acts only through `EngineProcess`, so the Panel
/// can never claim something the Engine has not actually reported.
struct PanelView: View {

    @EnvironmentObject private var state: AppState

    let engine: EngineProcess

    /// Shared with the first-run screen and the app delegate. Both grants can be revoked
    /// in System Settings at any time, and Accessibility revocation is silent, so the
    /// Panel watches them for as long as it is open rather than trusting a launch-time
    /// answer.
    @ObservedObject var permissions: PermissionsChecker

    @State private var tab: Tab = .control

    /// A segmented control rather than a `TabView`: at 320pt the macOS tab bar's chrome
    /// makes a popover read as a settings window.
    private enum Tab: Hashable {
        case control
        case settings
    }

    var body: some View {
        VStack(spacing: 12) {
            Picker("", selection: $tab) {
                Text("Control").tag(Tab.control)
                Text("Settings").tag(Tab.settings)
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            Group {
                switch tab {
                case .control: controlTab
                case .settings: settingsTab
                }
            }
            // Both tabs sit at roughly this height, so switching between them does not
            // make the popover jump.
            .frame(maxWidth: .infinity, minHeight: 104, alignment: .top)
        }
        .padding(14)
        .frame(width: 320)
    }

    // MARK: - Control

    private var controlTab: some View {
        VStack(alignment: .leading, spacing: 12) {
            Toggle(isOn: power) {
                Text("Tracking").font(.headline)
            }
            .toggleStyle(.switch)
            .controlSize(.large)
            // Disabled rather than silently ignored, so a missing grant reads as "not
            // available yet" instead of a switch that flips back for no visible reason.
            .disabled(!state.permissionsSatisfied && !state.isTracking)

            if !state.permissionsSatisfied {
                Text("Grant Camera and Accessibility in Settings to start.")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }

            Divider()

            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Circle()
                        .fill(statusColour)
                        .frame(width: 8, height: 8)
                    Text(statusText)
                }

                if let problem {
                    Text(problem)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }

                // Hard Off promises the camera light is genuinely out, so the Panel
                // reports the camera separately rather than implying it from Tracking.
                HStack(spacing: 6) {
                    Image(systemName: state.cameraOpen ? "video.fill" : "video.slash.fill")
                        // Green echoes the hardware light the user is being asked to trust.
                        .foregroundStyle(state.cameraOpen ? Color.green : Color.secondary)
                    Text(state.cameraOpen ? "Camera on" : "Camera off")
                        .foregroundStyle(.secondary)
                }
                .font(.subheadline)
            }
        }
    }

    /// Expresses a target state rather than flipping whatever was last reported, so a
    /// Panel that has drifted out of sync corrects itself instead of inverting the user.
    ///
    /// `.starting` counts as on: the switch must stay where the user put it during the
    /// ~1s camera warm-up instead of snapping back.
    private var power: Binding<Bool> {
        Binding(
            get: { state.isTracking || state.status == .starting },
            set: { wantsTracking in
                // Starting without both grants gets you a lit camera and a cursor that
                // never moves, because Accessibility denial is silent. Stopping is always
                // allowed: refusing to switch something off is never the safer choice.
                guard wantsTracking else { return engine.stop() }
                guard state.permissionsSatisfied else { return }
                engine.start()
            }
        )
    }

    private var statusText: String {
        switch state.status {
        case .off: return "Off"
        case .starting: return "Starting…"
        case .on: return "On"
        case .countingDown(let secondsLeft): return "Disabling in \(secondsLeft)…"
        case .failed: return "Problem"
        }
    }

    private var statusColour: Color {
        switch state.status {
        case .off: return Color(nsColor: .tertiaryLabelColor)
        case .starting, .countingDown: return .orange
        case .on: return .green
        case .failed: return .red
        }
    }

    private var problem: String? {
        if case .failed(let reason) = state.status { return reason }
        // Non-fatal complaints matter just as much here: they are how "tracking is on
        // but the cursor is not moving" reaches the user instead of dying in a log.
        return state.lastEngineError
    }

    // MARK: - Settings

    private var settingsTab: some View {
        VStack(alignment: .leading, spacing: 12) {
            permissionsSection

            Divider()

            LabeledContent("Toggle Shortcut") {
                // Re-registration is handled by the app delegate observing state.shortcut,
                // so the recorder only has to write the new binding here.
                ShortcutRecorderView(shortcut: $state.shortcut)
            }

            Toggle("Launch at login", isOn: $state.launchAtLogin)
                .toggleStyle(.switch)

            Divider()

            // The app is menu-bar-only (LSUIElement), so there is no Dock icon to quit
            // from and no ⌘Q whilst unfocused. This button is the only way out, and must
            // stay one click from the icon rather than buried behind a disclosure.
            HStack {
                Spacer()
                Button("Quit handTrack") { NSApp.terminate(nil) }
            }
        }
        // Revocation happens over in System Settings, with nothing sent back to us, so
        // the only way to notice is to keep looking while the Panel is on screen.
        .onAppear { permissions.beginLiveRefresh() }
        .onDisappear { permissions.endLiveRefresh() }
    }

    // MARK: - Permissions

    private var permissionsSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Permissions")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)

            permissionRow(
                title: "Camera",
                granted: permissions.camera == .granted,
                detail: cameraDetail,
                action: { permissions.openCameraSettings() }
            )

            permissionRow(
                title: "Accessibility",
                granted: permissions.accessibility == .granted,
                detail: accessibilityDetail,
                action: { permissions.openAccessibilitySettings() }
            )
        }
    }

    private func permissionRow(title: String, granted: Bool, detail: String,
                               action: @escaping () -> Void) -> some View {
        HStack(spacing: 8) {
            // Shape first, colour second — a red dot alone is invisible to a
            // colour-blind user, and this row is the difference between "working" and
            // "silently doing nothing".
            Image(systemName: granted ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(granted ? Color.green : Color.red)

            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.callout)
                Text(detail)
                    .font(.caption2)
                    .foregroundStyle(granted ? .secondary : Color.red)
            }

            Spacer(minLength: 4)

            if !granted {
                Button("Fix…", action: action).controlSize(.small)
            }
        }
    }

    private var cameraDetail: String {
        switch permissions.camera {
        case .granted: return "Granted"
        case .notRequested: return "Not yet requested"
        case .denied: return "Denied — hand tracking cannot see anything"
        case .restricted: return "Blocked by a system policy"
        }
    }

    private var accessibilityDetail: String {
        switch permissions.accessibility {
        case .granted: return "Granted"
        case .notGranted: return "Missing — the cursor will not move"
        case .awaitingGrant: return "Waiting for the switch in System Settings…"
        // Worth naming: the grant may already be on, and only a relaunch can see it.
        case .needsRestart: return "Granted? Relaunch handTrack to pick it up"
        }
    }
}
