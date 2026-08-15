"""Timing tests for the Disable Fist state machine.

No camera, no MediaPipe, no cursor. The state machine is pure timing, so it takes
an injected clock and is driven here by synthetic frames — which is also why
`engine.py` defers cv2, mediapipe and pyautogui to startup rather than importing
them at module scope. This suite is the reason for that, and would slow from
milliseconds to seconds if it changed.

Frames arrive at intervals that do not divide a tick evenly, because the real frame
rate varies and the machine must not depend on landing exactly on a second.
"""

import pytest

from engine.handtrack_engine.engine import (
    COUNTDOWN_FROM,
    DWELL_SECONDS,
    GRACE_PERIOD_SECONDS,
    TICK_SECONDS,
    DisableFistState,
    FistState,
    FistUpdate,
)

FRAME = 0.05  # 20fps

#: A fist must survive the Dwell and the whole Countdown before Tracking stops.
TIME_TO_DISABLE = DWELL_SECONDS + COUNTDOWN_FROM * TICK_SECONDS

#: Every tick of an uninterrupted Countdown, and the second it should land on.
FULL_COUNTDOWN = [(4, 1.0), (3, 2.0), (2, 3.0), (1, 4.0), (0, 5.0)]


class FakeClock:
    """A clock that only moves when a test moves it."""

    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        # Rounded because a hundred additions of 0.05 otherwise drift by enough to
        # land on the wrong side of a tick boundary, which would make a timing
        # assertion depend on floating point rather than on the machine under test.
        self.now = round(self.now + seconds, 9)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def timer(clock):
    """A timer already past its Grace Period, which most tests are not about."""
    timer = DisableFistState(clock=clock)
    timer.tracking_started()
    clock.advance(GRACE_PERIOD_SECONDS + FRAME)
    return timer


def frames(timer, clock, seconds, fist=True, frame=FRAME):
    """Yield (elapsed, update) per frame of a gesture held for `seconds`.

    The first frame lands at the instant the gesture starts, so `seconds` reads as
    the length of the hold rather than of the wait after it.
    """
    start = clock.now
    yield clock.now - start, timer.update(fist)
    for _ in range(int(round(seconds / frame))):
        clock.advance(frame)
        yield clock.now - start, timer.update(fist)


def hold(timer, clock, seconds, fist=True, frame=FRAME):
    return [update for _, update in frames(timer, clock, seconds, fist, frame)]


def tick_times(timer, clock, seconds, frame=FRAME):
    """Every Countdown tick and when it landed, in seconds since the fist began."""
    return [(tick, round(elapsed, 6))
            for elapsed, update in frames(timer, clock, seconds, frame=frame)
            for tick in update.ticks]


def ticks(updates):
    return [tick for update in updates for tick in update.ticks]


def cancellations(updates):
    return [update for update in updates if update.cancelled]


class TestHoldingThroughToDisable:
    def test_counts_four_to_zero_one_second_apart_after_the_dwell(self, timer, clock):
        assert tick_times(timer, clock, TIME_TO_DISABLE) == FULL_COUNTDOWN

    def test_the_last_tick_is_the_one_that_disables_tracking(self, timer, clock):
        updates = hold(timer, clock, TIME_TO_DISABLE)
        disabling = [update for update in updates if update.disable]
        assert len(disabling) == 1
        assert disabling[0].ticks[-1] == 0

    def test_nothing_is_cancelled_along_the_way(self, timer, clock):
        assert cancellations(hold(timer, clock, TIME_TO_DISABLE)) == []

    def test_the_state_resets_itself_once_it_has_disabled(self, timer, clock):
        hold(timer, clock, TIME_TO_DISABLE)
        assert timer.state is FistState.IDLE
        assert not timer.locked_out

    def test_the_disable_does_not_repeat_on_the_next_frame(self, timer, clock):
        hold(timer, clock, TIME_TO_DISABLE)
        assert not timer.update(True).disable


