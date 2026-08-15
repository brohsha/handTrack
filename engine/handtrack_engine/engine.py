"""The Engine: camera, MediaPipe, the frame loop, and the Disable Fist state machine.

Everything the Engine owns comes together here. Gesture geometry lives in
`gestures`, cursor control in `tracking`, and the wire format in `protocol`; this
module is the loop that drives them and the commands from the Shell that steer it.

Nothing here draws, and nothing here decides what the user sees — the Shell owns
that. The Engine reports; it never asks.
"""

import enum
import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple

from . import protocol
from .gestures import (
    INDEX_TIP,
    MIDDLE_TIP,
    RING_MCP,
    THUMB_TIP,
    WRIST,
    Landmark,
    is_disable_fist,
)

#: How long a Disable Fist must be held before the Countdown starts.
DWELL_SECONDS = 1.0

#: The Countdown starts here and ends at zero, one TICK_SECONDS apart.
COUNTDOWN_FROM = 4
TICK_SECONDS = 1.0

#: A Disable Fist is ignored for this long after Tracking starts, so a hand still
#: closed from the last shutdown cannot immediately trigger another.
GRACE_PERIOD_SECONDS = 3.0

CAMERA_INDEX = 0

#: How long the loop waits on the command queue while in Hard Off. There is no
#: frame to process there, so without this the loop would spin a core for nothing.
IDLE_POLL_SECONDS = 0.1

#: Enough of an unusable line to identify it, without letting a runaway Shell push
#: an unbounded string back down the pipe.
_BAD_LINE_EXCERPT = 80

# Imported at startup rather than here: cv2 and mediapipe together cost about a
# second and load a model, and tracking reaches the window server through
# pyautogui. Deferring all three keeps the state machine below importable — and so
# unit-testable — without a camera, a screen, or that second.
cv2 = None
mp_hands = None
tracking = None


def _load_dependencies() -> None:
    global cv2, mp_hands, tracking
    import cv2 as opencv
    import mediapipe

    from . import tracking as tracking_module

    cv2 = opencv
    mp_hands = mediapipe.solutions.hands
    tracking = tracking_module


def _write_to_stdout(line: str) -> None:
    sys.stdout.write(line)
    sys.stdout.flush()


class FistState(enum.Enum):
    """Where a Disable Fist has got to. Explicit because the three differ in what
    the hand is allowed to do, not only in what the Shell is told."""

    IDLE = "idle"
    DWELLING = "dwelling"
    COUNTING = "counting"


@dataclass(frozen=True)
class FistUpdate:
    """What one frame owes the Shell.

    `ticks` holds every `seconds_left` that came due on this frame, in order — more
    than one when a slow frame straddles a tick boundary.
    """

    ticks: Tuple[int, ...] = ()
    cancelled: bool = False
    disable: bool = False


