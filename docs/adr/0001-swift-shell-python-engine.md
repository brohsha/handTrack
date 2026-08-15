# Swift Shell around an unchanged Python Engine

The app is two processes in one bundle: a Swift/AppKit **Shell** that owns all user
interface, and the existing Python **Engine** that owns all hand tracking. The Shell
launches the Engine as a child process and they exchange newline-delimited JSON over
a pipe.

The Engine's accuracy is the product. Its thresholds, smoothing and landmark maths
are tuned empirically and are not worth re-deriving, so any option that rewrote them
was rejected on risk grounds alone — including a full Swift rewrite on Apple's Vision
framework, which offers equivalent hand landmarks but would have meant reproducing
every tuned constant. Splitting Shell from Engine puts a hard interface between the
code we are adding and the code we must not perturb.

## Considered Options

**Pure Python via pyobjc.** Genuinely viable — a `.app` need not contain any Swift,
and pyobjc drives real AppKit, so this was the initial recommendation. It was
displaced when the interface grew to four surfaces: menu bar panel, settings, a
keyboard shortcut recorder, and a floating countdown overlay. At that size,
hand-written AppKit from Python costs more to write and far more to debug than
SwiftUI, and its errors surface as opaque Objective-C exceptions. Its one lasting
advantage is recorded under Consequences.

**Full Swift rewrite on Vision.** Rejected: discards the tuned tracking logic.

## Consequences

Two languages, and a message protocol to maintain — though the boundary is narrow,
roughly sixty lines on each side.

The significant cost is permissions. macOS attributes Camera and Accessibility
permission per process, and the Engine, not the Shell, is what opens the camera and
moves the cursor. A single-process design would have had exactly one identity to
authorise; this design relies on macOS crediting the child's access to the parent
bundle.

**This was proven by spike before any interface was built.** With the grants held by
`handTrack.app`, a Python child process captured a 1080p frame and moved the cursor to
the exact requested coordinates. Four findings worth keeping:

- **The bundle needs a stable signing identity.** An ad-hoc signature is derived from
  the bundle's contents, so every rebuild is a different app to macOS and both grants
  are silently dropped. An earlier revision of this ADR claimed the opposite on the
  strength of a single rebuild that happened to survive; re-granting after every build
  proved that wrong. See ADR-0002.

- The Shell must request both permissions, so the prompts are attributed to the app
  bundle. The Engine must never prompt for anything.
- `AXIsProcessTrusted()` is cached per process. A grant made while the app is running
  is invisible to it until relaunch, so the first-run screen must offer a restart
  rather than polling forever.
- A stale Accessibility entry silently grants nothing. If the row exists but the app
  still reads untrusted, `tccutil reset Accessibility com.brohsha.handTrack` clears it
  so the app can re-add itself.

Accessibility denial is silent — cursor moves are accepted and simply do not happen —
so the Engine verifies that a move landed before reporting itself healthy.

Xcode is not required: SwiftPM from the Command Line Tools builds an AppKit and
SwiftUI executable, and `codesign` and `notarytool` are both present.
