"""Cursor movement and scrolling — ported unchanged from the original script.

This is the accuracy-critical half of the Engine. Every number below (the 0.7 inner
area, the 0.19 touch threshold, the `/12` movement step, the 0.95 scroll inertia) was
tuned empirically against a real hand and a real webcam, and the whole Shell/Engine
split exists so none of it ever has to be re-derived. The coordinate helpers, the
constants and `ScrollThread` are frozen: they are copied verbatim, comments and all,
and should be read as inherited rather than authored.

`CursorMovementThread` is the one exception. Its tuning is untouched, but *which
kind of event* it posts is not inherited: the original always posted a plain move,
which silently broke dragging. See `_post` and ADR 0003.

The port changed only *when* work happens. The original opened a camera, read the
screen size and started both threads at import time; here nothing touches hardware
until a caller constructs a `CursorController` and calls `start()`.
"""

import contextlib
import threading
import time
from typing import Tuple

import numpy as np
import pyautogui

# Load-bearing, not housekeeping. PAUSE must be 0 because mouseDown/mouseUp/scroll
# below do not pass `_pause=False` themselves, and FAILSAFE must be off or a cursor
# driven into a screen corner raises instead of clamping.
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

# Define the portion of the camera view to map to the full screen (70% here)
inner_area_percent = 0.7

touch_threshold = 0.19
scroll_threshold = 0.005  # Smaller threshold for finer detection
scroll_sensitivity = 0.05  # Adjust this value for scrolling speed

#: How long stop() waits for each thread to notice it should exit. Bounded by the
#: threads' own sleeps (0.1s and 0.005s), so this is slack, not a real delay.
_JOIN_TIMEOUT = 0.5

#: How far the cursor must have travelled since the button went down before an
#: abandoned press is treated as a drag worth cancelling with Escape. Below this
#: the press was a click, and Escape would be a stray keystroke into whatever the
#: user had open -- enough to dismiss a dialog they were part-way through. Set well
#: clear of the movement thread's own ~5px resting slack.
DRAG_CANCEL_MIN_TRAVEL = 10

_screen_size = None


def get_screen_size():
    """Screen size, read once on first use and then cached.

    Lazy on purpose: the original read this at import, which meant importing the
    module talked to the window server. Caching keeps the original's cost — one
    lookup per process rather than one per frame.
    """
    global _screen_size
    if _screen_size is None:
        _screen_size = tuple(pyautogui.size())
    return _screen_size


# Calculate the margins around the inner area
def calculate_margins(frame_width, frame_height, inner_area_percent):
    margin_width = frame_width * (1 - inner_area_percent) / 2
    margin_height = frame_height * (1 - inner_area_percent) / 2
    return margin_width, margin_height


# Convert video coordinates to screen coordinates
def convert_to_screen_coordinates(x, y, frame_width, frame_height, margin_width, margin_height):
    screen_width, screen_height = get_screen_size()
    screen_x = np.interp(x, (margin_width, frame_width - margin_width), (0, screen_width))
    screen_y = np.interp(y, (margin_height, frame_height - margin_height), (0, screen_height))
    return screen_x, screen_y


# Function to get distance between two landmarks
def get_landmark_distance(landmark1, landmark2):
    x1, y1 = landmark1.x, landmark1.y
    x2, y2 = landmark2.x, landmark2.y
    distance = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
    return distance


