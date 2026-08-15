import AppKit
import Combine
import SwiftUI

// handTrack — menu bar app wrapping the Python hand-tracking Engine.
//
// This file only wires the pieces together. Every component observes AppState and none
// of them talk to each other, so the wiring stays this short.

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {

    private let state = AppState()
    private let permissions = PermissionsChecker()

    private var engine: EngineProcess!
    private var menuBar: MenuBarController!
    private var overlay: CountdownOverlay!
    private var hotkey: GlobalHotkey!

    private var firstRunWindow: NSWindow?
    private var cancellables = Set<AnyCancellable>()

    /// The last combination macOS actually accepted, so a rejected rebinding has
    /// something real to fall back to.
    private var acceptedShortcut: Shortcut = .default

    func applicationDidFinishLaunching(_ notification: Notification) {
        Diagnostics.log("didFinishLaunching")

        // Menu bar only: no Dock icon, no app switcher entry.
        NSApp.setActivationPolicy(.accessory)

        engine = EngineProcess(state: state)
        overlay = CountdownOverlay(state: state)
        menuBar = MenuBarController(appState: state, engine: engine, permissions: permissions)
        Diagnostics.log("at launch: " + menuBar.diagnosticSummary)
        // The status item is not positioned until AppKit has laid the menu bar out, so
        // its frame at launch is meaningless. This is the reading that counts.
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            guard let self else { return }
            Diagnostics.log("settled:   " + self.menuBar.diagnosticSummary)
        }

        state.shortcut = ShortcutStore.load()
        acceptedShortcut = state.shortcut
        hotkey = GlobalHotkey { [weak self] in self?.toggleFromShortcut() }
        if !hotkey.register(state.shortcut), state.shortcut != .default {
            // The stored combination has been claimed by something else since it was
            // set. Falling back keeps a working shortcut rather than none at all.
            state.apply(engineError:
                "\(state.shortcut.displayString) is no longer available — using "
                + "\(Shortcut.default.displayString).")
            acceptedShortcut = .default
            state.shortcut = .default
        }

        // Rebinding in Settings re-registers immediately; the old combination is
        // released by register(_:) itself.
        state.$shortcut
            .dropFirst()
            .sink { [weak self] shortcut in
                guard let self else { return }
                // Only persist a binding macOS accepted. `accepted` is the last one that
                // actually registered, so a rejected combination can be backed out of
                // rather than becoming the value we restore on the next launch.
                if self.hotkey.register(shortcut) {
                    self.acceptedShortcut = shortcut
                    ShortcutStore.save(shortcut)
                    self.state.apply(engineError: nil)
                } else {
                    _ = self.hotkey.register(self.acceptedShortcut)
                    self.state.apply(engineError:
                        "Another app already uses that shortcut. Keeping "
                        + "\(self.acceptedShortcut.displayString).")
                    self.state.shortcut = self.acceptedShortcut
                }
            }
            .store(in: &cancellables)

        state.launchAtLogin = FirstRunLoginItem.isEnabled
        state.$launchAtLogin
            .dropFirst()
            .sink { [weak self] wanted in
                // Previously this toggle wrote a published boolean nobody read, so it
                // looked like a setting and did nothing.
                guard !FirstRunLoginItem.set(wanted) else { return }
                self?.state.apply(engineError: "Could not change the login item.")
            }
            .store(in: &cancellables)

        permissions.$allGranted
            .sink { [weak self] granted in
                self?.state.permissionsSatisfied = granted
                if granted { self?.dismissFirstRun() }
            }
            .store(in: &cancellables)

        permissions.appState = state
        permissions.refresh()
        Diagnostics.log("permissions: camera=\(permissions.camera) "
            + "accessibility=\(permissions.accessibility) "
            + "allGranted=\(permissions.allGranted) "
            + "AXIsProcessTrusted=\(AXIsProcessTrusted())")

        if !permissions.allGranted { presentFirstRun() }
    }

    /// The Toggle Shortcut must not silently do nothing when a permission is missing —
    /// Accessibility denial is invisible, so send the user somewhere that explains it.
    private func toggleFromShortcut() {
        guard state.permissionsSatisfied else {
            presentFirstRun()
            return
        }
        engine.toggle()
    }

    // MARK: - First run

    private func presentFirstRun() {
        if let window = firstRunWindow {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }

        // "Start handTrack" has to actually start it. Closing the window and leaving the
        // user with no Dock icon, no visible change and nothing running reads as a dead
        // button — which is exactly how it behaved before.
        let view = FirstRunView(checker: permissions) { [weak self] in
            guard let self else { return }
            self.dismissFirstRun()
            self.engine.start()
        }
        .environmentObject(state)

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 520, height: 460),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.title = "handTrack Setup"
        window.contentView = NSHostingView(rootView: view)
        window.center()
        window.isReleasedWhenClosed = false
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        firstRunWindow = window
    }

    private func dismissFirstRun() {
        firstRunWindow?.close()
        firstRunWindow = nil
    }

    // MARK: - Lifecycle

    /// With no Dock icon this is the escape hatch from Q8: if the menu bar icon is
    /// hidden — swallowed by the notch, or tucked away by something like Bartender —
    /// relaunching the app brings the Panel back rather than starting a second copy.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        // Re-read first. Permissions may well have been granted since launch — that is
        // the usual reason someone relaunches — and a stale answer here would show the
        // setup window forever to a user who is already set up.
        permissions.refresh()
        if permissions.allGranted {
            menuBar.showPanel()
        } else {
            presentFirstRun()
        }
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        // Release the camera before we go, so the green light never outlives the app.
        engine.shutdown()
        hotkey.unregister()
    }
}

// Top-level code is nonisolated, but it does run on the main thread — so the delegate,
// which is @MainActor like everything else in the Shell, is safe to build here.
let delegate = MainActor.assumeIsolated { AppDelegate() }
let application = NSApplication.shared
application.delegate = delegate
application.run()

/// Launch-path logging. A menu-bar-only app has nowhere to print, so failures during
/// startup are otherwise invisible — the process just sits there looking healthy.
enum Diagnostics {
    /// Under the user's own Logs directory, not /tmp. A predictable name in a
    /// world-writable directory can be pre-created as a symlink pointing anywhere the
    /// user can write, and the contents — bundle paths, permission state, screen
    /// geometry — are readable by every other account on the machine.
    private static let path = ("~/Library/Logs/handTrack.log" as NSString).expandingTildeInPath

    static func log(_ message: String) {
        let line = "[\(Date())] \(message)\n"
        guard let data = line.data(using: .utf8) else { return }
        if FileManager.default.fileExists(atPath: path),
           let handle = FileHandle(forWritingAtPath: path) {
            handle.seekToEndOfFile()
            handle.write(data)
            try? handle.close()
        } else {
            FileManager.default.createFile(atPath: path, contents: data)
        }
    }
}