class DisableFistState:
    """The Disable Fist state machine: Grace Period, Dwell, Countdown, Lockout.

    Timed against a clock rather than counted in frames, because the frame rate
    varies with light and load and a Countdown that ran at the camera's pace would
    be a different length every time. The clock is injected so the whole machine can
    be tested without a camera.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 dwell_seconds: float = DWELL_SECONDS,
                 grace_seconds: float = GRACE_PERIOD_SECONDS,
                 countdown_from: int = COUNTDOWN_FROM,
                 tick_seconds: float = TICK_SECONDS):
        # monotonic, not wall time: a clock correction mid-Countdown must not skip
        # it to zero or stall it forever.
        self._clock = clock
        self._dwell_seconds = dwell_seconds
        self._grace_seconds = grace_seconds
        self._countdown_from = countdown_from
        self._tick_seconds = tick_seconds

        self._state = FistState.IDLE
        self._started_at = 0.0
        self._last_tick: Optional[int] = None
        self._grace_until: Optional[float] = None

    @property
    def state(self) -> FistState:
        return self._state

    @property
    def locked_out(self) -> bool:
        """True from the start of the Dwell until the Countdown ends or cancels.

        Deliberately true during the Dwell, before anything has been counted: a
        fist reads as an index Pinch, so waiting for the Countdown to begin would
        mean a second of dragging with the mouse button held down.
        """
        return self._state is not FistState.IDLE

    def tracking_started(self) -> None:
        """Tracking has begun. Opens the Grace Period."""
        self.reset()
        self._grace_until = self._clock() + self._grace_seconds

    def reset(self) -> None:
        """Abandon any Dwell or Countdown without announcing it."""
        self._state = FistState.IDLE
        self._last_tick = None

    def update(self, fist: bool) -> FistUpdate:
        """Advance one frame. `fist` is False when the hand is lost, which is the
        same break as opening it."""
        now = self._clock()

        if self._grace_until is not None:
            if now < self._grace_until:
                fist = False  # the Grace Period does not look at the hand at all
            else:
                self._grace_until = None

        if not fist:
            return self._break()
        if self._state is FistState.IDLE:
            return self._begin_dwell(now)
        if self._state is FistState.DWELLING and not self._dwell_complete(now):
            return FistUpdate()
        return self._count(now)

    def _break(self) -> FistUpdate:
        if self._state is FistState.IDLE:
            return FistUpdate()
        self.reset()
        return FistUpdate(cancelled=True)

    def _begin_dwell(self, now: float) -> FistUpdate:
        self._state = FistState.DWELLING
        self._started_at = now
        return FistUpdate()

    def _dwell_complete(self, now: float) -> bool:
        if now - self._started_at < self._dwell_seconds:
            return False
        self._state = FistState.COUNTING
        # Anchored to when the Dwell was due rather than to now, so a late frame
        # shortens that one tick instead of pushing the whole Countdown back.
        self._started_at += self._dwell_seconds
        return True

    def _count(self, now: float) -> FistUpdate:
        elapsed = now - self._started_at
        due = max(self._countdown_from - int(elapsed // self._tick_seconds), 0)

        # Every tick that came due is emitted, not just the newest one: seconds_left
        # 0 is what the Shell reads immediately before Tracking stops, so a stalled
        # frame must never be able to jump over it.
        ticks = []
        while self._last_tick is None or self._last_tick > due:
            self._last_tick = (self._countdown_from if self._last_tick is None
                               else self._last_tick - 1)
            ticks.append(self._last_tick)

        disable = self._last_tick == 0
        if disable:
            self.reset()
        return FistUpdate(ticks=tuple(ticks), disable=disable)


@dataclass
class _Frame:
    """One camera frame's worth of what the gesture code needs."""

    width: int
    height: int
    landmarks: Optional[Sequence[Landmark]] = None


