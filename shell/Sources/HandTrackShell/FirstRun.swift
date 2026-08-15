import AVFoundation
import AppKit
import ApplicationServices
import Combine
import ServiceManagement
import SwiftUI

// The guided first-run screen. Camera and Accessibility look alike to the user but
// behave nothing alike to us, and that difference drives this whole file:
//
//   Camera        — a real prompt. Ask, get an answer, done.
//   Accessibility — cannot be granted by a prompt at all. The system dialog only adds
//                   us to the list in System Settings; a human must flip the switch.
//
// Two findings from the ADR-0001 spike shape the design:
//
//   AXIsProcessTrusted() is cached per process, so a grant made while we are running
//   may stay invisible until relaunch. We poll briefly in case the cache does refresh,
//   then stop pretending and offer a restart.
//
//   Accessibility denial is silent — cursor moves are accepted and simply do not
//   happen. So a missing grant is shown as a hard red state and gates Tracking; it is
//   never assumed to be working.

/// Live Camera and Accessibility status, and the single `allGranted` gate the rest of
/// the Shell reads through `AppState.permissionsSatisfied`.
@MainActor
final class PermissionsChecker: ObservableObject {

    enum CameraStatus: Equatable {
        case granted
        case notRequested
        case denied
        /// Blocked by a profile or parental controls; the user cannot fix it from here.
        case restricted
    }

    enum AccessibilityStatus: Equatable {
        case granted
        case notGranted
        /// System Settings opened; watching for the switch to flip.
        case awaitingGrant
        /// Polled without seeing it. Either it is genuinely off, or the per-process
        /// cache is hiding a grant that only a relaunch will reveal.
        case needsRestart
    }

    @Published private(set) var camera: CameraStatus = .notRequested
    @Published private(set) var accessibility: AccessibilityStatus = .notGranted
    @Published private(set) var allGranted = false

    /// False once we know a relaunch cannot be done for the user — running the bare
    /// executable rather than the bundle, which would test the terminal's grants anyway.
    @Published private(set) var canRestart = true

    /// Written to on every status change. Weak, so putting the checker on `AppState`
    /// later cannot create a cycle.
    weak var appState: AppState? {
        didSet { publishGate() }
    }

    /// Roughly how long a person needs to find the row and flip the switch. Past this,
    /// the cache is the likelier explanation and a restart is the honest answer.
    private static let accessibilityPollTicks = 25

    private var accessibilityPoll: Task<Void, Never>?
    private var liveRefresh: Task<Void, Never>?

    nonisolated init() {}

    // MARK: - Reading current status

    /// Re-reads both grants. Cheap enough to call on a timer and on every app activation.
    func refresh() {
        camera = Self.readCamera()

        if AXIsProcessTrusted() {
            accessibilityPoll?.cancel()
            accessibilityPoll = nil
            accessibility = .granted
        } else {
            switch accessibility {
            case .awaitingGrant, .needsRestart:
                // Keep the more specific state; both already mean "not granted".
                break
            case .granted, .notGranted:
                accessibility = .notGranted
            }
        }

        publishGate()
    }

