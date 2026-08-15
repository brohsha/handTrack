"""Drag behaviour: the event type, the accurate drop, and the interrupted drag.

Two halves, split by what they need.

The `tracking` half imports pyautogui, which is the one thing the other suites go
out of their way to avoid -- but the bug these tests pin is *which pyautogui call
happens*, so faking the module out of the picture entirely would leave nothing to
assert. The cost is about a third of a second on the whole run.

The `Engine` half needs no such thing: the Engine only ever reaches the cursor
through `CursorController`, so a recording stand-in covers it and stays instant.

The regression under guard is quiet. Posting a plain move while a button is held is
accepted by macOS without complaint and simply does nothing, so nothing here fails
loudly in production -- dragging just stops working. See ADR 0003.
"""

import threading

import pytest

from engine.handtrack_engine import engine as engine_module
from engine.handtrack_engine import tracking
from engine.handtrack_engine.engine import Engine, _Frame
from engine.handtrack_engine.tracking import (
    DRAG_CANCEL_MIN_TRAVEL,
    CursorController,
    CursorMovementThread,
)

# The canonical hand poses, borrowed rather than re-posed: a second set that
# drifted from the first would quietly test a different hand.
from engine.tests.test_gestures import OPEN_HAND, landmarks

START = (100.0, 100.0)


class FakePyAutoGUI:
    """Records what would have reached macOS, and moves nothing."""

    def __init__(self):
        self.calls = []
        self.cursor = START

    def position(self):
        return self.cursor

    def size(self):
        return (1512, 982)

    def moveTo(self, x, y, **kwargs):
        self.calls.append(("moveTo", x, y))

    def dragTo(self, x, y, **kwargs):
        self.calls.append(("dragTo", x, y, kwargs.get("mouseDownUp")))

    def mouseDown(self, **kwargs):
        self.calls.append(("mouseDown",))

    def mouseUp(self, **kwargs):
        self.calls.append(("mouseUp",))

    def press(self, key, **kwargs):
        self.calls.append(("press", key))

    @property
    def names(self):
        return [call[0] for call in self.calls]


@pytest.fixture
def fake(monkeypatch):
    stand_in = FakePyAutoGUI()
    monkeypatch.setattr(tracking, "pyautogui", stand_in)
    # The real screen size is cached on first use, so it is cleared here to stop a
    # value read from this fake outliving the test that installed it.
    monkeypatch.setattr(tracking, "_screen_size", None)
    return stand_in


@pytest.fixture
def movement(fake):
    """A real movement thread, constructed but never started.

    Started, it would post events on its own schedule and make every assertion
    below a race. Unstarted, the methods under test are still the real ones.
    """
    return CursorMovementThread()


@pytest.fixture
def controller(fake, movement):
    """A real controller wired to the unstarted thread, bypassing `start()`.

    `start()` exists to spawn threads, which is exactly what these tests must not
    do, so the one field it would set is set directly.
    """
    cursor = CursorController()
    cursor._movement = movement
    return cursor


class TestPostedEventType:
    """The bug itself: a held button must produce drag events, not moves."""

    def test_posts_a_move_when_no_button_is_held(self, fake, movement):
        movement._post(200, 300)
        assert fake.calls == [("moveTo", 200, 300)]

    def test_posts_a_drag_when_a_button_is_held(self, fake, movement):
        movement.dragging = True
        movement._post(200, 300)
        assert fake.names == ["dragTo"]

    def test_the_drag_never_touches_the_button_itself(self, fake, movement):
        """`mouseDownUp=False` matters: the default would press and release around
        every single frame of motion, clicking dozens of times across one drag."""
        movement.dragging = True
        movement._post(200, 300)
        assert fake.calls == [("dragTo", 200, 300, False)]


class TestSnapToTarget:
    """The accurate drop. The easing leaves the cursor resting short of the hand."""

    def test_closes_the_gap_the_easing_left(self, movement):
        movement.update_target(400.0, 500.0)
        movement.snap_to_target()
        assert (movement.current_x, movement.current_y) == (400.0, 500.0)

    def test_lands_the_final_event_at_the_target(self, fake, movement):
        movement.update_target(400.0, 500.0)
        movement.snap_to_target()
        assert fake.calls == [("moveTo", 400.0, 500.0)]

    def test_snaps_as_a_drag_while_a_button_is_held(self, fake, movement):
        movement.dragging = True
        movement.update_target(400.0, 500.0)
        movement.snap_to_target()
        assert fake.calls == [("dragTo", 400.0, 500.0, False)]


