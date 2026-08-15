"""The line protocol is the only thing the Shell and Engine agree on, so these
tests pin the exact wire shapes in docs/protocol.md rather than the Python API."""

import json

import pytest

from engine.handtrack_engine import protocol


def parse(line: str) -> dict:
    """Read a line back the way the Shell would: strip the newline, then JSON."""
    assert line.endswith("\n")
    assert line.count("\n") == 1
    return json.loads(line[:-1])


class TestEncode:
    def test_is_one_newline_terminated_line(self):
        line = protocol.encode("ready")
        assert line.endswith("\n")
        assert line.count("\n") == 1

    def test_round_trips_to_the_same_dict(self):
        line = protocol.encode("state", tracking=True, camera="open")
        assert parse(line) == {"event": "state", "tracking": True, "camera": "open"}

    def test_event_name_lands_under_the_event_key(self):
        assert parse(protocol.encode("pong")) == {"event": "pong"}

    def test_stays_one_line_when_a_field_contains_a_newline(self):
        line = protocol.encode("error", fatal=False, message="camera\nfailed")
        assert line.count("\n") == 1
        assert parse(line)["message"] == "camera\nfailed"

    def test_is_not_pretty_printed(self):
        assert "\n" not in protocol.encode("countdown", seconds_left=4)[:-1]


class TestDecodeCommand:
    @pytest.mark.parametrize("cmd", ["start", "stop", "shutdown", "ping"])
    def test_returns_the_name_of_every_valid_command(self, cmd):
        assert protocol.decode_command(json.dumps({"cmd": cmd})) == cmd

    def test_accepts_a_line_with_its_trailing_newline(self):
        assert protocol.decode_command('{"cmd":"start"}\n') == "start"

    def test_ignores_extra_fields(self):
        assert protocol.decode_command('{"cmd":"stop","source":"panel"}') == "stop"

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "\n",
            "   ",
            " \t \n",
            "not json at all",
            "{",
            '{"cmd":"start"',
            "[1, 2, 3]",
            '"start"',
            "42",
            "null",
            "true",
            "{}",
            '{"event":"ready"}',
            '{"cmd":"explode"}',
            '{"cmd":"START"}',
            '{"cmd":null}',
            '{"cmd":1}',
        ],
    )
    def test_returns_none_and_never_raises_for_anything_unusable(self, line):
        assert protocol.decode_command(line) is None

    def test_commands_constant_matches_what_is_accepted(self):
        assert protocol.COMMANDS == {"start", "stop", "shutdown", "ping"}


class TestStateEvent:
    def test_tracking_with_the_camera_open(self):
        assert parse(protocol.state_event(True, True)) == {
            "event": "state",
            "tracking": True,
            "camera": "open",
        }

    def test_hard_off_reports_the_camera_closed(self):
        assert parse(protocol.state_event(False, False)) == {
            "event": "state",
            "tracking": False,
            "camera": "closed",
        }

    def test_camera_is_a_string_not_a_bool(self):
        assert parse(protocol.state_event(True, True))["camera"] == "open"
        assert parse(protocol.state_event(False, False))["camera"] == "closed"


class TestCountdownEvent:
    @pytest.mark.parametrize("seconds_left", [4, 3, 2, 1, 0])
    def test_carries_every_tick_of_the_countdown(self, seconds_left):
        assert parse(protocol.countdown_event(seconds_left)) == {
            "event": "countdown",
            "seconds_left": seconds_left,
        }


class TestCountdownCancelledEvent:
    def test_has_no_fields_beyond_the_event_name(self):
        assert parse(protocol.countdown_cancelled_event()) == {
            "event": "countdown_cancelled"
        }


class TestErrorEvent:
    def test_defaults_to_non_fatal(self):
        assert parse(protocol.error_event("camera busy")) == {
            "event": "error",
            "fatal": False,
            "message": "camera busy",
        }

    def test_fatal_is_carried_through(self):
        assert parse(protocol.error_event("model missing", fatal=True)) == {
            "event": "error",
            "fatal": True,
            "message": "model missing",
        }
