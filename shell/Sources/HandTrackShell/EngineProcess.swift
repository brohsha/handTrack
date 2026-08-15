import Combine
import Foundation

/// Owns the Python Engine: spawning it, talking to it over newline-delimited JSON,
/// and deciding when it should live or die. See `docs/protocol.md`.
///
/// The only part of the Shell that writes to `AppState`. Everything the user sees
/// reads state from there, so the interface can never disagree with the Engine about
/// whether the camera is open.
@MainActor
final class EngineProcess: ObservableObject {

    /// The Engine process is alive. True whether it is Tracking or sitting warm in
    /// Hard Off, so it is not a substitute for `AppState.isTracking`.
    @Published private(set) var isRunning = false

    private let state: AppState

    private var process: Process?
    private var stdin: FileHandle?

    /// One reader per pipe. Held so a relaunch cannot leave the previous Engine's
    /// output feeding into the new one's state.
    private var readers: [Task<Void, Never>] = []

    /// Fires the hybrid idle shutdown (Q15). Cancelled the moment Tracking resumes.
    private var idleShutdown: Task<Void, Never>?

    /// Set before every exit the Shell causes, so `terminationHandler` can tell an
    /// orderly shutdown from a crash. Anything else counts as unrequested.
    private var exitWasRequested = false

    /// Unrequested exits since the last time the user asked for Tracking. The first
    /// is retried in silence; the second gives up and surfaces the error Indicator.
    private var unrequestedExits = 0

    /// A `start` written before `ready` arrived. Without this the Engine's opening
    /// "idle in Hard Off" would report Off over a start the user has already asked for.
    private var startPending = false

    /// Idle window before the warm Engine is released, per docs/protocol.md.
    private static let idleTimeout = Duration.seconds(5 * 60)

    /// Writing to a dead Engine's stdin would otherwise raise SIGPIPE and take the
    /// Shell down with it; ignored, the same write fails as an ordinary error.
    private static let sigpipeIgnored: Void = { _ = signal(SIGPIPE, SIG_IGN) }()

    init(state: AppState) {
        _ = Self.sigpipeIgnored
        self.state = state
    }

    // MARK: - Control

    /// Enter Tracking, launching the Engine first if it is not already warm.
    func start() {
        idleShutdown?.cancel()
        idleShutdown = nil

        // A deliberate start is the user telling us to try again, so the crash
        // budget resets here rather than accumulating across a whole session.
        unrequestedExits = 0

        // `exitWasRequested` means a shutdown is already in flight. The old process can
        // still report isRunning for a second or two while MediaPipe tears down, and
        // reusing it would send `start` down a pipe nobody will ever read — leaving the
        // app stuck on "Starting…" with no Engine behind it.
        if !isRunning || exitWasRequested {
            guard launch() else { return }
        }

        startPending = true
        state.apply(status: .starting)
        send(.start)
    }

    /// Enter Hard Off. The Engine stays warm for `idleTimeout` before being released.
    func stop() {
        guard isRunning else {
            state.apply(status: .off)
            state.apply(cameraOpen: false)
            return
        }
        startPending = false
        send(.stop)
    }

    func toggle() {
        state.isTracking || state.status == .starting ? stop() : start()
    }