class TestPressAndRelease:
    def test_press_holds_the_button_and_starts_dragging(self, fake, controller,
                                                        movement):
        controller.press()
        assert fake.names == ["mouseDown"]
        assert controller.is_pressed
        assert movement.dragging

    def test_press_holds_the_button_before_arming_drag_events(self, fake, controller):
        """Arming first would let the movement thread post a drag with no button
        behind it -- an event macOS has no session to attach to."""
        posted = []
        controller._movement.dragging = False

        def record_down(**kwargs):
            posted.append(controller._movement.dragging)
            fake.calls.append(("mouseDown",))

        fake.mouseDown = record_down
        controller.press()
        assert posted == [False]

    def test_press_is_idempotent(self, fake, controller):
        controller.press()
        controller.press()
        assert fake.names.count("mouseDown") == 1

    def test_release_lands_on_target_before_lifting_the_button(self, fake, controller,
                                                               movement):
        controller.press()
        movement.update_target(400.0, 500.0)
        fake.calls.clear()

        controller.release()

        assert fake.calls == [("dragTo", 400.0, 500.0, False), ("mouseUp",)]

    def test_release_stops_dragging_before_lifting_the_button(self, fake, controller,
                                                              movement):
        controller.press()
        seen = []
        fake.mouseUp = lambda **kwargs: seen.append(movement.dragging)

        controller.release()

        assert seen == [False]

    def test_release_can_skip_the_snap(self, fake, controller, movement):
        controller.press()
        movement.update_target(400.0, 500.0)
        fake.calls.clear()

        controller.release(snap=False)

        assert fake.calls == [("mouseUp",)]

    def test_release_does_nothing_when_no_button_is_held(self, fake, controller):
        controller.release()
        assert fake.calls == []

    def test_release_clears_the_drag_state(self, controller, movement):
        controller.press()
        controller.release()
        assert not controller.is_pressed
        assert not movement.dragging


class TestCancelDrag:
    """An interrupted drag springs back rather than dropping somewhere arbitrary."""

    def _press_and_travel(self, controller, movement, distance):
        controller.press()
        movement.current_x += distance
        movement.update_target(movement.current_x, movement.current_y)

    def test_a_travelled_drag_is_cancelled_with_escape(self, fake, controller,
                                                       movement):
        self._press_and_travel(controller, movement, DRAG_CANCEL_MIN_TRAVEL + 5)
        fake.calls.clear()

        controller.cancel_drag()

        assert fake.calls == [("press", "escape"), ("mouseUp",)]

    def test_escape_is_sent_while_the_button_is_still_down(self, fake, controller,
                                                           movement):
        """Escape after the release would be a stray keystroke -- macOS only springs
        an item back if the drag session is still live when it arrives."""
        self._press_and_travel(controller, movement, DRAG_CANCEL_MIN_TRAVEL + 5)
        fake.calls.clear()

        controller.cancel_drag()

        assert fake.names.index("press") < fake.names.index("mouseUp")

    def test_a_click_that_never_travelled_gets_no_escape(self, fake, controller,
                                                         movement):
        """Hand lost mid-click. There is nothing to spring back, and Escape would
        land in whatever the user had open."""
        self._press_and_travel(controller, movement, DRAG_CANCEL_MIN_TRAVEL - 1)
        fake.calls.clear()

        controller.cancel_drag()

        assert fake.calls == [("mouseUp",)]

    def test_a_cancelled_drag_does_not_snap(self, fake, controller, movement):
        """The drop position is meaningless once the drag is being abandoned."""
        controller.press()
        movement.update_target(900.0, 900.0)
        fake.calls.clear()

        controller.cancel_drag()

        assert "dragTo" not in fake.names

    def test_cancelling_releases_the_button(self, controller, movement):
        self._press_and_travel(controller, movement, DRAG_CANCEL_MIN_TRAVEL + 5)
        controller.cancel_drag()
        assert not controller.is_pressed

    def test_cancelling_with_nothing_held_does_nothing(self, fake, controller):
        controller.cancel_drag()
        assert fake.calls == []


class TestButtonChangesAreAtomic:
    """The button and the event type must change as one unit.

    Found by smoke-testing the real controller against an event tap, not by
    reasoning: the first cut of this change left a window between `mouseDown` and
    arming drag events, and the movement thread posted a plain move straight into
    it -- the exact pattern that broke dragging to begin with.
    """

    def _lock_held_during(self, fake, movement, hook_name, action):
        """Whether the movement thread is shut out while `action` runs.

        Probed from another thread on purpose: the lock is re-entrant, so asking
        from this one would always succeed and prove nothing.
        """
        observed = []

        def probe():
            acquired = movement.button_lock.acquire(blocking=False)
            observed.append(acquired)
            if acquired:
                movement.button_lock.release()

        def hooked(*args, **kwargs):
            fake.calls.append((hook_name,))
            watcher = threading.Thread(target=probe)
            watcher.start()
            watcher.join()

        setattr(fake, hook_name, hooked)
        action()
        return observed

    def test_the_thread_is_shut_out_while_pressing(self, fake, controller, movement):
        observed = self._lock_held_during(
            fake, movement, "mouseDown", controller.press)
        assert observed == [False]

    def test_the_thread_is_shut_out_while_releasing(self, fake, controller, movement):
        controller.press()
        observed = self._lock_held_during(
            fake, movement, "mouseUp", controller.release)
        assert observed == [False]

    def test_the_thread_is_shut_out_while_cancelling(self, fake, controller, movement):
        controller.press()
        movement.current_x += DRAG_CANCEL_MIN_TRAVEL + 5
        observed = self._lock_held_during(
            fake, movement, "mouseUp", controller.cancel_drag)
        assert observed == [False]

    def test_posting_takes_the_same_lock(self, fake, movement):
        """Otherwise the exclusion above would be one-sided and prove nothing."""
        observed = []

        def probe_during_post(x, y, **kwargs):
            watcher = threading.Thread(
                target=lambda: observed.append(
                    movement.button_lock.acquire(blocking=False)))
            watcher.start()
            watcher.join()

        fake.moveTo = probe_during_post
        movement._post(1, 2)
        assert observed == [False]


