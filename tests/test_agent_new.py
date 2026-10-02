"""Tests for booth methods and MCP tool wiring."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
import urllib.request as req

import pytest

from rhapsode.agent import Booth


def _session_resp(playhead=None, current_line=None, lines=None, tabs=None):
    """Build a minimal /session response that agent_view can read."""
    return {
        "title": "test",
        "transport": "playing",
        "speed": 1.0,
        "voice": "af_heart",
        "lines": lines or [],
        "place": {"playhead": playhead},
        "source": {"label": "test.json"},
        "mcp": {},
    }


# Helpers: fake urlopen responses

class FakeJsonResp(BytesIO):
    """Fake response returning JSON."""

    def __init__(self, obj):
        super().__init__(json.dumps(obj).encode())

    def read(self, *a):
        self.seek(0)
        return self.getvalue()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class FakeHeaders:
    def __init__(self, d):
        self._d = d

    def get(self, name, default=None):
        return self._d.get(name, default)


class FakeBytesResp(BytesIO):
    """Fake response returning raw bytes with headers."""

    def __init__(self, data, headers=None):
        self._headers = FakeHeaders(headers or {})
        super().__init__(data)

    @property
    def headers(self):
        return self._headers

    def read(self, *a):
        self.seek(0)
        return self.getvalue()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def _multi_resp(*json_objs):
    """Return an iterator that yields FakeJsonResp for each json_obj."""
    return iter(FakeJsonResp(obj) for obj in json_objs)


# --- Booth.lines ---


def test_lines_honors_start_and_count(monkeypatch):
    session = _session_resp(
        current_line=0,
        lines=[
            {"text": f"line {i}", "kind": "p", "status": "done", "start": float(i * 10), "end": float((i + 1) * 10)}
            for i in range(5)
        ],
    )
    monkeypatch.setattr(req, "urlopen", lambda *a, **kw: FakeJsonResp(session))
    booth = Booth("http://127.0.0.1:12345")
    result = booth.lines(start=2, count=3)
    assert len(result) == 3
    assert result[0]["index"] == 2
    assert result[0]["text"] == "line 2"
    assert result[0]["start"] == 20.0
    assert result[0]["end"] == 30.0
    assert result[-1]["index"] == 4


# --- Booth.seek_line ---


def test_seek_line_posts_seek(monkeypatch):
    lines = [
        {"text": "a", "kind": "p", "status": "done", "start": 5.0, "end": 10.0},
        {"text": "b", "kind": "p", "status": "done", "start": 10.0, "end": 15.0},
    ]
    session = _session_resp(playhead=7.0, lines=lines)
    resp_iter = _multi_resp(session, {"ok": True})
    calls = []

    def fake_urlopen(req_obj, **kw):
        calls.append((req_obj.method, req_obj.full_url, req_obj.data))
        return next(resp_iter)

    monkeypatch.setattr(req, "urlopen", fake_urlopen)
    booth = Booth("http://127.0.0.1:12345")
    result = booth.seek_line(1)
    assert result == {"ok": True}
    assert calls[1][0] == "POST"
    assert json.loads(calls[1][2]) == {"type": "seek", "seconds": 10.0}


def test_seek_line_no_audio_raises(monkeypatch):
    lines = [{"text": "pending", "kind": "p", "status": "pending", "start": None, "end": None}]
    monkeypatch.setattr(req, "urlopen", lambda *a, **kw: FakeJsonResp(_session_resp(playhead=0, lines=lines)))
    booth = Booth("http://127.0.0.1:12345")
    with pytest.raises(RuntimeError, match="line 0 has no audio yet"):
        booth.seek_line(0)


def test_seek_line_out_of_range(monkeypatch):
    lines = [{"text": "only", "kind": "p", "status": "done", "start": 0.0, "end": 5.0}]
    monkeypatch.setattr(req, "urlopen", lambda *a, **kw: FakeJsonResp(_session_resp(playhead=0, lines=lines)))
    booth = Booth("http://127.0.0.1:12345")
    with pytest.raises(RuntimeError, match="line 5 is not in the script"):
        booth.seek_line(5)


# --- Booth.tabs ---


def test_tabs_returns_list(monkeypatch):
    tabs_resp = {
        "tabs": [
            {"id": "1", "url": "https://example.com", "title": "Example"},
            {"id": "2", "url": "https://other.com"},
        ]
    }
    monkeypatch.setattr(req, "urlopen", lambda *a, **kw: FakeJsonResp(tabs_resp))
    booth = Booth("http://127.0.0.1:12345")
    tabs = booth.tabs()
    assert len(tabs) == 2
    assert tabs[0]["title"] == "Example"
    assert "title" not in tabs[1]


# --- Booth.open_tab ---


def test_open_tab_rejects_non_http(monkeypatch):
    booth = Booth("http://127.0.0.1:12345")
    with pytest.raises(ValueError, match="Pick a tab by its http address"):
        booth.open_tab("ftp://no")


def test_open_tab_posts_url(monkeypatch):
    tab_resp = {"ok": True, "title": "New page", "url": "https://example.com"}
    calls = []

    def fake_urlopen(req_obj, **kw):
        calls.append((req_obj.method, req_obj.full_url, req_obj.data))
        return FakeJsonResp(tab_resp)

    monkeypatch.setattr(req, "urlopen", fake_urlopen)
    booth = Booth("http://127.0.0.1:12345")
    result = booth.open_tab("https://example.com")
    assert result["ok"] is True
    posted = json.loads(calls[0][2])
    assert posted["url"] == "https://example.com"


def test_open_tab_accepts_uppercase_http(monkeypatch):
    """HTTP://example.com must be accepted by .lower().startswith('http')."""
    calls = []

    def fake_urlopen(req_obj, **kw):
        calls.append((req_obj.method, req_obj.full_url, req_obj.data))
        return FakeJsonResp({"ok": True})

    monkeypatch.setattr(req, "urlopen", fake_urlopen)
    booth = Booth("http://127.0.0.1:12345")
    booth.open_tab("HTTP://example.com")
    posted = json.loads(calls[0][2])
    assert posted["url"] == "HTTP://example.com"