class Engine:
    """Owns the camera, the MediaPipe Hands instance, and the frame loop.

    The Shell drives it over stdin; every event goes out through `emit` as one
    protocol line. The process outlives Hard Off — only `shutdown` ends it.
    """

    def __init__(self, emit: Optional[Callable[[str], None]] = None,
                 clock: Callable[[], float] = time.monotonic):
        self._emit_line = emit if emit is not None else _write_to_stdout
        # The stdin thread reports unusable lines, so writes are serialised rather
        # than left to interleave halfway through a JSON object.
        self._emit_lock = threading.Lock()

        self._commands: "queue.Queue[str]" = queue.Queue()
        self._fist_state = DisableFistState(clock=clock)

        self._camera = None
        self._hands = None
        self._cursor = None

        self._tracking = False
        self._running = True
        self._shell_driven = True
        self._previous_y: Optional[float] = None
        #: Consecutive failed frame reads. A camera that is unplugged or suspended stops
        #: returning frames without ever raising, which would otherwise spin this loop
        #: forever with the user told nothing.
        self._read_failures = 0
        #: Consecutive frames with no hand while a button is held. Only a drag cares:
        #: it decides when an interrupted one has waited long enough to be cancelled.
        self._lost_frames = 0

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> None:
        """Serve the Shell until it says shutdown, or until stdin closes."""
        self._shell_driven = True
        if not self._start_up():
            return
        threading.Thread(target=self._read_commands, name="engine-stdin",
                         daemon=True).start()
        self._loop()

    def run_standalone(self) -> None:
        """Track immediately with no Shell, until the camera fails or Ctrl-C."""
        self._shell_driven = False
        if not self._start_up():
            return
        self._start_tracking()
        self._loop()

    def _start_up(self) -> bool:
        try:
            _load_dependencies()
            self._hands = mp_hands.Hands(
                static_image_mode=False,
                max_num_hands=1,
                min_detection_confidence=0.7,
                min_tracking_confidence=0.7,
                model_complexity=1,
            )
            self._cursor = tracking.CursorController()
        except Exception as failure:
            self._tear_down()  # Hands may already be holding a model
            self._emit(protocol.error_event(f"Engine failed to start: {failure}",
                                            fatal=True))
            return False
        self._emit(protocol.encode("ready"))
        return True

    def _loop(self) -> None:
        try:
            while self._running:
                if self._shell_driven:
                    self._drain_commands()
                elif not self._tracking:
                    break  # standalone: nothing to track and no Shell to ask
                if self._tracking:
                    self._process_frame()
        except KeyboardInterrupt:
            pass
        except Exception as failure:
            self._emit(protocol.error_event(
                f"{type(failure).__name__}: {failure}", fatal=True))
        finally:
            self._tear_down()

    def _tear_down(self) -> None:
        if self._tracking:
            self._release_tracking()
        # Keyed on the handle, not the tracking flag: a failure part-way through startup
        # or shutdown can leave the camera open with tracking already False, and this is
        # the last chance anything has to close it.
        self._close_camera()
        if self._hands is not None:
            try:
                self._hands.close()
            finally:
                self._hands = None

    # -- commands ----------------------------------------------------------

    def _read_commands(self) -> None:
        """Read stdin on its own thread, so a frame in flight never delays a command.

        End of input means the Shell is gone. Shutting down then is what stops an
        orphaned Engine from holding the camera open behind a dead app.
        """
        for line in sys.stdin:
            command = protocol.decode_command(line)
            if command is not None:
                self._commands.put(command)
            elif line.strip():
                self._emit(protocol.error_event(
                    f"Ignored unusable command: {line.strip()[:_BAD_LINE_EXCERPT]}"))
        self._commands.put(protocol.CMD_SHUTDOWN)

    def _drain_commands(self) -> None:
        """Handle everything queued, waiting briefly when there is no frame to run."""
        while True:
            try:
                if self._tracking:
                    command = self._commands.get_nowait()
                else:
                    command = self._commands.get(timeout=IDLE_POLL_SECONDS)
            except queue.Empty:
                return
            self._handle_command(command)

    def _handle_command(self, command: str) -> None:
        if command == protocol.CMD_START:
            self._start_tracking()
        elif command == protocol.CMD_STOP:
            self._stop_tracking()
        elif command == protocol.CMD_PING:
            self._emit(protocol.encode("pong"))
        elif command == protocol.CMD_SHUTDOWN:
            self._running = False

    # -- tracking state ----------------------------------------------------

    def _start_tracking(self) -> None:
        """Enter Tracking. Already Tracking is a no-op that still re-emits state."""
        if self._tracking:
            self._emit_state()
            return

        try:
            camera = cv2.VideoCapture(CAMERA_INDEX)
            opened, _ = camera.read()
        except Exception as failure:
            # A camera another app is holding raises here rather than returning False.
            # That is recoverable, so it must not escape: an exception would exit the
            # process and spend the Shell's one silent restart on a condition that
            # restarting cannot fix.
            self._emit(protocol.error_event(
                f"Could not open the camera: {type(failure).__name__}: {failure}"))
            self._emit_state()
            return

        if not opened:
            camera.release()
            self._emit(protocol.error_event("Could not read from the camera"))
            self._emit_state()
            return

        self._camera = camera
        self._read_failures = 0
        try:
            self._cursor.start()
        except Exception as failure:
            self._close_camera()
            self._emit(protocol.error_event(
                f"Could not start cursor control: {type(failure).__name__}: {failure}"))
            self._emit_state()
            return

        # Prove the cursor actually moves before claiming Tracking works. Accessibility
        # can be revoked at any time and denial is silent, so the alternative is a lit
        # camera, a confident green Indicator, and a cursor that never moves.
        # Non-fatal: Tracking still starts, because the camera and gestures are fine and
        # the user may grant permission without restarting anything.
        try:
            if not self._cursor.probe_control():
                self._emit(protocol.error_event(
                    "The cursor is not responding. Grant Accessibility to handTrack in "
                    "System Settings > Privacy & Security, then restart handTrack."))
        except Exception as failure:
            self._emit(protocol.error_event(
                f"Could not verify cursor control: {type(failure).__name__}: {failure}"))
        self._previous_y = None
        self._lost_frames = 0
        self._tracking = True
        # Opened here rather than before the camera, so the Grace Period covers the
        # first frames the user is actually in rather than the camera warming up.
        self._fist_state.tracking_started()
        self._emit_state()

    def _stop_tracking(self) -> None:
        """Enter Hard Off. Already off is a no-op that still re-emits state."""
        if self._tracking:
            self._release_tracking()
        self._emit_state()

    def _release_tracking(self) -> None:
        self._tracking = False
        self._fist_state.reset()
        # The camera closes first. Hard Off promises the green light goes out, and the
        # cursor teardown below joins two threads and reaches Quartz — if that blocks or
        # raises, the light must not still be on behind it.
        self._close_camera()
        try:
            # Before teardown, and after the camera, so a drag still in the air when
            # Tracking ends springs back instead of dropping. Reaching here mid-drag
            # is the normal end of a Disable Fist: the Lockout held the button all
            # the way through the Countdown precisely so this could decide its fate.
            self._cursor.cancel_drag()
        except Exception as failure:
            self._emit(protocol.error_event(
                f"Could not cancel the drag in progress: "
                f"{type(failure).__name__}: {failure}"))
        try:
            self._cursor.stop()  # releases any held button before its threads go
        except Exception as failure:
            self._emit(protocol.error_event(
                f"Cursor teardown failed: {type(failure).__name__}: {failure}"))
        self._previous_y = None
        self._read_failures = 0
        self._lost_frames = 0

    def _close_camera(self) -> None:
        """Release the camera if it is open. Safe to call more than once."""
        if self._camera is not None:
            camera, self._camera = self._camera, None
            camera.release()

    # -- frames ------------------------------------------------------------

    #: Failed reads tolerated before giving up. At ~30fps this is roughly two seconds,
    #: long enough to ride out a hiccup and short enough that a dead camera is reported
    #: while the user still connects it to what they just did.
    MAX_READ_FAILURES = 60

    #: Frames the hand may be missing mid-drag before the drag is cancelled. At ~30fps
    #: this is under a fifth of a second -- long enough to ride out the odd dropped
    #: detection, short enough that a hand which has genuinely left does not leave the
    #: button held.
    MAX_DRAG_LOST_FRAMES = 5

    def _process_frame(self) -> None:
        try:
            frame = self._read_frame()
        except Exception as failure:
            # One bad frame must not end the session. Two fatal exits in a row make the
            # Shell stop retrying altogether, so a transient decode error would cost the
            # user the whole app.
            self._emit(protocol.error_event(
                f"Frame read failed: {type(failure).__name__}: {failure}"))
            frame = None

        if frame is None:
            self._read_failures += 1
            if self._read_failures >= self.MAX_READ_FAILURES:
                self._emit(protocol.error_event(
                    "Lost the camera — stopping. Reconnect it and start again."))
                self._stop_tracking()
            return

        self._read_failures = 0

        update = self._fist_state.update(
            frame.landmarks is not None and is_disable_fist(frame.landmarks))
        self._emit_fist_update(update)

        if update.disable:
            self._stop_tracking()
            return
        if self._fist_state.locked_out:
            self._apply_lockout()
            return
        if frame.landmarks is None:
            self._on_hand_lost()
            return
        self._drive_cursor(frame)

    def _read_frame(self) -> Optional[_Frame]:
        read, image = self._camera.read()
        if not read:
            return None

        # Flipped for a natural selfie view, and RGB because that is what MediaPipe
        # reads. Never converted back: the original did that only to display it.
        image = cv2.cvtColor(cv2.flip(image, 1), cv2.COLOR_BGR2RGB)
        results = self._hands.process(image)
        found = results.multi_hand_landmarks
        return _Frame(width=image.shape[1], height=image.shape[0],
                      landmarks=found[0].landmark if found else None)

    def _apply_lockout(self) -> None:
        """Gesture Lockout: the hand controls nothing until the fist resolves.

        The cursor freezes but a held button stays held. Closing a hand into a
        Disable Fist necessarily passes through a Pinch on the way -- the fingers
        cross the touch threshold before they finish curling -- so a fist made
        during a drag arrives with the button already down. Releasing it here would
        drop whatever the user was carrying the instant their hand relaxed, four
        seconds before the Countdown even asks whether they meant it.

        Frozen and held, the drag simply waits: break the fist and it resumes where
        it was, or let the Countdown finish and `_release_tracking` cancels it.
        """
        self._cursor.set_active(False)
        # A fist holds the middle finger against the thumb, which is the scroll
        # gesture. Dropping the reference height stops the frame after the Lockout
        # lifts from scrolling by however far the hand moved inside it.
        self._previous_y = None

    def _on_hand_lost(self) -> None:
        self._cursor.set_active(False)
        # Same hazard the Lockout guards against: without this, a hand that leaves
        # mid-scroll and returns at a different height scrolls by the whole gap at once.
        self._previous_y = None

        if not self._cursor.is_pressed:
            return
        # A drag is not abandoned on the first missed frame. MediaPipe loses a fast
        # or motion-blurred hand for a frame or two routinely, and a hand moving
        # fast is exactly what a drag looks like -- cancelling on the first miss
        # would make dragging fail most often when it was working hardest. The
        # cursor is already frozen, so the wait costs nothing but the delay.
        self._lost_frames += 1
        if self._lost_frames >= self.MAX_DRAG_LOST_FRAMES:
            self._cursor.cancel_drag()

    def _drive_cursor(self, frame: _Frame) -> None:
        landmarks = frame.landmarks
        self._cursor.set_active(True)
        self._lost_frames = 0  # the hand is back, so any interrupted drag survives

        # The base of the ring finger barely moves as the fingers work, so the
        # cursor does not drift while pinching.
        ring_finger_mcp = landmarks[RING_MCP]
        mcp_x = int(ring_finger_mcp.x * frame.width)
        mcp_y = int(ring_finger_mcp.y * frame.height)
        margin_width, margin_height = tracking.calculate_margins(
            frame.width, frame.height, tracking.inner_area_percent)
        self._cursor.move_to(*tracking.convert_to_screen_coordinates(
            mcp_x, mcp_y, frame.width, frame.height, margin_width, margin_height))

        wrist = landmarks[WRIST]
        middle_tip = landmarks[MIDDLE_TIP]
        thumb_tip = landmarks[THUMB_TIP]
        index_tip = landmarks[INDEX_TIP]

        hand_size = tracking.get_landmark_distance(wrist, middle_tip)
        adaptive_threshold = tracking.touch_threshold * hand_size

        if tracking.get_landmark_distance(index_tip, thumb_tip) < adaptive_threshold:
            self._cursor.press()
        else:
            self._cursor.release()

        if tracking.get_landmark_distance(middle_tip, thumb_tip) < adaptive_threshold:
            self._scroll_by(middle_tip.y)
        else:
            self._previous_y = None

    def _scroll_by(self, middle_tip_y: float) -> None:
        if self._previous_y is not None:
            delta_y = middle_tip_y - self._previous_y
            if abs(delta_y) > tracking.scroll_threshold:
                _, screen_height = self._cursor.screen_size
                self._cursor.scroll(
                    delta_y * screen_height * tracking.scroll_sensitivity)
        self._previous_y = middle_tip_y

    # -- events ------------------------------------------------------------

    def _emit(self, line: str) -> None:
        with self._emit_lock:
            self._emit_line(line)

    def _emit_state(self) -> None:
        self._emit(protocol.state_event(self._tracking, self._camera is not None))

    def _emit_fist_update(self, update: FistUpdate) -> None:
        if update.cancelled:
            self._emit(protocol.countdown_cancelled_event())
        for seconds_left in update.ticks:
            self._emit(protocol.countdown_event(seconds_left))
