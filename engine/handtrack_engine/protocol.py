"""The Shell <-> Engine line protocol. See docs/protocol.md.

One JSON object per line. A malformed line is never fatal: it is reported and
dropped, because a crash here would take down hand tracking over a typo.
"""

import json
from typing import Any, Optional

CMD_START = "start"
CMD_STOP = "stop"
CMD_SHUTDOWN = "shutdown"
CMD_PING = "ping"
COMMANDS = frozenset({CMD_START, CMD_STOP, CMD_SHUTDOWN, CMD_PING})


def encode(event: str, **fields: Any) -> str:
    """Render one Engine -> Shell event as a newline-terminated JSON line."""
    # json.dumps escapes any newline inside a value, so the trailing "\n" stays the
    # only one on the line and the Shell can split on it safely.
    return json.dumps({"event": event, **fields}, separators=(",", ":")) + "\n"


def decode_command(line: str) -> Optional[str]:
    """Parse one Shell -> Engine line, returning the command name.

    Returns None for blank lines, malformed JSON, or any command not in COMMANDS.
    """
    try:
        message = json.loads(line)
    except (ValueError, TypeError):
        # A typo on the pipe must not stop hand tracking, so nothing propagates.
        return None
    if not isinstance(message, dict):
        return None
    command = message.get("cmd")
    return command if command in COMMANDS else None


def state_event(tracking: bool, camera_open: bool) -> str:
    """The authoritative state line, emitted on every transition."""
    return encode("state", tracking=tracking,
                  camera="open" if camera_open else "closed")


def countdown_event(seconds_left: int) -> str:
    """One tick of the Countdown, 4 down to 0."""
    return encode("countdown", seconds_left=seconds_left)


def countdown_cancelled_event() -> str:
    """The Disable Fist broke or the hand was lost; the overlay should vanish."""
    return encode("countdown_cancelled")


def error_event(message: str, fatal: bool = False) -> str:
    """Something failed. `fatal` means the Engine is about to exit."""
    return encode("error", fatal=fatal, message=message)