# --- Booth.voice ---


def _make_voice_session(current_index, playhead, text_lines):
    """Create a /session response where _current_line resolves to current_index."""
    lines = []
    for i, t in enumerate(text_lines):
        lines.append({
            "text": t,
            "kind": "p",
            "status": "done",
            "start": float(i * 10),
            "end": float((i + 1) * 10),
        })
    return _session_resp(playhead=playhead, lines=lines)


def test_voice_auto_resolves_current_line(monkeypatch):
    """booth.voice('bf_emma') with current line index 1 posts line=1."""
    # playhead=15 → _current_line scans lines and finds index 1 (10..20)
    text_lines = ["intro", "hello", "world"]
    session = _make_voice_session(1, 15, text_lines)
    resp_iter = _multi_resp(session, {"ok": True})
    calls = []

    def fake_urlopen(req_obj, **kw):
        calls.append((req_obj.method, req_obj.full_url, req_obj.data))
        return next(resp_iter)

    monkeypatch.setattr(req, "urlopen", fake_urlopen)
    booth = Booth("http://127.0.0.1:12345")
    result = booth.voice("bf_emma")
    assert result == {"ok": True}
    posted = json.loads(calls[-1][2])
    assert posted == {"type": "voice", "value": "bf_emma", "line": 1}


def test_voice_explicit_line(monkeypatch):
    """booth.voice('af_heart', line=4) posts line=4 without needing a current line."""
    # Explicit line bypasses the status() lookup, so only command() is called
    resp_iter = _multi_resp({"ok": True})
    calls = []

    def fake_urlopen(req_obj, **kw):
        calls.append((req_obj.method, req_obj.full_url, req_obj.data))
        return next(resp_iter)

    monkeypatch.setattr(req, "urlopen", fake_urlopen)
    booth = Booth("http://127.0.0.1:12345")
    result = booth.voice("af_heart", line=4)
    assert result == {"ok": True}
    posted = json.loads(calls[-1][2])
    assert posted == {"type": "voice", "value": "af_heart", "line": 4}