    /// Release everything and let the Engine exit. Used at app quit and by the idle
    /// timer; the process is only killed if it fails to leave on its own.
    func shutdown() {
        idleShutdown?.cancel()
        idleShutdown = nil
        startPending = false

        guard let process, isRunning else { return }
        exitWasRequested = true
        send(.shutdown)

        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(2))
            // The identity check matters: a start during those two seconds would have
            // replaced the process, and killing the new one would look like a crash.
            guard let self, self.process === process, process.isRunning else { return }
            process.terminate()
        }
    }

    /// Liveness check. The Engine answers with `pong`.
    func ping() {
        send(.ping)
    }

    // MARK: - Spawning

    /// Launches the Engine and wires up its three pipes. Returns false if it could
    /// not be started, having already reported the failure through `AppState`.
    @discardableResult
    private func launch() -> Bool {
        readers.forEach { $0.cancel() }
        readers.removeAll()

        guard let paths = EnginePaths.resolve() else {
            state.apply(status: .failed(reason:
                "No Python interpreter found. Rebuild with build/make-app.sh, or set "
                + "HANDTRACK_PYTHON to the interpreter that has MediaPipe installed."))
            log("no interpreter resolved; refusing to guess a path")
            return false
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: paths.python)
        process.arguments = [paths.script]
        process.currentDirectoryURL = URL(fileURLWithPath: paths.script).deletingLastPathComponent()

        var environment = ProcessInfo.processInfo.environment
        // Python block-buffers stdout when it is a pipe, so without this the Engine's
        // events would sit unseen until it exited — a Countdown would never arrive.
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment

        let inPipe = Pipe()
        let outPipe = Pipe()
        let errPipe = Pipe()
        process.standardInput = inPipe
        process.standardOutput = outPipe
        process.standardError = errPipe

        // Weak on the OUTER closure too: a capture list on the nested Task does not
        // reach out here, so without this the handler retains self and self retains
        // the process that owns the handler.
        process.terminationHandler = { [weak self] finished in
            guard self != nil else { return }
            let code = finished.terminationStatus
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.engineExited(code: code, from: finished)
            }
        }

        do {
            try process.run()
        } catch {
            state.apply(status: .failed(reason: "Could not launch the Engine at \(paths.python)."))
            log("launch failed: \(error)")
            return false
        }

        self.process = process
        self.stdin = inPipe.fileHandleForWriting
        isRunning = true
        // Belongs to the process we just replaced, not this one. Left set, an early
        // crash of the new Engine would be read as an orderly shutdown and never
        // counted against the restart budget.
        exitWasRequested = false

        let events = Self.chunks(from: outPipe.fileHandleForReading)
        let diagnostics = Self.chunks(from: errPipe.fileHandleForReading)
        readers = [
            Task { [weak self] in
                guard let self else { return }
                await self.readEvents(from: events)
            },
            // Drained rather than used: an Engine that chatters on stderr would
            // otherwise fill the pipe buffer and block mid-frame.
            Task { [weak self] in
                guard let self else { return }
                await self.readDiagnostics(from: diagnostics)
            },
        ]
        return true
    }

    private func engineExited(code: Int32, from finished: Process) {
        // An Engine asked to shut down can take a second or two to go, and the user may
        // have started a replacement in the meantime. Without this check the straggler's
        // exit tears down the process that replaced it.
        guard finished === process else {
            log("ignoring exit (\(code)) from a superseded Engine")
            if exitWasRequested { exitWasRequested = false }
            return
        }

        isRunning = false
        process = nil
        stdin = nil
        startPending = false
        idleShutdown?.cancel()
        idleShutdown = nil

        // Readers are left to finish at EOF: cancelling here would discard the last
        // events the Engine wrote on its way out.

        if exitWasRequested {
            exitWasRequested = false
            return
        }

        unrequestedExits += 1
        log("engine exited unexpectedly (\(code)), unrequested exit \(unrequestedExits)")

        guard unrequestedExits < 2 else {
            state.apply(status: .failed(reason: "The Engine stopped unexpectedly."))
            state.apply(cameraOpen: false)
            return
        }

        // Silence only means something if the user gets back what they had. Losing a
        // warm idle Engine costs them nothing, so that one is simply not replaced —
        // the next start pays the reload it would have paid anyway.
        guard state.isTracking else {
            state.apply(status: .off)
            state.apply(cameraOpen: false)
            return
        }

        state.apply(status: .starting)
        state.apply(cameraOpen: false)
        guard launch() else { return }
        startPending = true
        send(.start)
    }

    // MARK: - Sending

    private struct Command: Encodable {
        let cmd: String

        static let start = Command(cmd: "start")
        static let stop = Command(cmd: "stop")
        static let shutdown = Command(cmd: "shutdown")
        static let ping = Command(cmd: "ping")
    }

    private func send(_ command: Command) {
        guard let stdin, process?.isRunning == true else {
            log("dropped \(command.cmd): no Engine to send it to")
            return
        }
        do {
            var line = try JSONEncoder().encode(command)
            line.append(0x0A)
            try stdin.write(contentsOf: line)
        } catch {
            log("could not send \(command.cmd): \(error)")
        }
    }

    // MARK: - Receiving

    /// One event as it arrives on the wire. Every field past `event` is optional so a
    /// message the Shell does not recognise still decodes instead of killing the line.
    private struct Event: Decodable {
        let event: String
        let tracking: Bool?
        let camera: String?
        let secondsLeft: Int?
        let fatal: Bool?
        let message: String?

        enum CodingKeys: String, CodingKey {
            case event, tracking, camera, fatal, message
            case secondsLeft = "seconds_left"
        }
    }

    private func readEvents(from stream: AsyncStream<Data>) async {
        for await line in Self.lines(from: stream) {
            guard let data = line.data(using: .utf8) else { continue }
            do {
                handle(try JSONDecoder().decode(Event.self, from: data))
            } catch {
                // Never fatal, per docs/protocol.md — a typo on the pipe must not
                // bring down hand tracking.
                log("unparseable event: \(line)")
            }
        }
    }

    private func readDiagnostics(from stream: AsyncStream<Data>) async {
        for await line in Self.lines(from: stream) {
            log("engine stderr: \(line)")
        }
    }

    private func handle(_ event: Event) {
        switch event.event {
        case "ready":
            state.apply(cameraOpen: false)
            if !startPending {
                state.apply(status: .off)
                scheduleIdleShutdown()
            }

        case "state":
            startPending = false
            let cameraOpen = event.camera == "open"
            state.apply(cameraOpen: cameraOpen)

            if event.tracking == true {
                // A Countdown is a phase of Tracking, not a transition out of it, so
                // a redundant tracking:true must not wipe the seconds on screen.
                if case .countingDown = state.status { break }
                state.apply(status: .on)
            } else {
                state.apply(status: .off)
                scheduleIdleShutdown()
            }

        case "countdown":
            guard let secondsLeft = event.secondsLeft else { break }
            if secondsLeft <= 0 {
                // Zero means the Countdown finished and the Engine is stopping Tracking
                // on the same frame. Rendering "Disabling in 0…" until its state event
                // arrives leaves the Panel contradicting the switched-off toggle beside
                // it — and strands there entirely if that event is ever missed.
                state.apply(status: .off)
            } else {
                state.apply(status: .countingDown(secondsLeft: secondsLeft))
            }

        case "countdown_cancelled":
            // The hand is still being tracked; only the overlay goes away.
            if case .countingDown = state.status {
                state.apply(status: .on)
            }

        case "error":
            log("engine error: \(event.message ?? "unspecified")")
            // Surfaced, not just logged. A non-fatal error is how the Engine reports
            // that tracking is running but achieving nothing — swallowing it leaves the
            // menu bar showing a confident green dot over a cursor that never moves.
            state.apply(engineError: event.message ?? "The Engine reported a problem.")
            guard event.fatal == true else { break }
            // A fatal error is an announced exit, not a crash, so it must not spend
            // the restart budget or be papered over by a silent relaunch.
            exitWasRequested = true
            state.apply(status: .failed(reason: event.message ?? "The Engine reported a fatal error."))

        case "pong":
            break

        default:
            log("unknown event: \(event.event)")
        }
    }

    // MARK: - Idle lifecycle

    private func scheduleIdleShutdown() {
        idleShutdown?.cancel()
        idleShutdown = Task { @MainActor [weak self] in
            try? await Task.sleep(for: Self.idleTimeout)
            guard !Task.isCancelled else { return }
            self?.shutdown()
        }
    }

    // MARK: - Pipe reading

    /// Raw bytes as the pipe produces them. Non-blocking: the Engine is long-lived, so
    /// reading to end-of-file would only return once it had died.
    private nonisolated static func chunks(from handle: FileHandle) -> AsyncStream<Data> {
        AsyncStream { continuation in
            handle.readabilityHandler = { pipe in
                let chunk = pipe.availableData
                if chunk.isEmpty {
                    pipe.readabilityHandler = nil
                    continuation.finish()
                } else {
                    continuation.yield(chunk)
                }
            }
            continuation.onTermination = { _ in
                handle.readabilityHandler = nil
            }
        }
    }

    /// Reassembles whole lines from arbitrary chunks. A JSON object can be split across
    /// reads, or several can land in one, so neither boundary can be assumed.
    private nonisolated static func lines(from stream: AsyncStream<Data>) -> AsyncStream<String> {
        AsyncStream { continuation in
            let task = Task {
                var buffer = Data()
                for await chunk in stream {
                    buffer.append(chunk)
                    while let newline = buffer.firstIndex(of: 0x0A) {
                        let line = Data(buffer[buffer.startIndex..<newline])
                        buffer.removeSubrange(buffer.startIndex...newline)
                        if let text = String(data: line, encoding: .utf8)?
                            .trimmingCharacters(in: .whitespacesAndNewlines),
                           !text.isEmpty {
                            continuation.yield(text)
                        }
                    }
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private nonisolated func log(_ message: String) {
        try? FileHandle.standardError.write(contentsOf: Data("[engine] \(message)\n".utf8))
    }
}

/// Where the interpreter and the Engine's entry script live. Inside a built bundle
/// both come from Resources; in development they come from the repo, so the Shell can
/// be run straight from SwiftPM without assembling an app first.
private struct EnginePaths {
    let python: String
    let script: String

    /// Nothing is guessed. This process holds Camera and Accessibility, so whatever it
    /// executes inherits the webcam and the ability to synthesise input without any
    /// further prompt. A fallback to a fixed, user-writable path would mean that on any
    /// machine where the path happens not to exist, anyone able to write to the user's
    /// home could create it and be executed with those grants.
    ///
    /// So each candidate is explicit, and if none resolves the Engine does not start.
    static func resolve() -> EnginePaths? {
        let resources = Bundle.main.resourceURL
        let files = FileManager.default

        guard let script = resources?.appending(path: "engine/main.py").path,
              files.isReadableFile(atPath: script) else {
            return nil
        }

        // 1. An interpreter inside the bundle. Absent until Q13 embeds one, but it is
        //    the only candidate that travels with the signed app, so it wins.
        if let bundled = resources?.appending(path: "python/bin/python3").path,
           files.isExecutableFile(atPath: bundled) {
            return EnginePaths(python: bundled, script: script)
        }

        // 2. An explicit override. A deliberate act by whoever launched the app, rather
        //    than a path an attacker can guess and pre-create.
        if let override = ProcessInfo.processInfo.environment["HANDTRACK_PYTHON"],
           !override.isEmpty, files.isExecutableFile(atPath: override) {
            return EnginePaths(python: override, script: script)
        }

        // 3. The interpreter recorded at build time, which is correct for this machine
        //    rather than for whichever machine happened to build it.
        if let configured = Bundle.main.object(forInfoDictionaryKey: "HTDevelopmentPython") as? String,
           !configured.isEmpty, files.isExecutableFile(atPath: configured) {
            return EnginePaths(python: configured, script: script)
        }

        return nil
    }
}