class TestTheDwell:
    def test_a_fist_shorter_than_the_dwell_never_counts_down(self, timer, clock):
        assert ticks(hold(timer, clock, DWELL_SECONDS - FRAME)) == []

    def test_the_first_tick_lands_exactly_when_the_dwell_completes(self, timer, clock):
        assert tick_times(timer, clock, DWELL_SECONDS) == [(4, DWELL_SECONDS)]

    def test_breaking_during_the_dwell_cancels_without_ever_counting_down(self, timer, clock):
        hold(timer, clock, DWELL_SECONDS - FRAME)

        updates = hold(timer, clock, TIME_TO_DISABLE, fist=False)

        assert ticks(updates) == []
        assert not any(update.disable for update in updates)
        assert len(cancellations(updates)) == 1

    def test_a_fist_broken_during_the_dwell_must_serve_the_dwell_again(self, timer, clock):
        hold(timer, clock, DWELL_SECONDS - FRAME)
        hold(timer, clock, FRAME, fist=False)
        assert tick_times(timer, clock, TIME_TO_DISABLE) == FULL_COUNTDOWN


class TestBreakingTheCountdown:
    def test_opening_the_hand_mid_countdown_cancels_immediately(self, timer, clock):
        hold(timer, clock, DWELL_SECONDS + 2 * TICK_SECONDS)

        updates = hold(timer, clock, FRAME, fist=False)

        assert updates[0].cancelled
        assert len(cancellations(updates)) == 1

    def test_losing_the_hand_mid_countdown_cancels_immediately(self, timer, clock):
        """A hand that leaves the frame reports no fist, and must read as a break."""
        hold(timer, clock, DWELL_SECONDS + 2 * TICK_SECONDS)
        assert timer.update(False).cancelled

    def test_cancelling_resets_the_countdown_to_zero(self, timer, clock):
        hold(timer, clock, DWELL_SECONDS + 2 * TICK_SECONDS)
        hold(timer, clock, FRAME, fist=False)
        assert timer.state is FistState.IDLE
        assert not timer.locked_out

    def test_re_fisting_restarts_at_four_rather_than_where_it_stopped(self, timer, clock):
        assert ticks(hold(timer, clock, DWELL_SECONDS + 2 * TICK_SECONDS)) == [4, 3, 2]
        hold(timer, clock, FRAME, fist=False)
        assert tick_times(timer, clock, TIME_TO_DISABLE) == FULL_COUNTDOWN

    def test_a_cancelled_countdown_is_announced_once_not_every_frame(self, timer, clock):
        hold(timer, clock, DWELL_SECONDS + TICK_SECONDS)
        assert len(cancellations(hold(timer, clock, 2.0, fist=False))) == 1


class TestGestureLockout:
    def test_engages_on_the_very_first_fist_frame(self, timer):
        assert not timer.locked_out
        timer.update(True)
        assert timer.locked_out

    def test_is_already_held_throughout_the_dwell_before_any_tick(self, timer, clock):
        """The whole reason the state machine exists: a fist reads as an index Pinch,
        so the hand must stop controlling anything a second before the Countdown even
        starts, rather than holding the mouse button down and dragging."""
        updates = hold(timer, clock, DWELL_SECONDS - FRAME)

        assert ticks(updates) == []
        assert timer.state is FistState.DWELLING
        assert timer.locked_out

    def test_stays_held_for_the_whole_countdown(self, timer, clock):
        for _, update in frames(timer, clock, TIME_TO_DISABLE - FRAME):
            assert timer.locked_out
            assert not update.disable

    def test_lifts_when_the_fist_breaks_during_the_dwell(self, timer, clock):
        hold(timer, clock, DWELL_SECONDS - FRAME)
        timer.update(False)
        assert not timer.locked_out

    def test_lifts_when_the_fist_breaks_mid_countdown(self, timer, clock):
        hold(timer, clock, DWELL_SECONDS + TICK_SECONDS)
        timer.update(False)
        assert not timer.locked_out

    def test_never_engages_for_an_open_hand(self, timer, clock):
        hold(timer, clock, TIME_TO_DISABLE, fist=False)
        assert not timer.locked_out