def test_voice_no_current_line_raises(monkeypatch):
    """booth.voice('af_heart') with no current line raises 'no current line; pass line='."""
    text_lines = []
    session = _make_voice_session(None, None, text_lines)
    monkeypatch.setattr(req, "urlopen", lambda *a, **kw: FakeJsonResp(session))
    booth = Booth("http://127.0.0.1:12345")
    with pytest.raises(RuntimeError, match=r"no current line; pass line="):
        booth.voice("af_heart")


# --- Booth.skip ---


def _make_skip_session(playhead, num_lines=3):
    """Create a /session response with playhead set."""
    text_lines = [f"line {i}" for i in range(num_lines)]
    lines = []
    for i, t in enumerate(text_lines):
        lines.append({
            "text": t,
            "kind": "p",
            "status": "done",
            "start": float(i * 10),
            "end": float((i + 1) * 10),
        })
    return _session_resp(playhead=playhead, lines=lines)


def test_skip_no_playhead_raises(monkeypatch):
    """booth.skip() when playhead is None raises RuntimeError."""
    session = _make_skip_session(None)
    monkeypatch.setattr(req, "urlopen", lambda *a, **kw: FakeJsonResp(session))
    booth = Booth("http://127.0.0.1:12345")
    with pytest.raises(RuntimeError, match=r"the booth has not reported a playhead yet"):
        booth.skip()


def test_skip_forward(monkeypatch):
    """booth.skip(15) with playhead=12 posts seek 27."""
    session = _make_skip_session(12)
    resp_iter = _multi_resp(session, {"ok": True})
    calls = []

    def fake_urlopen(req_obj, **kw):
        calls.append((req_obj.method, req_obj.full_url, req_obj.data))
        return next(resp_iter)

    monkeypatch.setattr(req, "urlopen", fake_urlopen)
    booth = Booth("http://127.0.0.1:12345")
    result = booth.skip(15)
    assert result == {"ok": True}
    posted = json.loads(calls[-1][2])
    assert posted == {"type": "seek", "seconds": 27.0}


def test_skip_back_clamps_to_zero(monkeypatch):
    """booth.skip(-20) with playhead=10 posts seek 0 (clamped)."""
    session = _make_skip_session(10)
    resp_iter = _multi_resp(session, {"ok": True})
    calls = []

    def fake_urlopen(req_obj, **kw):
        calls.append((req_obj.method, req_obj.full_url, req_obj.data))
        return next(resp_iter)

    monkeypatch.setattr(req, "urlopen", fake_urlopen)
    booth = Booth("http://127.0.0.1:12345")
    result = booth.skip(-20)
    assert result == {"ok": True}
    posted = json.loads(calls[-1][2])
    assert posted == {"type": "seek", "seconds": 0.0}


# --- Booth.export ---


def test_export_writes_file(tmp_path, monkeypatch):
    wav_data = b"\x52\x49\x46\x46" + b"\x00" * 100  # fake WAV
    headers = {
        "X-Rhapsode-Lines": "8/10",
        "X-Rhapsode-Partial": "1",
        "Content-Disposition": 'attachment; filename="my-export.wav"',
    }
    monkeypatch.setattr(req, "urlopen", lambda *a, **kw: FakeBytesResp(wav_data, headers))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    booth = Booth("http://127.0.0.1:12345")
    result = booth.export(start=0, end=10, name="my-export.wav")
    assert result["included"] == 8
    assert result["requested"] == 10
    assert result["partial"] is True
    assert result["bytes"] == len(wav_data)
    exported = Path(result["path"])
    assert exported.is_file()
    assert exported.read_bytes() == wav_data
    assert exported.name == "my-export.wav"


def test_export_no_partial_no_disposition(tmp_path, monkeypatch):
    wav_data = b"\x52\x49\x46\x46" + b"\x00" * 50
    headers = {"X-Rhapsode-Lines": "5/5"}
    monkeypatch.setattr(req, "urlopen", lambda *a, **kw: FakeBytesResp(wav_data, headers))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    booth = Booth("http://127.0.0.1:12345")
    result = booth.export()
    assert result["partial"] is False
    assert result["included"] == 5
    assert result["requested"] == 5
    assert result["bytes"] == len(wav_data)
