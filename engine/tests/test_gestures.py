"""Geometry tests for Disable Fist recognition.

MediaPipe is never imported: every rule under test is pure geometry, and loading
the landmark model would cost seconds on a suite that should run in milliseconds.

Poses are written in a hand frame where one unit is a palm_span, `u` runs toward
the little finger and `v` runs toward the fingertips. `landmarks()` maps that into
MediaPipe's image frame, where y grows downward, so a pose reads the way a hand
looks rather than the way a camera stores it.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

# The Engine package is not installed, so put its directory on the path rather
# than depend on how pytest happens to be invoked.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handtrack_engine.gestures import (  # noqa: E402
    FINGERS,
    INDEX_MCP,
    INDEX_PIP,
    INDEX_TIP,
    MIDDLE_MCP,
    MIDDLE_PIP,
    MIDDLE_TIP,
    PINKY_MCP,
    PINKY_PIP,
    PINKY_TIP,
    RING_MCP,
    RING_PIP,
    RING_TIP,
    THUMB_TIP,
    WRIST,
    distance_2d,
    is_disable_fist,
    is_finger_curled,
    is_thumb_folded,
    palm_span,
)

LANDMARK_COUNT = 21
PALM_SPAN = 0.25  # a hand at a comfortable working distance from the webcam
WRIST_AT = (0.5, 0.85)


@dataclass
class Point:
    """Stands in for a MediaPipe landmark. `z` is carried so tests can prove it
    changes nothing."""
    x: float
    y: float
    z: float = 0.0


def landmarks(pose, scale=PALM_SPAN, origin=WRIST_AT):
    """Build a full 21-landmark hand from a partial hand-frame pose.

    Landmarks the pose omits sit on the wrist: no rule reads them, and a bogus
    value there would be noticed rather than quietly change a verdict.
    """
    origin_x, origin_y = origin
    points = [Point(origin_x, origin_y) for _ in range(LANDMARK_COUNT)]
    for index, (u, v, *depth) in pose.items():
        points[index] = Point(origin_x + u * scale, origin_y - v * scale,
                              depth[0] if depth else 0.0)
    return points


def at_depth(fragment, z):
    """The same pose in the image plane, at a different depth."""
    return {index: (u, v, z) for index, (u, v, *_) in fragment.items()}


KNUCKLES = {
    WRIST: (0.00, 0.00),
    INDEX_MCP: (-0.28, 0.93),
    MIDDLE_MCP: (0.00, 1.00),
    RING_MCP: (0.24, 0.95),
    PINKY_MCP: (0.46, 0.85),
}

#: Fingertips beyond their own middle joints.
EXTENDED_FINGERS = {
    INDEX_PIP: (-0.32, 1.30), INDEX_TIP: (-0.35, 1.72),
    MIDDLE_PIP: (0.00, 1.40), MIDDLE_TIP: (0.00, 1.85),
    RING_PIP: (0.27, 1.33), RING_TIP: (0.29, 1.75),
    PINKY_PIP: (0.52, 1.15), PINKY_TIP: (0.56, 1.48),
}

#: Fingertips folded back into the palm, knuckles bulging past them.
CURLED_FINGERS = {
    INDEX_PIP: (-0.30, 1.22), INDEX_TIP: (-0.22, 0.78),
    MIDDLE_PIP: (0.00, 1.28), MIDDLE_TIP: (0.02, 0.80),
    RING_PIP: (0.26, 1.22), RING_TIP: (0.24, 0.78),
    PINKY_PIP: (0.50, 1.10), PINKY_TIP: (0.44, 0.72),
}

OPEN_HAND = {**KNUCKLES, **EXTENDED_FINGERS, THUMB_TIP: (-0.82, 0.62)}

# The two accepted Disable Fists. They sit in almost the same place in the image
# and on opposite sides of the fingers in depth, which is the whole point.
FIST_THUMB_OVER = {**KNUCKLES, **at_depth(CURLED_FINGERS, 0.20),
                   THUMB_TIP: (0.05, 0.95, -0.30)}
FIST_THUMB_UNDER = {**KNUCKLES, **at_depth(CURLED_FINGERS, -0.20),
                    THUMB_TIP: (0.02, 0.88, 0.35)}

# Index flexed hard enough to read as curled, so the pinch is rejected on the
# three fingers still out rather than on an accident of the index.
PINCH = {**KNUCKLES, **EXTENDED_FINGERS,
         INDEX_PIP: (-0.34, 1.28), INDEX_TIP: (-0.55, 1.05),
         THUMB_TIP: (-0.58, 1.02)}

THUMBS_UP = {**KNUCKLES, **CURLED_FINGERS, THUMB_TIP: (-0.45, 1.45)}

FINGER_NAMES = ("index", "middle", "ring", "little")


class TestDistance2d:
    def test_is_plain_pythagoras(self):
        assert distance_2d(Point(0.0, 0.0), Point(0.3, 0.4)) == pytest.approx(0.5)

    def test_ignores_depth(self):
        near = Point(0.0, 0.0, -5.0)
        far = Point(0.3, 0.4, 5.0)
        assert distance_2d(near, far) == pytest.approx(0.5)

    def test_is_zero_for_a_point_against_itself(self):
        assert distance_2d(Point(0.7, 0.2), Point(0.7, 0.2)) == pytest.approx(0.0)


class TestPalmSpan:
    def test_measures_wrist_to_middle_knuckle(self):
        assert palm_span(landmarks(OPEN_HAND)) == pytest.approx(PALM_SPAN)

    def test_barely_moves_when_the_hand_closes(self):
        assert palm_span(landmarks(FIST_THUMB_OVER)) == pytest.approx(
            palm_span(landmarks(OPEN_HAND)))

    def test_halves_with_the_hand(self):
        assert palm_span(landmarks(OPEN_HAND, scale=PALM_SPAN / 2)) == pytest.approx(
            PALM_SPAN / 2)


class TestIsFingerCurled:
    @pytest.mark.parametrize("tip, pip", FINGERS, ids=FINGER_NAMES)
    def test_curled_fingers_are_curled(self, tip, pip):
        assert is_finger_curled(landmarks(FIST_THUMB_OVER), tip, pip)

    @pytest.mark.parametrize("tip, pip", FINGERS, ids=FINGER_NAMES)
    def test_extended_fingers_are_not(self, tip, pip):
        assert not is_finger_curled(landmarks(OPEN_HAND), tip, pip)

    def test_holds_at_any_hand_size(self):
        far_away = landmarks(FIST_THUMB_OVER, scale=PALM_SPAN / 4)
        assert is_finger_curled(far_away, INDEX_TIP, INDEX_PIP)


class TestIsThumbFolded:
    def test_thumb_over_the_fingers_is_folded(self):
        assert is_thumb_folded(landmarks(FIST_THUMB_OVER))

    def test_thumb_tucked_under_the_fingers_is_folded(self):
        assert is_thumb_folded(landmarks(FIST_THUMB_UNDER))

    def test_an_open_hands_thumb_sticks_out(self):
        assert not is_thumb_folded(landmarks(OPEN_HAND))

    def test_a_thumbs_up_thumb_sticks_out(self):
        assert not is_thumb_folded(landmarks(THUMBS_UP))


class TestIsDisableFist:
    def test_an_open_flat_hand_is_not_a_disable_fist(self):
        assert not is_disable_fist(landmarks(OPEN_HAND))

    def test_a_fist_with_the_thumb_over_the_fingers_is_a_disable_fist(self):
        assert is_disable_fist(landmarks(FIST_THUMB_OVER))

    def test_a_fist_with_the_thumb_tucked_under_is_a_disable_fist(self):
        assert is_disable_fist(landmarks(FIST_THUMB_UNDER))

    def test_thumb_over_and_thumb_under_agree(self):
        """The two grips differ mostly in depth, so they must not differ in verdict."""
        over = landmarks(FIST_THUMB_OVER)
        under = landmarks(FIST_THUMB_UNDER)
        assert min(point.z for point in over) < -0.2 < 0.2 < max(point.z for point in under)
        assert is_disable_fist(over) == is_disable_fist(under)

    def test_depth_alone_never_changes_the_verdict(self):
        flat = landmarks(FIST_THUMB_OVER)
        contorted = [Point(point.x, point.y, 3.0 * index - 30.0)
                     for index, point in enumerate(flat)]
        assert is_disable_fist(contorted) == is_disable_fist(flat)

    def test_a_pinch_is_not_a_disable_fist(self):
        assert not is_disable_fist(landmarks(PINCH))

    def test_a_pinch_is_rejected_on_its_extended_fingers(self):
        """A Pinch brings the thumb in against the hand, so only the middle, ring
        and little fingers keep it from reading as a fist and clicking."""
        pinch = landmarks(PINCH)
        assert is_thumb_folded(pinch)
        assert not is_finger_curled(pinch, MIDDLE_TIP, MIDDLE_PIP)
        assert not is_finger_curled(pinch, RING_TIP, RING_PIP)
        assert not is_finger_curled(pinch, PINKY_TIP, PINKY_PIP)

    def test_a_thumbs_up_is_not_a_disable_fist(self):
        assert not is_disable_fist(landmarks(THUMBS_UP))

    def test_a_thumbs_up_is_rejected_on_its_thumb_alone(self):
        thumbs_up = landmarks(THUMBS_UP)
        assert all(is_finger_curled(thumbs_up, tip, pip) for tip, pip in FINGERS)
        assert not is_disable_fist(thumbs_up)

    @pytest.mark.parametrize("tip, pip", FINGERS, ids=FINGER_NAMES)
    def test_one_finger_breaking_the_fist_is_enough(self, tip, pip):
        """The Countdown cancels on any break in the fist, so a single finger
        coming out must drop the verdict."""
        broken = {**FIST_THUMB_OVER,
                  pip: EXTENDED_FINGERS[pip], tip: EXTENDED_FINGERS[tip]}
        assert not is_disable_fist(landmarks(broken))

    @pytest.mark.parametrize("scale", (PALM_SPAN, PALM_SPAN / 2, PALM_SPAN / 4))
    def test_detection_is_scale_invariant(self, scale):
        assert is_disable_fist(landmarks(FIST_THUMB_OVER, scale=scale))

    def test_a_half_size_fist_in_a_corner_is_still_a_fist(self):
        assert is_disable_fist(landmarks(FIST_THUMB_OVER, scale=PALM_SPAN / 2,
                                         origin=(0.12, 0.40)))

    @pytest.mark.parametrize("scale", (PALM_SPAN, PALM_SPAN / 2, PALM_SPAN / 4))
    def test_an_open_hand_stays_open_at_any_size(self, scale):
        assert not is_disable_fist(landmarks(OPEN_HAND, scale=scale))
