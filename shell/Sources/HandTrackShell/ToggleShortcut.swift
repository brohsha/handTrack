import AppKit
import Carbon.HIToolbox
import Foundation
import SwiftUI

// The Toggle Shortcut has to fire while the user is in another app — that is the
// whole point of it — so it is registered with Carbon's `RegisterEventHotKey`.
// The alternative, `NSEvent.addGlobalMonitorForEvents`, would additionally require
// Accessibility just to observe keystrokes, and would see every key the user types
// rather than only ours.

// MARK: - Shortcut

extension Shortcut {

    /// ⌃⌥⌘H — three modifiers puts it clear of anything the popular apps claim.
    static let `default` = Shortcut(
        keyCode: UInt32(kVK_ANSI_H),
        modifiers: UInt32(controlKey | optionKey | cmdKey)
    )

    /// Human-readable form for the Panel, e.g. "⌃⌥⌘H".
    ///
    /// Modifier symbols are emitted in Apple's canonical order (⌃⌥⇧⌘) so the Panel
    /// reads the same way as a system menu, whatever order the user pressed them in.
    var displayString: String {
        var symbols = ""
        if modifiers & UInt32(controlKey) != 0 { symbols += "⌃" }
        if modifiers & UInt32(optionKey) != 0 { symbols += "⌥" }
        if modifiers & UInt32(shiftKey) != 0 { symbols += "⇧" }
        if modifiers & UInt32(cmdKey) != 0 { symbols += "⌘" }
        return symbols + Shortcut.keyName(for: keyCode)
    }

    /// Translates AppKit's modifier flags into the Carbon bits `RegisterEventHotKey`
    /// wants, which is the only form we store.
    static func carbonModifiers(from flags: NSEvent.ModifierFlags) -> UInt32 {
        var carbon: UInt32 = 0
        if flags.contains(.control) { carbon |= UInt32(controlKey) }
        if flags.contains(.option) { carbon |= UInt32(optionKey) }
        if flags.contains(.shift) { carbon |= UInt32(shiftKey) }
        if flags.contains(.command) { carbon |= UInt32(cmdKey) }
        return carbon
    }

    private static func keyName(for keyCode: UInt32) -> String {
        if let name = keyNames[Int(keyCode)] { return name }
        return "Key \(keyCode)"
    }

    /// Virtual key code to printed legend. Fixed to the US layout: the toggleShortcut itself
    /// is registered by key code, so a different layout still triggers on the same
    /// physical key — only this label would name the wrong letter.
    private static let keyNames: [Int: String] = [
        kVK_ANSI_A: "A", kVK_ANSI_B: "B", kVK_ANSI_C: "C", kVK_ANSI_D: "D",
        kVK_ANSI_E: "E", kVK_ANSI_F: "F", kVK_ANSI_G: "G", kVK_ANSI_H: "H",
        kVK_ANSI_I: "I", kVK_ANSI_J: "J", kVK_ANSI_K: "K", kVK_ANSI_L: "L",
        kVK_ANSI_M: "M", kVK_ANSI_N: "N", kVK_ANSI_O: "O", kVK_ANSI_P: "P",
        kVK_ANSI_Q: "Q", kVK_ANSI_R: "R", kVK_ANSI_S: "S", kVK_ANSI_T: "T",
        kVK_ANSI_U: "U", kVK_ANSI_V: "V", kVK_ANSI_W: "W", kVK_ANSI_X: "X",
        kVK_ANSI_Y: "Y", kVK_ANSI_Z: "Z",

        kVK_ANSI_0: "0", kVK_ANSI_1: "1", kVK_ANSI_2: "2", kVK_ANSI_3: "3",
        kVK_ANSI_4: "4", kVK_ANSI_5: "5", kVK_ANSI_6: "6", kVK_ANSI_7: "7",
        kVK_ANSI_8: "8", kVK_ANSI_9: "9",

        kVK_ANSI_Minus: "-", kVK_ANSI_Equal: "=", kVK_ANSI_LeftBracket: "[",
        kVK_ANSI_RightBracket: "]", kVK_ANSI_Backslash: "\\", kVK_ANSI_Semicolon: ";",
        kVK_ANSI_Quote: "'", kVK_ANSI_Comma: ",", kVK_ANSI_Period: ".",
        kVK_ANSI_Slash: "/", kVK_ANSI_Grave: "`",

        kVK_Return: "↩", kVK_Tab: "⇥", kVK_Space: "Space", kVK_Delete: "⌫",
        kVK_ForwardDelete: "⌦", kVK_Escape: "⎋", kVK_ANSI_KeypadEnter: "⌤",
        kVK_ANSI_KeypadClear: "⌧", kVK_Help: "Help",

        kVK_Home: "↖", kVK_End: "↘", kVK_PageUp: "⇞", kVK_PageDown: "⇟",
        kVK_LeftArrow: "←", kVK_RightArrow: "→", kVK_UpArrow: "↑", kVK_DownArrow: "↓",

        kVK_F1: "F1", kVK_F2: "F2", kVK_F3: "F3", kVK_F4: "F4", kVK_F5: "F5",
        kVK_F6: "F6", kVK_F7: "F7", kVK_F8: "F8", kVK_F9: "F9", kVK_F10: "F10",
        kVK_F11: "F11", kVK_F12: "F12", kVK_F13: "F13", kVK_F14: "F14",
        kVK_F15: "F15", kVK_F16: "F16", kVK_F17: "F17", kVK_F18: "F18",
        kVK_F19: "F19", kVK_F20: "F20",
    ]
}

