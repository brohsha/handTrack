"""Engine entry point.

Two modes, one Engine. By default it speaks the Shell <-> Engine protocol on stdin
and stdout and waits to be told what to do. `--standalone` drops the protocol,
enters Tracking immediately and writes human-readable lines to stderr, so hand
tracking can be exercised and debugged without building or running the Shell.

Standalone ends on Ctrl-C or on a Disable Fist. The original script also watched for
Escape, but only through `cv2.waitKey` on a window it never opened, so there was
never a key for it to read.
"""

import argparse
import json
import sys
from pathlib import Path

# The Engine package is not installed, so put its directory on the path rather than
# depend on how the process happens to be launched — directly, by the Shell, or
# through the handTrack.py shim.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from handtrack_engine.engine import Engine  # noqa: E402


def log_to_stderr(line: str) -> None:
    """Write one protocol line as a readable log line instead of sending it.

    Read back through JSON rather than formatted separately, so protocol.py stays
    the only place that knows what an event contains.
    """
    message = json.loads(line)
    event = message.pop("event")
    fields = " ".join(f"{key}={value}" for key, value in message.items())
    print(f"{event} {fields}".rstrip(), file=sys.stderr, flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="handTrack Engine: hand tracking, gestures and cursor control.")
    parser.add_argument(
        "--standalone", action="store_true",
        help="track immediately and log to stderr, with no Shell on stdin/stdout")
    arguments = parser.parse_args(argv)

    if arguments.standalone:
        print("handTrack Engine, standalone. Ctrl-C to stop.",
              file=sys.stderr, flush=True)
        Engine(emit=log_to_stderr).run_standalone()
    else:
        Engine().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
