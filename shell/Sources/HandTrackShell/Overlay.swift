import AppKit
import Combine

/// The Countdown display — bare numerals floating beside the cursor while a Disable
/// Fist is held. Visible for `.countingDown`, hidden for every other status.
///
/// This is the only Shell surface that reads the cursor position. The protocol keeps
/// coordinates out of Engine messages entirely, so the Engine says *how long is left*
/// and the Shell decides *where that goes*.
@MainActor
final class CountdownOverlay {

    private enum Metrics {
        static let fontSize: CGFloat = 96
        /// Transparent margin inside the window. The stroke and the shadow both bleed
        /// outside the glyph's typographic bounds and would otherwise be clipped.
        static let padding: CGFloat = 22
        /// Cursor hot spot to nearest window edge. The numeral itself starts a further
        /// `padding` in, which clears the arrow glyph without drifting far from it.
        static let cursorGap: CGFloat = 8
    }

    private var statusObserver: AnyCancellable?
    private var panel: OverlayPanel?

    init(state: AppState) {
        statusObserver = state.$status
            .removeDuplicates()
            .sink { [weak self] status in
                // `@Published` fires synchronously on the mutating thread, and the only
                // writer is MainActor-isolated `AppState`, so this is always main.
                MainActor.assumeIsolated { self?.apply(status) }
            }
    }

    private func apply(_ status: AppState.Status) {
        if case .countingDown(let secondsLeft) = status {
            show(secondsLeft: secondsLeft)
        } else {
            hide()
        }
    }

    // MARK: - Display

    func show(secondsLeft: Int) {
        // The Countdown reads 4…1. `seconds_left: 0` means the Engine has already put
        // itself into Hard Off, so there is nothing left to count.
        guard secondsLeft > 0 else {
            hide()
            return
        }

        let numeral = Self.numeral(secondsLeft)
        let glyph = numeral.size()
        let size = NSSize(width: ceil(glyph.width) + Metrics.padding * 2,
                          height: ceil(glyph.height) + Metrics.padding * 2)

        let window = panel ?? makePanel()
        panel = window

        (window.contentView as? NumeralView)?.numeral = numeral
        window.setFrame(NSRect(origin: origin(for: size), size: size), display: false)
        // Not `makeKeyAndOrderFront` — that would activate the Shell and pull focus out
        // of whatever the user was doing.
        window.orderFrontRegardless()
    }

    func hide() {
        panel?.orderOut(nil)
    }

    // MARK: - Placement

    /// Southeast of the cursor, flipping like a tooltip when that would run off the
    /// display the cursor is on.
    ///
    /// Anchoring to the cursor is stable precisely because Gesture Lockout freezes it
    /// for the whole Dwell and Countdown — the numeral cannot drift once it is up.
    private func origin(for size: NSSize) -> NSPoint {
        let cursor = NSEvent.mouseLocation
        let southEast = NSPoint(x: cursor.x + Metrics.cursorGap,
                                y: cursor.y - Metrics.cursorGap - size.height)

        // The screen the cursor is on, not the main one — the hand may well be driving
        // the cursor around a second display.
        guard let limits = (NSScreen.screens.first { $0.frame.contains(cursor) }
                            ?? NSScreen.main
                            ?? NSScreen.screens.first)?.visibleFrame else {
            return southEast
        }

        // Flip rather than clamp: clamping a numeral back inside the screen would slide
        // it over the cursor it is meant to sit beside. A single digit is far narrower
        // and shorter than any display, so whenever one side overflows the other fits.
        var x = southEast.x
        if x + size.width > limits.maxX {
            x = cursor.x - Metrics.cursorGap - size.width  // west
        }

        var y = southEast.y
        if y < limits.minY {
            y = cursor.y + Metrics.cursorGap  // north
        }

        return NSPoint(x: x, y: y)
    }

    // MARK: - Drawing

    private static func numeral(_ value: Int) -> NSAttributedString {
        let base = NSFont.systemFont(ofSize: Metrics.fontSize, weight: .heavy)
        let font = base.fontDescriptor.withDesign(.rounded)
            .flatMap { NSFont(descriptor: $0, size: Metrics.fontSize) } ?? base

        let shadow = NSShadow()
        shadow.shadowColor = NSColor.black.withAlphaComponent(0.55)
        shadow.shadowBlurRadius = 14
        shadow.shadowOffset = NSSize(width: 0, height: -3)  // unflipped view: -y is down

        return NSAttributedString(string: String(value), attributes: [
            .font: font,
            // White fill inside a black outline. Legibility comes from the light/dark
            // edge rather than from hue, so the numeral survives a white page, a black
            // terminal or arbitrary video underneath it.
            .foregroundColor: NSColor.white,
            .strokeColor: NSColor.black,
            // Negative width means fill *and* stroke; the magnitude is a percentage of
            // the point size, so the outline stays proportional if the font grows.
            .strokeWidth: -7.0,
            .shadow: shadow,
        ])
    }

    private func makePanel() -> OverlayPanel {
        let panel = OverlayPanel(contentRect: .zero,
                                 styleMask: [.borderless, .nonactivatingPanel],
                                 backing: .buffered,
                                 defer: false)
        panel.contentView = NumeralView()
        panel.backgroundColor = .clear
        panel.isOpaque = false
        // A window shadow would trace a rectangle around transparent pixels — exactly
        // the background box the Countdown must not have. The glyph carries its own.
        panel.hasShadow = false
        panel.ignoresMouseEvents = true
        panel.isFloatingPanel = true
        // Set last: `isFloatingPanel` rewrites the level to `.floating`, which would
        // silently undo this and let the menu bar cover the Countdown.
        panel.level = .screenSaver
        panel.becomesKeyOnlyIfNeeded = true
        // The Shell is LSUIElement and therefore never the active app; without this the
        // panel would be ordered out the instant it appeared.
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        // `.fullScreenAuxiliary` lets it sit over a full-screen app instead of forcing a
        // Space switch; `.stationary` keeps it put during Mission Control.
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary,
                                    .fullScreenAuxiliary, .ignoresCycle]
        // The default panel fade would smear a numeral that changes once a second.
        panel.animationBehavior = .none
        return panel
    }
}

/// Never key, never main. A Countdown must not interrupt the thing it is counting down
/// on, so the app the user is actually working in keeps focus throughout.
private final class OverlayPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// Draws the numeral centred and nothing else — no fill, so the transparent panel shows
/// straight through around it.
private final class NumeralView: NSView {
    var numeral: NSAttributedString? {
        didSet { needsDisplay = true }
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let numeral else { return }
        let glyph = numeral.size()
        numeral.draw(at: NSPoint(x: (bounds.width - glyph.width) / 2,
                                 y: (bounds.height - glyph.height) / 2))
    }
}
