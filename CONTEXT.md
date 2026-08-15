# handTrack

Hand-gesture control of the macOS cursor via a webcam. The user's hand replaces the
mouse: moving the hand moves the cursor, pinching gestures click and scroll.

## Language

### Control state

**Tracking**:
The state in which the webcam is running and hand gestures drive the cursor.
_Avoid_: enabled, active, running, "the script"

**Hard Off**:
Tracking stopped with the webcam released, so the camera indicator light goes out.
The only kind of "off" the app currently has.
_Avoid_: paused, disabled, suspended

**Toggle Shortcut**:
The global keyboard shortcut that switches between Tracking and Hard Off. The same
shortcut does both.
_Avoid_: hotkey, keybinding

### Gestures

**Pinch**:
Thumb touching a fingertip. Index pinch clicks; middle pinch scrolls.
_Avoid_: tap, touch, click gesture

**Disable Fist**:
A closed hand — all four fingers curled and the thumb folded against the hand —
held deliberately, that ends Tracking. Accepted whether the thumb folds over or
under the fingers. Distinct from a Pinch, and never produces a click.
_Avoid_: kill gesture, stop gesture, closed hand

### Shutting down by gesture

**Dwell**:
The period a Disable Fist must be held before the Countdown begins, to prove the
fist was deliberate.
_Avoid_: delay, grace period, confirmation

**Countdown**:
The visible count from 4 to 0 that follows the Dwell. Reaching 0 puts the app into
Hard Off. Any break in the Disable Fist cancels it and resets it to zero.
_Avoid_: timer, disarm, shutdown timer

**Gesture Lockout**:
The condition, lasting from the start of the Dwell until the Countdown ends or
cancels, in which the hand controls nothing — the cursor is frozen and any held
mouse button is released.
_Avoid_: freeze, suppression, ignore mode

**Grace Period**:
The short window after Tracking starts during which a Disable Fist is ignored, so
that a hand still closed from the last shutdown cannot immediately trigger another.
_Avoid_: cooldown, debounce, re-arm delay

### Parts of the app

**Shell**:
The macOS application the user sees and interacts with — menu bar icon, panel,
Toggle Shortcut, and Countdown display. Owns no tracking logic.
_Avoid_: app, frontend, UI layer

**Engine**:
The hand-tracking process — camera capture, landmark detection, gesture
recognition, and cursor control. Owns no user interface.
_Avoid_: script, backend, tracker, daemon

**Panel**:
The window the menu bar icon opens, containing everything the user can see or
change. The app's only interface besides the Countdown.
_Avoid_: dropdown, menu, popover, preferences

**Indicator**:
The small mark beside the menu bar logo that reports the current state. The logo
itself never changes, so the app stays recognisable.
_Avoid_: badge, status icon, light
