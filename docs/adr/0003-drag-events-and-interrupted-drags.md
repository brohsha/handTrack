# Post drag events while a button is held, and hold a Drag through the Lockout

Dragging did not work. Pinching a file and moving picked it up for a moment and then
went dead, however far the hand travelled.

The cause was the *kind* of event being posted, not the cursor position. macOS routes
`kCGEventMouseMoved` and `kCGEventLeftMouseDragged` to different handlers, and apps
follow a drag from the latter. `CursorMovementThread` called `pyautogui.moveTo`
unconditionally, so an app received `leftMouseDown` and then a stream of moves — it
saw the press, then never heard that anything had moved. Nothing errored; macOS
accepted every event and did nothing with them.

Measured before writing the fix, by tapping the event stream while posting both
sequences:

| posted while the button was held | delivered to the session |
| --- | --- |
| `pyautogui.moveTo` | 5 × `mouseMoved`, 0 × `leftMouseDragged` |
| `pyautogui.dragTo(mouseDownUp=False)` | 5 × `leftMouseDragged` |

`mouseDownUp=False` is load-bearing. The default presses and releases the button
around each call, which across one drag would be dozens of clicks.

## A held button is a Drag, and macOS decides what that means

No Drag mode is entered and no gesture is added. `press` puts the movement thread
into drag events and `release` takes it out, so a quick Pinch reads to macOS as a
click and a Pinch-move-release reads as a drag — the same rule a real mouse follows.
The click/drag distinction stays where the OS already implements it.

No separate movement threshold guards against a click turning into an accidental
micro-drag. `jitter_threshold` already ignores motion under 0.3% of the screen
diagonal (~5px), and a second threshold would only have to be tuned against the
first.

## The Lockout holds a Drag instead of dropping it

The Gesture Lockout used to release the button the instant a Dwell began. That was
free when dragging did not work; once it does, it is destructive.

Closing a hand into a Disable Fist necessarily passes through a Pinch — the fingers
cross the touch threshold before they finish curling — so a fist made mid-Drag
arrives with the button already down. Releasing there would drop whatever the user
was carrying the moment their hand relaxed, four seconds before the Countdown even
asked whether they meant it.

The Lockout now freezes the cursor but keeps the button held. Break the fist and the
Drag carries on from where it stopped; let the Countdown finish and it Springs back.
The original reasoning — avoid "a second of dragging with the mouse button held
down" — was about the cursor *moving* while pressed, and the freeze already prevents
that.

The alternative was to suppress the Disable Fist while a button is held. It does not
work: because forming a fist presses the button on the way through, suppression
would make the shutdown gesture unreachable.

## An interrupted Drag Springs back rather than dropping

Losing the hand mid-Drag is never a decision to drop something there — it is the
frame edge, the light, or a missed detection. Escape sent while the button is still
down makes macOS return the item to where it came from, which is recoverable; a
wrong file move is not.

Two limits are deliberate:

- **Escape is only sent if the cursor travelled at least `DRAG_CANCEL_MIN_TRAVEL`
  (10px) since the press.** A press that never moved was a click, and Escape would
  leak into whatever the user had on screen — enough to dismiss a dialog they were
  part-way through.
- **A Drag survives `MAX_DRAG_LOST_FRAMES` (5, under a fifth of a second) without a
  hand.** MediaPipe loses a fast or motion-blurred hand routinely, and a fast-moving
  hand is exactly what a Drag looks like; cancelling on the first miss would make
  dragging fail hardest when it was working hardest.

Escape cancels drag-and-drop sessions only. A text selection or a dragged slider
stops where it is, which is still better than a wrong drop.

## Consequences

Releasing snaps the cursor to its true target before lifting the button. The easing
closes only a twelfth of the gap per pass and stops within `jitter_threshold`, so the
cursor habitually rests a few pixels behind the hand — invisible while moving, but a
drop lands where the cursor *is*.

**If this reads as twitchy, the documented fallback is to drop the snap** and accept
a drop that lands slightly behind the hand. That reverts to coarse placement without
touching anything else; `release(snap=False)` already exists for the abandon path.

The easing itself is unchanged during a Drag. Holding a pose is when hand tremor is
worst, so a Drag wants more smoothing than free movement, not less.

Index and middle Pinch stay independent, so dragging while scrolling still works —
the mouse analogy holds, and it costs nothing to leave alone.

`CursorMovementThread` is no longer verbatim-inherited from the original script. Its
tuning is untouched; only which event it posts has changed.

The button and the event type change under one re-entrant lock. Unguarded, the
movement thread posts between them and slips a plain move under a held button —
measured, not theorised: the first cut of this change did exactly that.

One plain move still trails every drag, because `pyautogui.mouseUp` posts a move to
the current position inside its own call. It lands exactly where the snap did, after
the app has had the whole run of drag events, so it is a positional no-op and is
left alone rather than reached around with `platformModule._mouseUp`.

This regression is silent by nature — nothing fails loudly when the wrong event type
is posted, dragging just stops working — so `engine/tests/test_drag.py` pins the
event type directly, and is the one suite that imports pyautogui in order to do it.
