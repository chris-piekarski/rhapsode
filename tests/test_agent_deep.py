"""Coverage for agent module - edge cases for _current_line, _nearby, Booth."""
from __future__ import annotations

import json
import urllib.error
from io import BytesIO

import pytest

from rhapsode.agent import Booth, agent_view, interpret_command


def _agent_view(session: dict) -> dict:
    return agent_view(session)


# --- _current_line edges ---

def test_current_line_rendering_status():
    lines = [
        {"status": "done", "start": 0, "end": 10},
        {"status": "rendering", "start": 10, "end": 20},
        {"status": "pending", "start": 20, "end": 30},
    ]
    view = _agent_view({"lines": lines, "place": {"playhead": 5.0}})
    assert view["line"]["index"] == 0


def test_current_line_no_playhead_fallthrough():
    lines = [
        {"status": "done", "start": 0, "end": 10},
        {"status": "rendering", "start": 10, "end": 20},
        {"status": "pending"},
    ]
    view = _agent_view({"lines": lines, "place": {"playhead": 25.0}})
    assert view["line"]["index"] == 1


def test_current_line_none_start_end():
    lines = [{"status": "done", "start": None, "end": None}]
    view = _agent_view({"lines": lines, "place": {"playhead": 0.0}})
    assert view["line"] is None


# --- _nearby edges ---

def test_nearby_at_start():
    lines = [
        {"status": "done", "start": 0, "end": 10},
        {"status": "pending", "start": 10, "end": 20},
        {"status": "pending", "start": 20, "end": 30},
        {"status": "pending", "start": 30, "end": 40},
        {"status": "pending", "start": 40, "end": 50},
    ]
    view = _agent_view({"lines": lines, "place": {"playhead": 5.0}})
    assert len(view["nearby"]) == 3
    assert view["nearby"][0]["current"] is True


def test_nearby_at_end():
    lines = [
        {"status": "done", "start": 0, "end": 10},
        {"status": "done", "start": 10, "end": 20},
        {"status": "done", "start": 20, "end": 30},
        {"status": "done", "start": 30, "end": 40},
        {"status": "done", "start": 40, "end": 45},
    ]
    view = _agent_view({"lines": lines, "place": {"playhead": 42.0}})
    assert view["line"]["index"] == 4
    assert len(view["nearby"]) == 2
    assert view["nearby"][-1]["current"] is True


# --- Booth _json edges ---

def test_booth_urllib_error():
    booth = Booth("http://127.0.0.1:19999")
    with pytest.raises(RuntimeError, match="booth is not running"):
        booth._json("GET", "/notfound")


def test_booth_non_dict_response(monkeypatch):
    import urllib.request as req

    class FakeResp:
        def read(self):
            return b'"hello"'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(req, "urlopen", lambda *a, **kw: FakeResp())
    booth = Booth("http://x")
    with pytest.raises(RuntimeError, match="not an object"):
        booth._json("GET", "/")


def test_booth_success(monkeypatch):
    import urllib.request as req
    result = {"status": "ok"}

    class Resp(BytesIO):
        def __init__(self):
            super().__init__(json.dumps(result).encode())

        def read(self, *a):
            self.seek(0)
            return self.getvalue()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(req, "urlopen", lambda *a, **kw: Resp())
    booth = Booth("http://127.0.0.1:12345")
    data = booth._json("GET", "/session")
    assert data == result


def test_booth_http_error(monkeypatch):
    import urllib.request as req
    err = urllib.error.HTTPError(
        "x", 500, "Internal", {}, BytesIO(b'{"error":"boom"}')
    )

    def fake_urlopen(*a, **kw):
        raise err

    monkeypatch.setattr(req, "urlopen", fake_urlopen)
    booth = Booth("http://127.0.0.1:12345")
    with pytest.raises(RuntimeError, match="boom"):
        booth._json("GET", "/session")


# --- interpret_command edges ---

def test_interpret_command_speed_range():
    with pytest.raises(ValueError, match="speed"):
        interpret_command({"type": "speed", "value": 0.1}, 1.0)
    with pytest.raises(ValueError, match="speed"):
        interpret_command({"type": "speed", "value": 3.0}, 1.0)


def test_interpret_command_seek_negative():
    with pytest.raises(ValueError, match="seek"):
        interpret_command({"type": "seek", "seconds": -1}, 1.0)


def test_interpret_command_voice_negative():
    with pytest.raises(ValueError, match="voice"):
        interpret_command({"type": "voice", "value": "af_heart", "line": -1}, 1.0)


def test_interpret_command_voice_invalid():
    with pytest.raises(ValueError, match="unknown voice"):
        interpret_command({"type": "voice", "value": "zz_bad"}, 1.0)
