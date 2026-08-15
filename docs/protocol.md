# Shell ↔ Engine protocol

One JSON object per line, UTF-8, over the Engine's stdin and stdout. Newline
terminated, no pretty-printing. Unparseable lines are logged and ignored, never
fatal — a malformed message must not take down either side.

**Division of responsibility.** The Engine owns every gesture decision and all
gesture timing: it decides what a Disable Fist is, runs the Dwell, counts the
Countdown down, enforces the Grace Period, and applies Gesture Lockout. The Shell
owns everything the user sees and the Engine's lifecycle. The Shell never inspects
landmarks; the Engine never draws.

The Shell reads the cursor position itself when placing the Countdown, so the
Engine never reports coordinates.

## Shell → Engine

| Message | Meaning |
| --- | --- |
| `{"cmd":"start"}` | Enter Tracking. Opens the camera. |
| `{"cmd":"stop"}` | Enter Hard Off. Releases the camera; process stays alive. |
| `{"cmd":"shutdown"}` | Release everything and exit cleanly. |
| `{"cmd":"ping"}` | Liveness check. Answered with `pong`. |

`start` while already Tracking, and `stop` while already in Hard Off, are no-ops
that still emit the corresponding `state` event, so the Shell can resynchronise
without tracking what it already sent.

## Engine → Shell

| Message | Meaning |
| --- | --- |
| `{"event":"ready"}` | Imports and model loaded; idle in Hard Off. Sent once at startup. |
| `{"event":"state","tracking":<bool>,"camera":"open"\|"closed"}` | Authoritative state. Sent on every transition. |
| `{"event":"countdown","seconds_left":<4..0>}` | Dwell completed; Countdown running. Emitted once per second. |
| `{"event":"countdown_cancelled"}` | Disable Fist broken or hand lost. Overlay should disappear. |
| `{"event":"error","fatal":<bool>,"message":"..."}` | Something failed. `fatal` means the Engine is about to exit. |
| `{"event":"pong"}` | Reply to `ping`. |

`seconds_left: 0` is followed immediately by a `state` event with
`tracking: false` — the Engine disables itself and reports it, rather than asking
the Shell for permission.

## Lifecycle

The Shell spawns the Engine on demand and keeps it warm while in use. After
**5 minutes** in Hard Off the Shell sends `shutdown` and lets the process exit,
reclaiming its memory; the next `start` pays the ~1.2s reload. This is the hybrid
from Q15.

If the Engine exits without being asked to, the Shell restarts it once in silence.
A second unrequested exit surfaces the error Indicator and stops retrying.

## Permissions

The **Shell** requests Camera and Accessibility, so both prompts are attributed to
the app bundle. The Engine assumes it has them and reports failure if it does not.
The Engine must never trigger a permission prompt of its own.

Accessibility denial is silent — cursor moves are accepted and do nothing (proven
by the spike). The Engine therefore verifies a move landed where it was asked
before reporting itself healthy, rather than assuming success.
