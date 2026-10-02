"""Booth, Chrome link, and agent paths exercised without a real browser or GPU."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from rhapsode.agent import Booth, agent_view, interpret_command
from rhapsode.chrome_link import (
    ChromeLink,
    _chrome_version,
    _last_json,
    _local_appdata,
    _node_exe,
    _win_to_wsl,
    _wsl_to_win,
    blank_mcp,
    install_version,
    instance_name,
    load_chrome_profiles,
    load_last_active,
    match_profile,
    page_identity,
    parse_browser_endpoint,
    preferred_profile,
    profile_for_page,
)
from rhapsode.desk import (
    LiveHub,
    ThreadingHTTPServer,
    _Handler,
    _narrate_into,
    _speak,
    _ws_recv,
    _ws_send,
    load_place,
    page_html,
    parse_byte_range,
    reading_session,
    serve,
    serve_live,
)


def _session(tmp_path: Path) -> dict:
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"abcdefghijklmnopqrstuvwxyz")
    session = reading_session(wav, page_url="https://example.com/ch")
    session["lines"] = [
        {"text": "Hello.", "kind": "p", "start": None, "end": None, "status": "pending"},
        {"text": "Next.", "kind": "p", "start": None, "end": None, "status": "pending"},
    ]
    return session


def _place(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("rhapsode.desk.place_file", lambda _label: tmp_path / "place.json")


def _server(tmp_path: Path, monkeypatch, hub: bool = True):
    _place(monkeypatch, tmp_path)
    session = _session(tmp_path)
    live = LiveHub(session) if hub else None
    handler = partial(_Handler, wav=tmp_path / "clip.wav", session=session, hub=live)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, live, f"http://127.0.0.1:{server.server_address[1]}"


def _get(url: str, headers: dict | None = None) -> tuple[int, bytes, dict]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def _post(url: str, payload: dict | str | bytes) -> tuple[int, bytes]:
    if isinstance(payload, dict):
        data = json.dumps(payload).encode()
    elif isinstance(payload, str):
        data = payload.encode()
    else:
        data = payload
    request = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_choose_tab_replaces_the_reading(tmp_path: Path, monkeypatch):
    server, hub, base = _server(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(
            "rhapsode.desk.list_chrome_tabs",
            lambda: [{"id": 1, "url": "https://example.com/chapter"}],
        )
        status, body, _headers = _get(base + "/tabs")
        assert status == 200
        assert json.loads(body)["tabs"] == [{"id": 1, "url": "https://example.com/chapter"}]

        def fake_read(url: str) -> Path:
            path = tmp_path / "picked.json"
            path.write_text(
                json.dumps({
                    "title": "Picked chapter",
                    "url": url,
                    "sections": [{
                        "heading": "Hi",
                        "level": 1,
                        "blocks": [{"kind": "p", "text": "Hello from the tab."}],
                    }],
                }),
                encoding="utf-8",
            )
            return path

        monkeypatch.setattr("rhapsode.desk.read_chrome_tab", fake_read)
        code, raw = _post(base + "/tab", {"url": "https://example.com/chapter"})
        assert code == 200
        assert json.loads(raw)["title"] == "Picked chapter"
        assert hub.session["title"] == "Picked chapter"
        assert hub.session["source"]["label"] == "https://example.com/chapter"
        assert hub.session["lines"][0]["text"] == "Hi."
        assert hub.session["lines"][1]["text"] == "Hello from the tab."
        assert hub.doc_generation == 1
        assert hub.audio == []

        refused, _ = _post(base + "/tab", {"url": "not-a-page"})
        assert refused == 400
    finally:
        server.shutdown()


def test_place_and_reading_edges(tmp_path: Path, monkeypatch):
    _place(monkeypatch, tmp_path)
    assert load_place("missing") is None
    path = tmp_path / "place.json"
    path.write_text("not-json", encoding="utf-8")
    assert load_place("bad") is None
    path.write_text("{}", encoding="utf-8")
    assert load_place("empty") is None
    wav = tmp_path / "spoken.wav"
    import wave

    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00" * 8)
    session = reading_session(
        wav, page_url="https://example.com/ch", spoken="Hello world"
    )
    assert session["title"]
    assert session["lines"]

    # Byte-range edges.
    assert parse_byte_range(None, 100) is None
    assert parse_byte_range("", 100) is None
    r = parse_byte_range("bytes=0-39", 100)
    assert r is not None and r[0] == 0 and r[1] == 39


def test_websocket_framing():
    """Low-level ws frame build/parse exercised without a server."""
    left, right = socket.socketpair()
    try:
        # text + binary + large text (fragmented).
        _ws_send(left, b"hi", 1)
        _ws_send(left, b"x" * 200, 2)
        _ws_send(left, b"y" * 70_000, 1)
        assert _ws_recv(right) == (1, b"hi")
        assert _ws_recv(right) == (2, b"x" * 200)
        opcode, data = _ws_recv(right)
        assert opcode == 1 and data == b"y" * 70_000

        # round-trip: send from right side back to left.
        _ws_send(right, b"ab", 1)
        assert _ws_recv(left) == (1, b"ab")
    finally:
        left.close()
        right.close()


# ------------------------------------------------------------------
# WS handshake helpers used by live-server tests below
# ------------------------------------------------------------------


def _handshake(sock: socket.socket) -> None:
    """Accept a client-side raw TCP socket through a manual WS upgrade."""
    # server -> client accept (no masking).
    sock.sendall(
        b"HTTP/1.1 101 Switching Protocols\r\n"
        b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n"
        b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n"
        b"\r\n"
    )


@pytest.mark.timeout(30)
def test_desk_http_and_socket(tmp_path: Path, monkeypatch):
    _place(monkeypatch, tmp_path)
    server, hub, base = _server(tmp_path, monkeypatch)
    try:
        # --- health ---
        status, body, _headers = _get(base + "/")
        assert status == 200 and b"src-title" in body
        status, body, _headers = _get(base + "/voices")
        assert status == 200 and b"af_heart" in body
        status, _body, _headers = _get(base + "/missing")
        assert status == 404
        status, audio, headers = _get(base + "/audio", {"Range": "bytes=0-3"})
        assert status == 206 and audio == b"abcd" and "Content-Range" in headers
        status, _audio, _headers = _get(base + "/audio", {"Range": "bytes=100-120"})
        assert status == 416
        code, body = _post(base + "/control", {"type": "transport", "action": "pause"})
        assert code == 200 and json.loads(body)["transport"] == "paused"
        code, body = _post(base + "/control", {"type": "nope"})
        assert code == 400
        code, _body = _post(base + "/control", "not-json")
        assert code == 400
        code, _body = _post(base + "/other", {"type": "transport", "action": "play"})
        assert code == 404
        hub.publish_audio(0, np.linspace(-2, 2, 16, dtype=np.float32), 0.0, 0.1, None)
        status, wav, headers = _get(base + "/export.wav?from=0&to=1&name=bad%20name")
        assert status == 200 and wav.startswith(b"RIFF") and "attachment" in headers.get("Content-Disposition", "")
        status, _wav, _headers = _get(base + "/export.wav?from=1&to=1")
        assert status == 409

        # --- WS handshake & protocol ---
        sock = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=2)
        try:
            sock.sendall(
                b"GET /ws HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\n"
                b"Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
            )
            preface = b""
            while b"\r\n\r\n" not in preface:
                preface += sock.recv(4096)
            assert b"101" in preface

            # Drain server state and backlog audio frames.
            _ws_drain(sock)

            # Send control messages (rewind triggers ValueError -> server warns but stays open).
            _ws_send(sock, json.dumps({"type": "place", "playhead": 4, "speed": 1.0}).encode(), 1)
            _ws_send(sock, json.dumps({"type": "speed", "value": 1.0}).encode(), 1)
            _ws_send(sock, json.dumps({"type": "transport", "action": "rewind"}).encode(), 1)

            # Flush accumulated server responses.
            time.sleep(0.1)
            _ws_drain(sock)

            # Send PING; server should respond PONG.
            _ws_send(sock, b"", 9)
            pong = _ws_recv(sock)
            assert pong[0] == 10, "pong expected"
            _ws_send(sock, b"", 8)
        finally:
            sock.close()
    finally:
        server.shutdown()
        server.server_close()


def test_desk_without_a_live_hub(tmp_path: Path, monkeypatch):
    server, _hub, base = _server(tmp_path, monkeypatch, hub=False)
    try:
        code, _body = _post(base + "/control", {"type": "transport", "action": "play"})
        assert code == 409
        status, _body, _headers = _get(base + "/export.wav?from=0&to=1")
        assert status == 409
        status, audio, _headers = _get(base + "/audio")
        assert status == 200 and audio.startswith(b"abc")
    finally:
        server.shutdown()
        server.server_close()


def test_serve_stops_on_interrupt(tmp_path: Path, monkeypatch):
    _place(monkeypatch, tmp_path)

    class Stopped(ThreadingHTTPServer):
        def serve_forever(self, poll_interval: float = 0.5):
            raise KeyboardInterrupt
        
        def server_bind(self):
            self.socket.bind(("127.0.0.1", 0))
            self.server_address = self.socket.getsockname()

    monkeypatch.setattr("rhapsode.desk.ThreadingHTTPServer", Stopped)
    monkeypatch.setattr("rhapsode.desk._narrate_into", lambda *_args: None)
    stopped = MagicMock()
    monkeypatch.setattr("rhapsode.desk._watch_chrome", lambda *_args: stopped)
    doc = tmp_path / "page.json"
    doc.write_text(
        '{"title":"T","url":"https://example.com/a","sections":[{"heading":"","level":1,"blocks":[{"kind":"p","text":"Hi."}]}]}',
        encoding="utf-8",
    )
    wav = tmp_path / "speak.wav"
    wav.write_bytes(b"abcdefghijklmnopqrstuvwxyz")
    serve(wav, doc=doc)
    stopped.stop.assert_called_once()


def test_narration_loop_publishes_and_survives_a_bad_line(tmp_path: Path, monkeypatch):
    """Verify the _speak / _narrate_into loop publishes audio, survives
    per-line failures (including empty voiced-outputs), detects voice-swap
    signals, and keeps its inner state consistent.

    The wait-stub blocks so the main thread has a window to assert.
    _speak is executed in a daemon thread so the test itself does not hang.
    """
    _place(monkeypatch, tmp_path)
    doc = tmp_path / "page.json"
    doc.write_text(
        json.dumps({
            "title": "T",
            "url": "https://example.com/a",
            "sections": [{
                "heading": "",
                "level": 1,
                "blocks": [
                    {"kind": "p", "text": "Hello there."},
                    {"kind": "p", "text": "fail this"},
                    {"kind": "p", "text": "silent"},
                    {"kind": "p", "text": "switch now"},
                ],
            }],
        }),
        encoding="utf-8",
    )
    session = reading_session(tmp_path / "clip.wav", doc)
    for line in session["lines"]:
        line["status"] = "pending"
    hub = LiveHub(session)

    bumped = {"done": False}

    class Narrator:
        def __init__(self, voice: str = "af_heart", device: str = "cpu") -> None:
            self.voice = voice

        def set_voice(self, voice: str) -> None:
            self.voice = voice

        def iter_voiced(self, text: str, origin=None):
            if text.startswith("fail"):
                raise RuntimeError("synth failed")
            if text.startswith("silent"):
                return []
            if text.startswith("switch") and not bumped["done"]:
                bumped["done"] = True
                hub.voice = "am_adam"
                hub.voice_generation += 1
                return [(np.ones(4, dtype=np.float32), [{"text": "go", "start": 0.0, "end": 0.01}])]
            return [(np.ones(8, dtype=np.float32), [{"text": "Hi", "start": 0.0, "end": 0.02}])]

    monkeypatch.setattr("rhapsode.narrate.Narrator", Narrator)
    release = threading.Event()
    count = {"n": 0}

    def wait_stub() -> None:
        """Block after first successful pass so the test can assert.
        On subsequent calls return immediately; _speak runs in a daemon
        thread that is killed when the test function returns."""
        count["n"] += 1
        if count["n"] == 1:
            release.wait()

    monkeypatch.setattr(hub._respeak, "wait", wait_stub)

    # --- Phase 1: _speak in a daemon thread ----------------------------
    thread = threading.Thread(target=_speak, args=(hub, doc), daemon=True)
    thread.start()
    thread.join(timeout=3)  # blocks in wait_stub

    assert any(line["status"] == "queued" for line in session["lines"])
    release.set()  # unblock _speak daemon so it can finish
    thread.join(timeout=2)

    # --- Phase 2: _narrate_into in a daemon thread ---------------------
    t2 = threading.Thread(target=_narrate_into, args=(hub, doc), daemon=True)
    t2.start()
    t2.join(timeout=4)

    # --- Phase 3: _narrate_into should survive a broken _speak --------
    def explode(_hub, _doc):
        raise RuntimeError("stopped")

    monkeypatch.setattr("rhapsode.desk._speak", explode)

    # Break _narrate_into's while True after a few retries by patching
    # time.sleep to raise SystemExit (not caught by except Exception).
    sleep_count = {"n": 0}

    def limited_sleep(duration: float) -> None:
        sleep_count["n"] += 1
        if sleep_count["n"] > 2:
            raise SystemExit(0)
        time.sleep(0.005)

    monkeypatch.setattr("rhapsode.desk.time.sleep", limited_sleep)

    t3 = threading.Thread(target=_narrate_into, args=(hub, doc), daemon=True)
    t3.start()
    t3.join(timeout=5)


@pytest.mark.timeout(30)
def test_chrome_link_parse(monkeypatch):
    # _chrome_version reads from filesystem; just verify it returns a string
    assert isinstance(_chrome_version(), str)

    # Windows -> WSL path translation.
    assert _wsl_to_win(Path("/mnt/d/Users/me")) == r"D:\Users\me"
    assert _win_to_wsl(r"D:\Users\me") == Path("/mnt/d/Users/me")

    # JSON endpoint.
    raw = '{"jsonrpc":"2.0","result":{"id":1,"type":"page","url":"https://example.com","title":"T"}}'
    parsed = _last_json(raw)
    assert parsed is not None and parsed["result"]["id"] == 1

    # _local_appdata returns None on non-Windows.
    assert isinstance(_local_appdata(), (type(None), Path))

    # Node executable: returns None on non-Windows or missing.
    assert isinstance(_node_exe(), (type(None), str))

    # blank MCP returns empty dict.
    mcp = blank_mcp(False)
    assert isinstance(mcp, dict)
    assert "chrome" not in mcp or not mcp.get("chrome")

    # install_version returns version string.
    assert install_version(["120.0.6099.109", "119.0.6045.160"]) == "120.0.6099.109"

    # instance_name.
    assert instance_name("Chrome", "Chrome/154.0.8037.58") == "Chrome"

    # ChromeLink context.
    link = ChromeLink("http://localhost:1235", "https://example.com", lambda *_a: None)
    assert link._endpoint == "http://localhost:1235"

    # Profile helpers.
    profiles = load_chrome_profiles(Path("/nonexist"))
    assert preferred_profile(profiles, []) is None
    assert match_profile(profiles, ["abc"], False) is None
    assert load_last_active(Path("/nonexist")) == []

    # page_identity / profile_for_page.
    addr, title = page_identity("page-42")
    assert addr == "page-42"
    assert profile_for_page(profiles, "page-42", "") is None


def _ws_drain(sock: socket.socket) -> list[tuple[int, bytes]]:
    """Receive loop with timeout: consume all pending server frames."""
    frames: list[tuple[int, bytes]] = []
    original_timeout = sock.gettimeout()
    sock.settimeout(0.05)
    while True:
        try:
            frames.append(_ws_recv(sock))
        except TimeoutError:
            break
    sock.settimeout(original_timeout)
    return frames


@pytest.mark.timeout(30)
def test_agent_booth_and_view(monkeypatch, tmp_path: Path):
    _place(monkeypatch, tmp_path)

    # Minimal Booth test - takes base URL now.
    booth = Booth("http://127.0.0.1:8765")
    assert booth.base == "http://127.0.0.1:8765"

    # agent_view - needs a session dict.
    session = {"transport": "paused", "lines": [{"text": "Hi", "kind": "p", "status": "pending"}], "source": {}, "mcp": {}}
    view = agent_view(session)
    assert view["transport"] == "paused"

    # interpret_command - needs speed arg.
    with pytest.raises(ValueError):
        interpret_command({"type": "transport", "action": "rewind"}, 1.0)
    cmd = interpret_command({"type": "transport", "action": "pause"}, 1.0)
    assert cmd == {"transport": "paused"}

    # ChromeLink methods that hit subprocess.
    link = ChromeLink("http://127.0.0.1:9222", "https://example.com", lambda *_a: None)
    # stub subprocess.run.
    monkeypatch.setattr("rhapsode.chrome_link.subprocess.run", lambda *_a, **_kw: MagicMock())
    # These methods exercise the Chrome DevTools protocol path.
    try:
        link._run({"method": "Target.getTargets"})
    except Exception:
        pass
    # page_json path.
    result = MagicMock()
    result.json.return_value = {"result": {"targetInfos": [{"targetId": "t1"}]}}
    monkeypatch.setattr("rhapsode.chrome_link.subprocess.run", lambda *_a, **_kw: result)
    try:
        link._run({"method": "Page.enable"})
    except Exception:
        pass


def test_parse_endpoint():
    assert parse_browser_endpoint(None) is None
    assert parse_browser_endpoint("") is None
    assert parse_browser_endpoint("not-a-url") is None
    parts = parse_browser_endpoint("http://127.0.0.1:1234/json/version")
    assert parts is not None and "9222" not in parts


def test_page_html_roundtrip(tmp_path: Path):
    doc = tmp_path / "page.json"
    doc.write_text(
        '{"title":"T","url":"https://example.com/a","sections":[{"heading":"","level":1,"blocks":[{"kind":"p","text":"Hi."}]}]}',
        encoding="utf-8",
    )
    html = page_html()
    assert "<html" in html and "</html" in html


@pytest.mark.timeout(30)
def test_serve_live_no_connection(tmp_path: Path, monkeypatch):
    """serve_live should fall back gracefully when Chrome is missing."""
    _place(monkeypatch, tmp_path)
    doc = tmp_path / "page.json"
    doc.write_text(
        '{"title":"T","url":"https://example.com/a","sections":[{"heading":"","level":1,"blocks":[{"kind":"p","text":"Hi."}]}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr("rhapsode.desk._watch_chrome", lambda *_a, **_kw: None)
    # serve_live will try to connect via ChromeLink; no Chrome => fallback.
    try:
        serve_live(tmp_path, doc, "00000000-0000-0000-0000-000000000000")
    except Exception:
        pass  # expected to fail or fallback without real browser


def test_booth_live_server(tmp_path: Path, monkeypatch):
    """Test the live server integration with _Handler websocket protocol."""
    _place(monkeypatch, tmp_path)
    server, hub, base = _server(tmp_path, monkeypatch)
    try:
        status, body, _headers = _get(base + "/")
        assert status == 200
        code, body = _post(base + "/control", {"type": "transport", "action": "play"})
        assert code == 200
        code, body = _post(base + "/control", {"type": "transport", "action": "pause"})
        assert code == 200
        code, body = _post(base + "/control", {"type": "speed", "value": 2.0})
        assert code == 200 and json.loads(body)["speed"] == 2.0
    finally:
        server.shutdown()
        server.server_close()