# Movement Thread for smoother cursor movement
class CursorMovementThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.current_x, self.current_y = pyautogui.position()
        self.target_x, self.target_y = self.current_x, self.current_y
        self.running = True
        self.active = False
        self.jitter_threshold = 0.003
        #: True while a mouse button is held, so `_post` sends drag events instead
        #: of move events. Owned by CursorController.
        self.dragging = False
        #: Held while posting, and by CursorController while it presses or releases,
        #: so the button and `dragging` change as one unit. Without it this thread
        #: can post between the two and slip a plain move under a held button --
        #: which is precisely the event pattern that broke dragging in the first
        #: place. Re-entrant because releasing snaps inside the same hold.
        self.button_lock = threading.RLock()

    def _post(self, x, y):
        """Put the cursor at (x, y), as a drag if a button is held.

        macOS routes the two to different handlers: an app reads `mouseDragged`
        to follow a drag and ignores `mouseMoved` for that purpose. Posting a move
        with the button down is what made dragging look broken -- the app saw the
        press, then never heard that anything had moved.
        """
        with self.button_lock:
            if self.dragging:
                pyautogui.dragTo(x, y, button="left", _pause=False, mouseDownUp=False)
            else:
                pyautogui.moveTo(x, y, _pause=False)

    def run(self):
        screen_width, screen_height = get_screen_size()
        while self.running:
            if self.active:
                distance = np.hypot(self.target_x - self.current_x, self.target_y - self.current_y)
                screen_diagonal = np.hypot(screen_width, screen_height)
                if distance / screen_diagonal > self.jitter_threshold:
                    step = max(0.0001, distance / 12)  # Smoother movement
                    if distance != 0:
                        step_x = (self.target_x - self.current_x) / distance * step
                        step_y = (self.target_y - self.current_y) / distance * step
                        self.current_x += step_x
                        self.current_y += step_y
                        self._post(self.current_x, self.current_y)
                time.sleep(0)
            else:
                time.sleep(0.1)

    def update_target(self, x, y):
        self.target_x, self.target_y = x, y

    def snap_to_target(self):
        """Jump the last of the way to the target, skipping the easing.

        The loop above closes only a twelfth of the remaining gap per pass and
        stops once inside `jitter_threshold`, so the cursor habitually rests a few
        pixels short of the hand. Harmless while moving -- the next frame moves the
        target anyway -- but a drop lands where the cursor *is*, so without this the
        item is released slightly behind where the user aimed.
        """
        self.current_x, self.current_y = self.target_x, self.target_y
        self._post(self.current_x, self.current_y)

    def activate(self):
        self.active = True

    def deactivate(self):
        self.active = False

    def stop(self):
        self.running = False


# Scrolling Thread for smooth scrolling with inertia
class ScrollThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.scroll_queue = []
        self.scroll_lock = threading.Lock()
        self.running = True
        self.inertia = 0.95  # Slower reduction for rolling stop effect
        self.scroll_step = 0.01  # Smaller step for smoother scroll
        self.inertia_threshold = 0.01  # Minimum inertia scroll amount

    def run(self):
        while self.running:
            if self.scroll_queue:
                with self.scroll_lock:
                    scroll_amount = self.scroll_queue.pop(0)
                pyautogui.scroll(scroll_amount)
                # Apply inertia effect if the queue is empty
                if len(self.scroll_queue) == 0 and abs(scroll_amount) > self.inertia_threshold:
                    scroll_amount *= self.inertia
                    if abs(scroll_amount) > self.scroll_step:
                        with self.scroll_lock:
                            self.scroll_queue.append(scroll_amount)
            time.sleep(0.005)  # Increased frequency for smoother processing

    def add_scroll(self, scroll_amount):
        with self.scroll_lock:
            self.scroll_queue.append(scroll_amount)

    def stop(self):
        self.running = False