    /// Polls while the screen is up, so a grant made over in System Settings appears
    /// without the user having to come back and click anything.
    func beginLiveRefresh() {
        refresh()
        liveRefresh?.cancel()
        liveRefresh = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                guard !Task.isCancelled, let self else { return }
                self.refresh()
            }
        }
    }

    func endLiveRefresh() {
        liveRefresh?.cancel()
        liveRefresh = nil
    }

    private static func readCamera() -> CameraStatus {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: return .granted
        case .notDetermined: return .notRequested
        case .denied: return .denied
        case .restricted: return .restricted
        @unknown default: return .denied
        }
    }

    private func publishGate() {
        allGranted = camera == .granted && accessibility == .granted
        appState?.permissionsSatisfied = allGranted
    }

    // MARK: - Camera

    /// Prompts if macOS has never asked, otherwise sends the user to the pane — a second
    /// `requestAccess` after a denial returns immediately and shows nothing.
    func handleCameraAction() {
        guard camera == .notRequested else {
            openCameraSettings()
            return
        }
        Task { [weak self] in
            _ = await Self.askForCamera()
            self?.refresh()
        }
    }

    private nonisolated static func askForCamera() async -> Bool {
        await withCheckedContinuation { continuation in
            AVCaptureDevice.requestAccess(for: .video) { continuation.resume(returning: $0) }
        }
    }

    func openCameraSettings() {
        Self.open("x-apple.systempreferences:com.apple.preference.security?Privacy_Camera")
    }

    // MARK: - Accessibility

    /// The prompt grants nothing, but it is what registers us in the Accessibility list,
    /// so the user has a switch to flip instead of hunting for the `+` button. The pane
    /// is opened straight after, in case the dialog gets dismissed.
    func handleAccessibilityAction() {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true]
        if AXIsProcessTrustedWithOptions(options as CFDictionary) {
            settle(accessibility: .granted)
            return
        }

        openAccessibilitySettings()
        beginAccessibilityPoll()
    }

    func openAccessibilitySettings() {
        Self.open("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
    }

    private func beginAccessibilityPoll() {
        accessibilityPoll?.cancel()
        settle(accessibility: .awaitingGrant)

        accessibilityPoll = Task { [weak self] in
            for _ in 0..<Self.accessibilityPollTicks {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                guard !Task.isCancelled, let self else { return }
                if AXIsProcessTrusted() {
                    self.settle(accessibility: .granted)
                    return
                }
            }
            self?.settle(accessibility: .needsRestart)
        }
    }

    private func settle(accessibility newStatus: AccessibilityStatus) {
        accessibility = newStatus
        publishGate()
    }

    // MARK: - Restart

    /// Relaunches the bundle. The only way to clear a cached `AXIsProcessTrusted()`.
    func restart() {
        let bundlePath = Bundle.main.bundlePath
        guard bundlePath.hasSuffix(".app") else {
            canRestart = false
            return
        }

        let relaunch = Process()
        relaunch.executableURL = URL(fileURLWithPath: "/bin/sh")
        // The path travels as an argument, never spliced into the script, so a space or
        // a quote in it cannot turn into shell syntax. The sleep lets us finish quitting
        // first, so macOS launches a fresh instance instead of waking the dying one.
        relaunch.arguments = [
            "-c", #"sleep 1; exec "$0" -n "$1""#,
            "/usr/bin/open", bundlePath,
        ]

        do {
            try relaunch.run()
        } catch {
            canRestart = false
            return
        }

        NSApp.terminate(nil)
    }

    /// Clears a stale Accessibility entry, which reads as switched on while granting
    /// nothing. Shown to the user rather than run for them — it needs their password.
    var accessibilityResetCommand: String {
        let identifier = Bundle.main.bundleIdentifier ?? "com.brohsha.handTrack"
        return "tccutil reset Accessibility \(identifier)"
    }

    private static func open(_ urlString: String) {
        guard let url = URL(string: urlString) else { return }
        NSWorkspace.shared.open(url)
    }
}

/// Launch at login, via the modern login-item API. Reports failure instead of swallowing
/// it, so the screen never shows a tick for something that did not take.
enum FirstRunLoginItem {

    static var isEnabled: Bool {
        SMAppService.mainApp.status == .enabled
    }

    @discardableResult
    static func set(_ enabled: Bool) -> Bool {
        do {
            if enabled {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
            return true
        } catch {
            return false
        }
    }
}

// MARK: - Screen

@MainActor
struct FirstRunView: View {

    @EnvironmentObject private var appState: AppState

    /// Injected rather than created here. When this view owned its own checker there
    /// were two of them: this one saw the grants land while the app delegate's copy,
    /// refreshed once at launch, went on believing there were none.
    @ObservedObject private var checker: PermissionsChecker

    @State private var wantsLaunchAtLogin = true
    @State private var loginItemFailed = false
    @State private var showingAccessibilityHelp = false
    @State private var copiedResetCommand = false

    private let onContinue: () -> Void

    init(checker: PermissionsChecker, onContinue: @escaping () -> Void = {}) {
        self.checker = checker
        self.onContinue = onContinue
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            header
            cameraRow
            accessibilitySection
            Divider()
            launchAtLoginRow
            footer
        }
        .padding(22)
        .frame(width: 480)
        .onAppear {
            checker.appState = appState
            if FirstRunLoginItem.isEnabled { wantsLaunchAtLogin = true }
            checker.beginLiveRefresh()
        }
        .onDisappear { checker.endLiveRefresh() }
        .onReceive(NotificationCenter.default.publisher(
            for: NSApplication.didBecomeActiveNotification)
        ) { _ in
            checker.refresh()
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("handTrack needs two permissions")
                .font(.title2.weight(.semibold))
            Text("Both have to be green. Tracking stays off until they are.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: Camera

    private var cameraRow: some View {
        PermissionRow(
            title: "Camera",
            detail: "Reads your hand from the webcam.",
            statusText: checker.camera.summary,
            tone: checker.camera.isGranted ? .good : .bad,
            actionTitle: checker.camera.actionTitle,
            actionEnabled: checker.camera != .granted && checker.camera != .restricted,
            action: { checker.handleCameraAction() }
        )
    }

    // MARK: Accessibility

    private var accessibilitySection: some View {
        VStack(alignment: .leading, spacing: 10) {
            PermissionRow(
                title: "Accessibility",
                detail: "Moves the cursor. macOS will not grant this from a prompt — "
                    + "you have to switch handTrack on yourself.",
                statusText: checker.accessibility.summary,
                tone: accessibilityTone,
                actionTitle: checker.accessibility.actionTitle,
                actionEnabled: !checker.accessibility.isGranted,
                action: { checker.handleAccessibilityAction() },
                busy: checker.accessibility == .awaitingGrant
            )

            if checker.accessibility == .needsRestart {
                restartAdvice
            }

            if checker.accessibility != .granted {
                accessibilityHelp
            }
        }
    }

    private var accessibilityTone: PermissionRow.Tone {
        switch checker.accessibility {
        case .granted: return .good
        case .awaitingGrant, .needsRestart: return .pending
        case .notGranted: return .bad
        }
    }

    private var restartAdvice: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "arrow.clockwise.circle.fill")
                .foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 6) {
                Text("handTrack only checks Accessibility once per launch, so a grant you "
                    + "just made stays invisible until it restarts.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                if checker.canRestart {
                    Button("Restart handTrack") { checker.restart() }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.small)
                } else {
                    Text("Quit handTrack and open it again.")
                        .font(.caption.weight(.semibold))
                }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.orange.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
    }

