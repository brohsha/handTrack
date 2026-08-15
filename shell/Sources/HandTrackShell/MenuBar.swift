import AppKit
import Combine
import SwiftUI

/// The app's only permanent presence: a constant logo with an Indicator beside it, and
/// the Panel hanging off a click. Reads `AppState` and never writes to it.
@MainActor
final class MenuBarController: NSObject {

    private let appState: AppState

    /// Handed in so the menu bar owns the same Engine the Panel drives, rather than a
    /// second one. Nothing here commands it — every control lives in the Panel.
    let engine: EngineProcess

    /// Passed through to the Panel, which shows both grants and flags revocation.
    private let permissions: PermissionsChecker

    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let popover = NSPopover()

    private var outsideClickMonitor: Any?
    private var cancellables = Set<AnyCancellable>()

    init(appState: AppState, engine: EngineProcess, permissions: PermissionsChecker) {
        self.appState = appState
        self.engine = engine
        self.permissions = permissions
        super.init()

        configureButton()
        configurePopover()
        observeStatus()
    }

    // MARK: - Status item

    private func configureButton() {
        guard let button = statusItem.button else { return }

        button.image = Logo.image()
        button.imagePosition = .imageLeading
        button.target = self
        button.action = #selector(statusItemClicked)

        render(appState.status)
    }

    @objc private func statusItemClicked() {
        popover.isShown ? closePopover() : showPopover()
    }

    // MARK: - Indicator

    private func observeStatus() {
        // `@Published` fires in `willSet`, so `appState.status` is still the previous
        // value in here — render the value the publisher hands us, not the property.
        appState.$status
            .sink { [weak self] status in
                MainActor.assumeIsolated { self?.render(status) }
            }
            .store(in: &cancellables)
    }

    private func render(_ status: AppState.Status) {
        guard let button = statusItem.button else { return }

        let indicator = Indicator.matching(status)
        button.attributedTitle = NSAttributedString(
            string: Indicator.gap + indicator.glyph,
            attributes: [
                .font: Indicator.font,
                .foregroundColor: indicator.tint,
            ]
        )
        button.toolTip = indicator.spoken
        button.setAccessibilityLabel("handTrack — \(indicator.spoken)")
    }

    /// Reports what the status item actually became. A zero length or a missing button
    /// means the item exists but draws nothing, which looks identical to the app simply
    /// not having started.
    var diagnosticSummary: String {
        let button = statusItem.button
        let onScreen = button?.window?.frame
        let screen = NSScreen.main?.frame
        // On a notched display the menu bar is split in two; a status item that lands
        // between these two areas is drawn behind the notch and simply cannot be seen.
        let leftArea = NSScreen.main?.auxiliaryTopLeftArea
        let rightArea = NSScreen.main?.auxiliaryTopRightArea
        return "statusItem visible=\(statusItem.isVisible) length=\(statusItem.length) "
            + "button=\(button != nil) image=\(button?.image != nil) "
            + "title=\"\(button?.attributedTitle.string ?? "nil")\" "
            + "onScreen=\(onScreen.map { NSStringFromRect($0) } ?? "nil") "
            + "screen=\(screen.map { NSStringFromRect($0) } ?? "nil") "
            + "leftArea=\(leftArea.map { NSStringFromRect($0) } ?? "none") "
            + "rightArea=\(rightArea.map { NSStringFromRect($0) } ?? "none") "
            + "logo=\(Logo.image() != nil)"
    }

    // MARK: - Panel

    /// Opens the Panel from outside the menu bar. With no Dock icon, relaunching the app
    /// is the only way back in when the status item is hidden, so this is that route.
    func showPanel() {
        guard !popover.isShown else { return }
        showPopover()
    }

    private func configurePopover() {
        let hosting = NSHostingController(
            rootView: PanelView(engine: engine, permissions: permissions)
                .environmentObject(appState)
        )
        // Without this the popover keeps NSPopover's default 320x320 content size and
        // positions itself against that, while SwiftUI lays the Panel out at its own
        // smaller height — leaving the visible card floating well below the menu bar.
        // Letting the hosting controller publish its real fitting size anchors it.
        hosting.sizingOptions = [.preferredContentSize]
        popover.contentViewController = hosting
        // Not `.transient`: AppKit dismisses a transient popover on the mouse-down that
        // also hits the status button, so the button's own action then sees it closed and
        // immediately reopens it — the Panel could never be shut by clicking the icon.
        popover.behavior = .applicationDefined
    }

    private func showPopover() {
        guard let button = statusItem.button else { return }

        // A menu-bar-only app has no window to bring forward, and without activating,
        // fields inside the Panel would never take the keyboard.
        NSApp.activate()
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)

        // We opted out of automatic dismissal above, so outside clicks are ours to catch.
        outsideClickMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown]
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.closePopover() }
        }
    }

    private func closePopover() {
        popover.performClose(nil)

        if let monitor = outsideClickMonitor {
            NSEvent.removeMonitor(monitor)
            outsideClickMonitor = nil
        }
    }
}

// MARK: -

/// The constant half of the menu bar presence. It never reacts to state, so the app looks
/// the same whatever it is doing and stays findable at a glance.
private enum Logo {

    /// PLACEHOLDER — the real logo is still being designed. Swapping it is this one line
    /// plus, for a bundled asset, `NSImage(named:)` in place of the symbol lookup.
    static let placeholderSymbolName = "hand.raised"

    static func image() -> NSImage? {
        let image = NSImage(
            systemSymbolName: placeholderSymbolName,
            accessibilityDescription: "handTrack"
        )
        // Template rendering lets macOS invert the logo for light and dark menu bars
        // rather than us shipping and choosing between two assets.
        image?.isTemplate = true
        return image
    }
}

/// The moving half. Every state gets its own shape; colour only reinforces it, because
/// colour alone is invisible to colour-blind users and unreliable against a menu bar that
/// changes brightness under them.
private struct Indicator {

    let glyph: String
    let tint: NSColor
    /// Spoken to VoiceOver and shown as the tooltip — the only place the shape is named.
    let spoken: String

    /// Monospaced digits so the status item does not twitch narrower and wider as the
    /// Countdown ticks 4, 3, 2, 1.
    static let font = NSFont.monospacedDigitSystemFont(
        ofSize: NSFont.menuBarFont(ofSize: 0).pointSize,
        weight: .medium
    )

    /// Thin space, to keep the Indicator from crowding the logo.
    static let gap = "\u{2009}"

    static func matching(_ status: AppState.Status) -> Indicator {
        switch status {
        case .off:
            // Hard Off is deliberately the one uncoloured state: nothing is happening,
            // and a colour here would compete with the states that matter.
            return Indicator(glyph: "○", tint: .secondaryLabelColor, spoken: "Off")

        case .starting:
            // The same hollow circle as Off, only fainter. Starting is Off already in
            // motion, not a state of its own — and it is gone in about a second.
            return Indicator(glyph: "○", tint: .tertiaryLabelColor, spoken: "Starting…")

        case .on:
            return Indicator(glyph: "●", tint: .systemGreen, spoken: "Tracking")

        case .countingDown(let secondsLeft):
            return Indicator(
                glyph: "\(secondsLeft)",
                tint: .systemOrange,
                spoken: "Turning off in \(secondsLeft)"
            )

        case .failed(let reason):
            // The menu bar is where a failure is noticed, and the tooltip is the only
            // room there is to say what went wrong before the user opens the Panel.
            return Indicator(glyph: "!", tint: .systemRed, spoken: "Failed — \(reason)")
        }
    }
}