class CursorController:
    """Owns the movement and scroll threads and the mouse button state.

    The Engine drives this; it never touches pyautogui directly. `press`/`release`
    are idempotent and `is_pressed` is readable, so a caller can freeze or end a
    drag without having to remember whether a button is down.

    A held button is a drag. There is no separate drag mode to enter: `press` puts
    the movement thread into drag events and `release` takes it out, which is what
    lets a quick pinch read to macOS as a click and a pinch-move-release read as a
    drag, exactly as a real mouse does.
    """

    def __init__(self):
        self._movement = None
        self._scroll = None
        self._pressed = False
        #: Where the cursor was when the button went down, to tell a drag from a
        #: click if the press has to be abandoned. None whenever nothing is held.
        self._press_origin = None

    def probe_control(self) -> bool:
        """Nudge the cursor one pixel and check it actually moved.

        Accessibility denial is silent: pyautogui accepts the move, returns cleanly,
        and nothing happens. Without reading the position back there is no way to
        tell working cursor control from none, so Tracking would report itself
        healthy while doing nothing at all.

        One pixel, and put back afterwards, so proving it costs the user nothing
        they can see.
        """
        start_x, start_y = pyautogui.position()
        pyautogui.moveTo(start_x + 1, start_y, _pause=False)
        landed = pyautogui.position() != (start_x, start_y)
        pyautogui.moveTo(start_x, start_y, _pause=False)
        return landed

    @property
    def is_pressed(self) -> bool:
        return self._pressed

    @property
    def screen_size(self) -> Tuple[int, int]:
        return get_screen_size()

    def start(self) -> None:
        """Start both threads. A no-op if already started.

        The threads are constructed here rather than in `__init__` because a
        `threading.Thread` cannot be restarted, and the Engine cycles between
        Tracking and Hard Off many times within one process. Rebuilding the
        movement thread also re-seeds it from the cursor's real position, so
        returning to Tracking never resumes from a stale target.
        """
        if self._movement is not None:
            return
        self._movement = CursorMovementThread()
        self._scroll = ScrollThread()
        self._movement.start()
        self._scroll.start()

    def stop(self) -> None:
        """Stop both threads and release any held button. Safe to call twice.

        Releasing first, and joining afterwards, is what stops Hard Off from leaving
        a button stuck down or letting one last queued move land after the user has
        turned tracking off.

        No snap: this is the safety net, not the drop. The Engine cancels a live
        drag before it gets here, so anything still held at this point is being
        abandoned and there is no target worth landing on.
        """
        self.release(snap=False)

        movement, self._movement = self._movement, None
        scroll, self._scroll = self._scroll, None

        if movement is not None:
            movement.stop()
        if scroll is not None:
            scroll.stop()
        if movement is not None:
            movement.join(timeout=_JOIN_TIMEOUT)
        if scroll is not None:
            scroll.join(timeout=_JOIN_TIMEOUT)

    def move_to(self, x, y) -> None:
        """Set the movement target in screen coordinates."""
        if self._movement is not None:
            self._movement.update_target(x, y)

    def set_active(self, active: bool) -> None:
        """Let the hand drive the cursor, or freeze it where it is."""
        if self._movement is None:
            return
        if active:
            self._movement.activate()
        else:
            self._movement.deactivate()

    def _button_change(self):
        """Exclude the movement thread while the button and `dragging` both change.

        The two must move together. Left unguarded, the thread can post between them
        and emit a plain move under a held button, or a drag event with no button
        behind it. A no-op before `start()`, when there is no thread to exclude.
        """
        if self._movement is None:
            return contextlib.nullcontext()
        return self._movement.button_lock

    def press(self) -> None:
        """Hold the button down, and start posting drag events."""
        if self._pressed:
            return
        with self._button_change():
            pyautogui.mouseDown()
            self._pressed = True
            if self._movement is not None:
                self._press_origin = (self._movement.current_x,
                                      self._movement.current_y)
                self._movement.dragging = True

    def release(self, snap: bool = True) -> None:
        """Let the button up, landing it on the target rather than short of it.

        `snap` is the accurate drop; pass False when the position no longer matters
        because the drag is being abandoned or the Engine is shutting down.

        Snap, stop dragging, then lift -- all inside one hold, so the final drag
        event and the button coming up cannot have anything posted between them.

        `pyautogui.mouseUp` posts a move to the current position before the up,
        inside its own call, so one plain move always trails a drag no matter what
        this holds. It is a positional no-op landing exactly where the snap did,
        after the app has already had the whole run of drag events, so it is left
        alone rather than reached around with a private platform call.
        """
        if not self._pressed:
            return
        with self._button_change():
            if self._movement is not None:
                if snap:
                    self._movement.snap_to_target()
                self._movement.dragging = False
            pyautogui.mouseUp()
            self._pressed = False
            self._press_origin = None

    def cancel_drag(self) -> None:
        """Abandon a held button instead of dropping what it was carrying.

        Escape while the button is still down makes macOS spring a dragged item
        back where it came from, so an interrupted drag undoes itself rather than
        filing a document somewhere the user never chose. Only for genuine drags:
        a press that never travelled was a click, and Escape would leak into
        whatever the user had on screen.

        Escape cancels drag-and-drop sessions only. A text selection or a dragged
        slider stops where it is, which is still better than a wrong drop.
        """
        if not self._pressed:
            return
        # One hold across both, so no drag event lands between the cancel and the
        # button coming up.
        with self._button_change():
            if self._travelled_since_press() >= DRAG_CANCEL_MIN_TRAVEL:
                pyautogui.press("escape")
            self.release(snap=False)

    def _travelled_since_press(self) -> float:
        """How far the cursor has moved since the button went down."""
        if self._press_origin is None or self._movement is None:
            return 0.0
        origin_x, origin_y = self._press_origin
        return float(np.hypot(self._movement.current_x - origin_x,
                              self._movement.current_y - origin_y))

    def scroll(self, amount) -> None:
        """Queue a scroll. Dropped rather than buffered when not started, so a
        scroll gesture cannot fire after Hard Off."""
        if self._scroll is not None:
            self._scroll.add_scroll(amount)
