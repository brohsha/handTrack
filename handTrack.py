"""Run hand tracking on its own, without the Shell.

The implementation moved into engine/ when the macOS Shell was added: the tracking
loop is now engine/handtrack_engine/, and engine/main.py is its entry point. This
file only survives so that `python handTrack.py` still does what it always did.
"""

import sys

from engine.main import main

if __name__ == "__main__":
    sys.exit(main(["--standalone"]))
