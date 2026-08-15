"""Permission spike: does a child process of a signed .app inherit its TCC grants?

Run by the Shell spike, never by hand (run it directly and you are testing the
terminal's permissions, not the app's). Prints one JSON object to stdout.
"""

import json
import sys

result = {"camera": None, "cursor": None}

try:
    import cv2

    cap = cv2.VideoCapture(0)
    ok, frame = cap.read()
    result["camera"] = (
        {"ok": True, "frame": list(frame.shape)}
        if ok and frame is not None
        else {"ok": False, "error": "opened but no frame (permission usually)"}
    )
    cap.release()
except Exception as exc:  # noqa: BLE001 - spike reports every failure verbatim
    result["camera"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

try:
    import pyautogui

    pyautogui.FAILSAFE = False
    start_x, start_y = pyautogui.position()
    target_x, target_y = start_x + 60, start_y + 60
    pyautogui.moveTo(target_x, target_y, _pause=False)
    end_x, end_y = pyautogui.position()
    # Accessibility denied is silent: moveTo returns cleanly, cursor never moves.
    result["cursor"] = {
        "ok": (end_x, end_y) != (start_x, start_y),
        "from": [start_x, start_y],
        "requested": [target_x, target_y],
        "landed": [end_x, end_y],
    }
    pyautogui.moveTo(start_x, start_y, _pause=False)
except Exception as exc:  # noqa: BLE001
    result["cursor"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

json.dump(result, sys.stdout)
sys.stdout.write("\n")
sys.stdout.flush()