class TestGracePeriod:
    def test_a_fist_held_from_the_moment_tracking_starts_is_ignored(self, clock):
        timer = DisableFistState(clock=clock)
        timer.tracking_started()

        updates = hold(timer, clock, GRACE_PERIOD_SECONDS - FRAME)

        assert ticks(updates) == []
        assert timer.state is FistState.IDLE
        assert not timer.locked_out

    def test_a_hand_still_closed_after_a_shutdown_cannot_immediately_retrigger(
            self, timer, clock):
        assert hold(timer, clock, TIME_TO_DISABLE)[-1].disable

        timer.tracking_started()  # the Shell turned Tracking back on
        updates = hold(timer, clock, GRACE_PERIOD_SECONDS - FRAME)  # fist never opened

        assert ticks(updates) == []
        assert not any(update.disable for update in updates)
        assert not timer.locked_out

    def test_a_fist_still_held_when_the_grace_period_ends_counts_down_from_there(
            self, clock):
        timer = DisableFistState(clock=clock)
        timer.tracking_started()
        hold(timer, clock, GRACE_PERIOD_SECONDS - FRAME)

        first_tick, landed_at = tick_times(timer, clock, TIME_TO_DISABLE)[0]

        assert first_tick == COUNTDOWN_FROM
        assert landed_at == DWELL_SECONDS + FRAME  # one frame to clear the Grace Period

    def test_the_grace_period_only_reopens_when_tracking_starts_again(self, timer, clock):
        hold(timer, clock, GRACE_PERIOD_SECONDS, fist=False)
        assert tick_times(timer, clock, TIME_TO_DISABLE) == FULL_COUNTDOWN


class TestVaryingFrameRate:
    @pytest.mark.parametrize("frame", [0.02, 0.05, 0.13])
    def test_the_countdown_keeps_its_own_time_whatever_the_frame_rate(
            self, timer, clock, frame):
        stamps = tick_times(timer, clock, TIME_TO_DISABLE + frame, frame=frame)

        assert [tick for tick, _ in stamps] == [4, 3, 2, 1, 0]
        for tick, landed_at in stamps:
            due_at = DWELL_SECONDS + (COUNTDOWN_FROM - tick) * TICK_SECONDS
            assert due_at <= landed_at < due_at + frame

    def test_one_slow_frame_emits_every_tick_that_came_due(self, timer, clock):
        timer.update(True)
        clock.advance(DWELL_SECONDS + 2 * TICK_SECONDS)
        assert timer.update(True).ticks == (4, 3, 2)

    def test_a_frame_slow_enough_to_overshoot_still_reports_zero_and_disables(
            self, timer, clock):
        """`seconds_left: 0` is what the Shell reads before the state event, so a
        stalled frame must not be allowed to skip past it."""
        timer.update(True)
        clock.advance(TIME_TO_DISABLE + 10.0)

        update = timer.update(True)

        assert update.ticks == (4, 3, 2, 1, 0)
        assert update.disable

    def test_two_frames_at_the_same_instant_do_not_repeat_a_tick(self, timer, clock):
        hold(timer, clock, DWELL_SECONDS)
        assert timer.update(True).ticks == ()


class TestQuietFrames:
    def test_an_open_hand_produces_no_events_at_all(self, timer, clock):
        assert all(update == FistUpdate()
                   for update in hold(timer, clock, 2.0, fist=False))

    def test_a_fist_inside_the_dwell_produces_no_events(self, timer, clock):
        assert all(update == FistUpdate()
                   for update in hold(timer, clock, DWELL_SECONDS - FRAME))


class TestReset:
    def test_clears_a_running_countdown_and_the_lockout(self, timer, clock):
        hold(timer, clock, DWELL_SECONDS + TICK_SECONDS)
        timer.reset()
        assert timer.state is FistState.IDLE
        assert not timer.locked_out

    def test_leaves_nothing_behind_for_the_next_fist(self, timer, clock):
        hold(timer, clock, DWELL_SECONDS + 2 * TICK_SECONDS)
        timer.reset()
        assert tick_times(timer, clock, TIME_TO_DISABLE) == FULL_COUNTDOWN

    def test_a_reset_countdown_is_not_announced_as_cancelled(self, timer, clock):
        """Reset is the Engine tidying up after itself; only the hand cancels."""
        hold(timer, clock, DWELL_SECONDS + TICK_SECONDS)
        timer.reset()
        assert not timer.update(False).cancelled