// MARK: - Persistence

/// Where the user's chosen Toggle Shortcut lives between launches.
enum ShortcutStore {

    private static let key = "toggleShortcut"

    /// Falls back to ⌃⌥⌘H on a first run, and also on stored data we can no longer
    /// decode — a corrupt preference should cost the user their binding, not the app.
    static func load() -> Shortcut {
        guard let data = UserDefaults.standard.data(forKey: key),
              let stored = try? JSONDecoder().decode(Shortcut.self, from: data)
        else { return .default }
        return stored
    }

    static func save(_ shortcut: Shortcut) {
        guard let data = try? JSONEncoder().encode(shortcut) else { return }
        UserDefaults.standard.set(data, forKey: key)
    }
}

// MARK: - Global toggleShortcut

/// The system-wide Toggle Shortcut. One instance owns one registration; call
/// `register(_:)` again to rebind and the old combination is released first.
@MainActor
final class GlobalToggleShortcut {

    /// Currently registered combination, or nil if nothing is registered.
    private(set) var shortcut: Shortcut?

    private let onFire: () -> Void
    private var shortcutRef: EventHotKeyRef?
    private var handlerRef: EventHandlerRef?

    init(onFire: @escaping () -> Void) {
        self.onFire = onFire
        installHandler()
    }

    deinit {
        // Carbon teardown only. A `deinit` cannot call main-actor methods, but both
        // of these are plain C and the handler must go before `self`'s memory does —
        // it holds an unretained pointer back to us.
        if let shortcutRef { _ = UnregisterEventHotKey(shortcutRef) }
        if let handlerRef { _ = RemoveEventHandler(handlerRef) }
    }

    /// Claims `shortcut` system-wide, replacing any previous registration.
    /// Returns false if macOS refused it, which in practice means another
    /// application already owns that combination.
    @discardableResult
    func register(_ shortcut: Shortcut) -> Bool {
        unregister()

        var ref: EventHotKeyRef?
        let status = RegisterEventHotKey(
            shortcut.keyCode,
            shortcut.modifiers,
            EventHotKeyID(signature: shortcutSignature, id: shortcutIdentifier),
            GetApplicationEventTarget(),
            0,
            &ref
        )

        guard status == noErr, let ref else { return false }

        shortcutRef = ref
        self.shortcut = shortcut
        return true
    }

    func unregister() {
        if let shortcutRef { _ = UnregisterEventHotKey(shortcutRef) }
        shortcutRef = nil
        shortcut = nil
    }

    fileprivate func fired(id: UInt32) {
        guard id == shortcutIdentifier else { return }
        onFire()
    }

