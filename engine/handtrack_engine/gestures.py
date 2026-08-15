"""Gesture recognition on MediaPipe hand landmarks.

Pure geometry, no camera and no cursor, so every rule here is unit-testable against
synthetic landmarks.

Scale reference note: the original script sized its thresholds against the distance
from wrist to middle *fingertip*, which shrinks as the hand closes — exactly the
motion we need to measure. Everything here scales against `palm_span` (wrist to
middle finger *knuckle*) instead, which barely changes as the fingers curl.
"""

import math
from typing import Protocol, Sequence

WRIST = 0
THUMB_TIP = 4
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_MCP, RING_PIP, RING_TIP = 13, 14, 16
PINKY_MCP, PINKY_PIP, PINKY_TIP = 17, 18, 20

#: (tip, pip) for the four fingers a Disable Fist requires to be curled.
FINGERS = ((INDEX_TIP, INDEX_PIP), (MIDDLE_TIP, MIDDLE_PIP),
           (RING_TIP, RING_PIP), (PINKY_TIP, PINKY_PIP))

#: Thumb counts as folded when its tip is within this fraction of palm_span of the
#: index knuckle. Generous on purpose: a fist folded thumb-over-fingers and one
#: folded thumb-under-fingers differ mostly in depth, which we ignore. Bounded
#: above by the thumbs-up, whose extended tip clears the knuckle by roughly half a
#: palm_span while a folded one sits at a third of it.
THUMB_FOLD_RATIO = 0.45


class Landmark(Protocol):
    """A MediaPipe landmark. `z` exists but is deliberately unused."""
    x: float
    y: float
    z: float


def distance_2d(a: Landmark, b: Landmark) -> float:
    """Euclidean distance in the image plane, ignoring depth."""
    return math.hypot(a.x - b.x, a.y - b.y)


def palm_span(landmarks: Sequence[Landmark]) -> float:
    """Wrist to middle-finger knuckle. Stable scale reference under any curl."""
    return distance_2d(landmarks[WRIST], landmarks[MIDDLE_MCP])


def is_finger_curled(landmarks: Sequence[Landmark], tip: int, pip: int) -> bool:
    """True when the fingertip has folded back closer to the wrist than its own
    middle joint. Scale-free and rotation-free, so it holds at any hand distance."""
    wrist = landmarks[WRIST]
    return distance_2d(landmarks[tip], wrist) < distance_2d(landmarks[pip], wrist)


def is_thumb_folded(landmarks: Sequence[Landmark]) -> bool:
    """True when the thumb is folded against the hand rather than sticking out.

    Accepts the thumb folded over the fingers or tucked under them — the two differ
    almost entirely in depth, which this ignores by design.
    """
    reach = distance_2d(landmarks[THUMB_TIP], landmarks[INDEX_MCP])
    # Scaled by multiplication rather than division so a degenerate frame, where
    # the wrist and knuckle land on the same pixel, reports an unfolded thumb
    # instead of raising inside the tracking loop.
    return reach < THUMB_FOLD_RATIO * palm_span(landmarks)


def is_disable_fist(landmarks: Sequence[Landmark]) -> bool:
    """True for a closed hand: all four fingers curled and the thumb folded.

    Must be False for a Pinch, where the middle, ring and little fingers stay
    extended, and False for a thumbs-up, where the thumb sticks out.
    """
    return (all(is_finger_curled(landmarks, tip, pip) for tip, pip in FINGERS)
            and is_thumb_folded(landmarks))