    private var accessibilityHelp: some View {
        DisclosureGroup("Switch is already on but handTrack still says no?",
                        isExpanded: $showingAccessibilityHelp) {
            VStack(alignment: .leading, spacing: 8) {
                Text("An old entry can read as on while granting nothing. Run this in "
                    + "Terminal to clear it, then reopen handTrack and grant it again.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                Text(checker.accessibilityResetCommand)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.secondary.opacity(0.10),
                                in: RoundedRectangle(cornerRadius: 6))

                Button(copiedResetCommand ? "Copied" : "Copy command") { copyResetCommand() }
                    .controlSize(.small)
            }
            .padding(.top, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .font(.caption)
    }

    private func copyResetCommand() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(checker.accessibilityResetCommand, forType: .string)
        copiedResetCommand = true
        Task {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            copiedResetCommand = false
        }
    }

    // MARK: Launch at login

    private var launchAtLoginRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            Toggle("Launch handTrack at login", isOn: $wantsLaunchAtLogin)
                .onChange(of: wantsLaunchAtLogin) { _, _ in loginItemFailed = false }

            Text(loginItemFailed
                ? "Could not change this. Set it under System Settings › General › Login Items."
                : "On by default, and only applied when you start handTrack. "
                    + "Turn it off here if you would rather open it yourself.")
                .font(.caption)
                .foregroundStyle(loginItemFailed ? Color.red : Color.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: Footer

    private var footer: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(checker.allGranted
                ? "Ready. handTrack can start tracking."
                : "Grant both to continue.")
                .font(.caption)
                .foregroundStyle(checker.allGranted ? Color.green : Color.secondary)

            Spacer(minLength: 8)

            Button("Start handTrack") {
                applyLaunchAtLogin()
                // A login-item failure is worth showing but not worth blocking on; the
                // permissions are the only thing Tracking actually depends on.
                onContinue()
            }
            .keyboardShortcut(.defaultAction)
            .buttonStyle(.borderedProminent)
            .disabled(!checker.allGranted)
            .fixedSize()
        }
    }

    /// Applied here rather than on toggle, so the pre-ticked box only takes effect once
    /// the user has seen it and moved on deliberately.
    private func applyLaunchAtLogin() {
        if wantsLaunchAtLogin != FirstRunLoginItem.isEnabled {
            loginItemFailed = !FirstRunLoginItem.set(wantsLaunchAtLogin)
        }
        // Report what actually happened, never what was asked for.
        let actual = FirstRunLoginItem.isEnabled
        wantsLaunchAtLogin = actual
        appState.launchAtLogin = actual
    }
}

/// One permission, stated plainly enough that a missing grant cannot be mistaken for a
/// working one.
@MainActor
private struct PermissionRow: View {

    enum Tone {
        case good, bad, pending

        var color: Color {
            switch self {
            case .good: return .green
            case .bad: return .red
            case .pending: return .orange
            }
        }

        var symbol: String {
            switch self {
            case .good: return "checkmark.circle.fill"
            case .bad: return "xmark.circle.fill"
            case .pending: return "clock.fill"
            }
        }
    }

    let title: String
    let detail: String
    let statusText: String
    let tone: Tone
    let actionTitle: String
    let actionEnabled: Bool
    let action: () -> Void
    var busy = false

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: tone.symbol)
                .font(.title3)
                .foregroundStyle(tone.color)
                .padding(.top, 1)

            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.headline)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 6) {
                    if busy {
                        ProgressView().controlSize(.small)
                    }
                    Text(statusText)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(tone.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            Spacer(minLength: 8)

            Button(actionTitle) { action() }
                .controlSize(.small)
                .disabled(!actionEnabled)
                .fixedSize()
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tone.color.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(tone.color.opacity(0.25), lineWidth: 1)
        )
    }
}
