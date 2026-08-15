# <img src="handTrack.png" width="40" alt="handTrack" align="top" /> handTrack

> A macOS menu bar app that lets your hand replace the mouse. A webcam watches your
> hand; the app moves the cursor, clicks, drags and scrolls.

<p align="center">
  <img src="https://github.com/small-cactus/handTrack/assets/125771841/7e4b22b8-46b5-47e0-92ea-f8bfd13f829b" width="900" alt="handTrack" />
</p>

## Gestures

| Gesture | What it does |
| --- | --- |
| Palm to the camera, hand moves | Moves the cursor |
| Index finger and thumb touch | Clicks |
| Index and thumb held together while the hand moves | Drags, and drops when you let go |
| Middle finger and thumb touch, then move up or down | Scrolls |
| Closed fist, held | Stops tracking |

Click and Drag are one gesture, told apart by whether your hand travelled while you
held the pinch. A pinch released on the spot is a click, the same way it works with
a real mouse.

If a Drag is interrupted, by your hand leaving the frame or by tracking stopping
while something is still held, whatever you were carrying returns to where it came
from rather than dropping wherever it happened to be.

## What you need

- macOS 14 (Sonoma) or later
- A Mac with a webcam
- Xcode command line tools, for `swift build`
- Python 3.9 or newer

## Installing

There is no downloadable release yet, so the app is built from source once and then
lives in `/Applications` like anything else.

**1. Clone the repository**

```bash
git clone https://github.com/brohsha/handTrack.git
cd handTrack
```

**2. Set up the Python environment**

The app ships its own hand-tracking code but not a Python runtime, so it needs an
interpreter with MediaPipe available. The build looks for `venv/` in the repo by
default.

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

To use an interpreter you keep elsewhere, point at it with `ENGINE_PYTHON` instead
of creating the venv.

**3. Build and install**

```bash
INSTALL=1 ./build/make-app.sh
```

The hand icon appears in your menu bar when it launches.

## Permissions

macOS asks for two, and they do not behave the same way.

**Camera** is an ordinary prompt. Allow it.

**Accessibility** has no prompt at all. You switch handTrack on yourself in
System Settings › Privacy & Security › Accessibility, and then relaunch, because
macOS caches the answer for as long as the process lives. The app's setup screen
walks you through both and offers to do the restart.

## Using it

Three ways to turn tracking on and off, all equivalent:

- **⌃⌥⌘H**, rebindable under Settings in the Panel
- The **Tracking** toggle in the Panel, which the menu bar icon opens
- **Holding a fist**: one second to confirm you meant it, then a four-second
  countdown beside your cursor. Opening your hand at any point cancels it.

Off releases the camera and puts its light out. Frames are not being captured and
quietly ignored.

Lighting matters more than camera quality. Keep your whole hand inside the frame.

## Updating

One command rebuilds, reinstalls and relaunches:

```bash
INSTALL=1 ./build/make-app.sh
```

A running copy is quit first and reopened afterwards, so there is nothing else to
remember.

Your permissions survive this, because builds are signed with a fixed local
certificate. An ad-hoc signature is derived from the app's contents, so every
rebuild would look like a brand new app to macOS and silently drop both grants,
leaving a `handTrack` row in System Settings that is switched on and granting
nothing. See
[ADR-0002](docs/adr/0002-stable-signing-identity.md).

The build prints which identity it used. If it reports signing ad-hoc, the
certificate is missing and permissions will not survive the next build.

## Troubleshooting

**The build stops with `no interpreter at ...`**
The venv is missing, or the Python it points at has no MediaPipe. Recreate it, or
pass another interpreter: `ENGINE_PYTHON=/path/to/python ./build/make-app.sh`

**The cursor moves but nothing clicks**
Accessibility is granted but the entry has gone stale. Clear it and relaunch so the
app can re-add itself: `tccutil reset Accessibility com.brohsha.handTrack`

**Your hand is not picked up**
Check the lighting and that your hand is fully in frame, palm towards the camera.

**The cursor is jumpy**
Tracking maps the camera's view onto your screen, so a camera at an angle to the
screen will feel skewed. Face the webcam straight on.

## Running the tracking on its own

The hand tracking still runs without the app, which is useful when working on it:

```bash
./venv/bin/python handTrack.py
```

This starts tracking immediately and logs to the terminal, with no menu bar icon.
Ctrl-C or a closed fist stops it.

## Contributing

`CONTEXT.md` defines the vocabulary this project uses for gestures and app states,
and the reasoning behind the larger decisions lives in [`docs/adr/`](docs/adr/).
Read both before changing anything.

Tests:

```bash
./venv/bin/python -m pytest engine/tests
```

## License

MIT, carried over from the original project. See [LICENSE](LICENSE).

---

<sup>handTrack began as [small-cactus/handTrack](https://github.com/small-cactus/handTrack)
by Anthony Hayward: a Python script that did the hand tracking and cursor control,
and the demo footage above. This version turns it into a macOS app with a menu bar
Shell, a gesture vocabulary, drag support and a way to stop it without reaching for
the keyboard. For the original script, go and see
[his repository](https://github.com/small-cactus/handTrack).</sup>