    /// Installed once for the lifetime of the instance, not per registration, so
    /// rebinding never leaves a stale handler behind.
    private func installHandler() {
        var spec = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard),
            eventKind: UInt32(kEventHotKeyPressed)
        )

        // Unretained: the handler is torn down in `deinit`, so it can never outlive us.
        _ = InstallEventHandler(
            GetApplicationEventTarget(),
            shortcutEventHandler,
            1,
            &spec,
            Unmanaged.passUnretained(self).toOpaque(),
            &handlerRef
        )
    }
}

private let shortcutSignature = OSType(0x6874_6B79)  // 'htky'
private let shortcutIdentifier: UInt32 = 1

private let shortcutEventHandler: EventHandlerUPP = { _, event, userData in
    guard let event, let userData else { return OSStatus(eventNotHandledErr) }

    var firedID = EventHotKeyID()
    let status = GetEventParameter(
        event,
        EventParamName(kEventParamDirectObject),
        EventParamType(typeEventHotKeyID),
        nil,
        MemoryLayout<EventHotKeyID>.size,
        nil,
        &firedID
    )
    guard status == noErr else { return status }

    // Carbon dispatches application event handlers on the main thread, so we are
    // already on the main actor. Asserting that rather than hopping keeps the
    // toggle synchronous with the key press.
    return MainActor.assumeIsolated {
        Unmanaged<GlobalToggleShortcut>.fromOpaque(userData)
            .takeUnretainedValue()
            .fired(id: firedID.id)
        return noErr
    }
}

// MARK: - Recorder

/// The Panel's control for rebinding the Toggle Shortcut. Click it, press a
/// combination, and it is saved; Escape backs out without changing anything.
struct ShortcutRecorderView: View {

    @Binding var shortcut: Shortcut

    /// Called after a new combination is saved, so the owner can re-register the
    /// `GlobalToggleShortcut`. This view deliberately knows nothing about registration.
    var onRecorded: ((Shortcut) -> Void)?

    @State private var isRecording = false
    @State private var monitor: Any?
    @State private var hint: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button {
                if isRecording { stopRecording() } else { startRecording() }
            } label: {
                Text(isRecording ? "Press keys…" : shortcut.displayString)
                    .frame(minWidth: 88)
                    .padding(.vertical, 2)
            }
            .buttonStyle(.bordered)
            .tint(isRecording ? Color.accentColor : nil)
            .help("Click, then press the keys you want. Escape cancels.")

            if let hint {
                Text(hint)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .onDisappear(perform: stopRecording)
    }

    private func startRecording() {
        guard monitor == nil else { return }
        isRecording = true
        hint = nil

        // A local monitor sees only keys aimed at this app, and returning nil
        // swallows them — otherwise pressing ⌘Q to record it would quit instead.
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .flagsChanged]) { event in
            handle(event)
            return nil
        }
    }

    private func stopRecording() {
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
        isRecording = false
        hint = nil
    }

    private func handle(_ event: NSEvent) {
        // Pressing a modifier on its own only ever produces .flagsChanged, so
        // ignoring that type is what stops a lone ⌘ being recorded as a shortcut.
        guard event.type == .keyDown else { return }

        let keyCode = UInt32(event.keyCode)
        if keyCode == UInt32(kVK_Escape) {
            stopRecording()
            return
        }

        let modifiers = Shortcut.carbonModifiers(from: event.modifierFlags)

        // Shift alone is not enough: ⇧A is how you type a capital, so such a
        // shortcut would fire mid-sentence. Same reasoning as rejecting a bare key.
        guard modifiers & UInt32(cmdKey | controlKey | optionKey) != 0 else {
            hint = "Add ⌘, ⌃ or ⌥ — anything less would fire while you type."
            return
        }

        let recorded = Shortcut(keyCode: keyCode, modifiers: modifiers)
        // Published, not persisted. Whoever owns the GlobalToggleShortcut decides whether
        // macOS accepted this combination, and only an accepted one is worth storing —
        // saving here would make a rejected binding the value we fall back to.
        shortcut = recorded
        stopRecording()
        onRecorded?(recorded)
    }
}