class RecordingCursor:
    """Stands in for CursorController where only the calls matter."""

    def __init__(self, pressed=False):
        self.is_pressed = pressed
        self.calls = []

    def set_active(self, active):
        self.calls.append(("set_active", active))

    def release(self, snap=True):
        self.calls.append(("release", snap))
        self.is_pressed = False

    def cancel_drag(self):
        self.calls.append(("cancel_drag",))
        self.is_pressed = False

    def press(self):
        self.calls.append(("press",))
        self.is_pressed = True

    def move_to(self, x, y):
        self.calls.append(("move_to", x, y))

    def scroll(self, amount):
        self.calls.append(("scroll", amount))

    @property
    def screen_size(self):
        return (1512, 982)

    @property
    def names(self):
        return [call[0] for call in self.calls]


def engine_with(cursor):
    """An Engine wired to a stand-in cursor. Touches no camera and no MediaPipe."""
    engine = Engine(emit=lambda line: None)
    engine._cursor = cursor
    return engine


class TestLockoutHoldsTheDrag:
    """Closing a hand into a Disable Fist passes through a Pinch, so a fist made
    mid-drag arrives with the button already down."""

    def test_the_lockout_does_not_drop_what_is_being_dragged(self):
        cursor = RecordingCursor(pressed=True)
        engine_with(cursor)._apply_lockout()
        assert "release" not in cursor.names
        assert "cancel_drag" not in cursor.names
        assert cursor.is_pressed

    def test_the_lockout_still_freezes_the_cursor(self):
        cursor = RecordingCursor(pressed=True)
        engine_with(cursor)._apply_lockout()
        assert ("set_active", False) in cursor.calls

    def test_the_lockout_drops_the_scroll_reference(self):
        engine = engine_with(RecordingCursor(pressed=True))
        engine._previous_y = 0.5
        engine._apply_lockout()
        assert engine._previous_y is None


class TestHandLostMidDrag:
    def test_a_single_missed_frame_keeps_the_drag(self):
        """MediaPipe drops a fast or blurred hand routinely, and a hand moving fast
        is what a drag looks like."""
        cursor = RecordingCursor(pressed=True)
        engine_with(cursor)._on_hand_lost()
        assert "cancel_drag" not in cursor.names
        assert cursor.is_pressed

    def test_the_drag_survives_right_up_to_the_limit(self):
        cursor = RecordingCursor(pressed=True)
        engine = engine_with(cursor)
        for _ in range(Engine.MAX_DRAG_LOST_FRAMES - 1):
            engine._on_hand_lost()
        assert "cancel_drag" not in cursor.names

    def test_a_hand_that_stays_gone_cancels_the_drag(self):
        cursor = RecordingCursor(pressed=True)
        engine = engine_with(cursor)
        for _ in range(Engine.MAX_DRAG_LOST_FRAMES):
            engine._on_hand_lost()
        assert "cancel_drag" in cursor.names

    def test_the_cursor_freezes_from_the_first_missed_frame(self):
        cursor = RecordingCursor(pressed=True)
        engine_with(cursor)._on_hand_lost()
        assert ("set_active", False) in cursor.calls

    def test_no_button_held_means_nothing_to_cancel(self):
        cursor = RecordingCursor(pressed=False)
        engine = engine_with(cursor)
        for _ in range(Engine.MAX_DRAG_LOST_FRAMES * 2):
            engine._on_hand_lost()
        assert "cancel_drag" not in cursor.names

    def test_a_returning_hand_resets_the_allowance(self, fake, monkeypatch):
        """Otherwise a drag long enough to accumulate scattered single misses would
        die partway through for no reason the user could see.

        The recovery runs through the real `_drive_cursor`, so this fails if the
        reset is ever dropped from the frame path -- which is the only place the
        Engine learns the hand came back.
        """
        monkeypatch.setattr(engine_module, "tracking", tracking)
        cursor = RecordingCursor(pressed=True)
        engine = engine_with(cursor)
        seen_hand = _Frame(width=640, height=480, landmarks=landmarks(OPEN_HAND))

        for _ in range(Engine.MAX_DRAG_LOST_FRAMES - 1):
            engine._on_hand_lost()
        engine._drive_cursor(seen_hand)
        assert engine._lost_frames == 0

        for _ in range(Engine.MAX_DRAG_LOST_FRAMES - 1):
            engine._on_hand_lost()
        assert "cancel_drag" not in cursor.names
