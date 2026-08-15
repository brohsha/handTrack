"""Cursor movement and scrolling — ported unchanged from the original script.

This is the accuracy-critical half of the Engine. Every number below (the 0.7 inner
area, the 0.19 touch threshold, the `/12` movement step, the 0.95 scroll inertia) was
tuned empirically against a real hand and a real webcam, and the whole Shell/Engine
split exists so none of it ever has to be re-derived. The two thread classes, the
coordinate helpers and the constants are frozen: they are copied verbatim, comments
and all, and should be read as inherited rather than authored.

The port changed only *when* work happens. The original opened a camera, read the
screen size and started both threads at import time; here nothing touches hardware
until a caller constructs a `CursorController` and calls `start()`.
"""

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
                        pyautogui.moveTo(self.current_x, self.current_y, _pause=False)
                time.sleep(0)
            else:
                time.sleep(0.1)

    def update_target(self, x, y):
        self.target_x, self.target_y = x, y

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
    are idempotent and `is_pressed` is readable so that entering Gesture Lockout can
    be expressed as `set_active(False)` plus `release()` without the caller having to
    remember whether a button is down.
    """

    def __init__(self):
        self._movement = None
        self._scroll = None
        self._pressed = False

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
        """
        self.release()

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

    def press(self) -> None:
        if not self._pressed:
            pyautogui.mouseDown()
            self._pressed = True

    def release(self) -> None:
        if self._pressed:
            pyautogui.mouseUp()
            self._pressed = False

    def scroll(self, amount) -> None:
        """Queue a scroll. Dropped rather than buffered when not started, so a
        scroll gesture cannot fire after Hard Off."""
        if self._scroll is not None:
            self._scroll.add_scroll(amount)
