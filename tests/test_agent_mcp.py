"""The agent MCP tools, spoken to a real booth HTTP server. No Chrome and no Kokoro."""

from __future__ import annotations

import asyncio
import json
from functools import partial
from pathlib import Path

import numpy as np

from rhapsode.agent import build_server
from rhapsode.desk import LiveHub, ThreadingHTTPServer, _Handler, reading_session


EXPECTED = {
    "status",
    "play",
    "pause",
    "speed",
    "seek",
    "skip",
    "voice",
    "voices",
    "lines",
    "seek_line",
    "tabs",
    "open_tab",
    "export",
}


def _server(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rhapsode.desk.place_file", lambda _label: tmp_path / "place.json")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"abcdefghijklmnopqrstuvwxyz")
    session = reading_session(wav, page_url="https://example.com/ch")
    session["lines"] = [
        {"text": "Hello.", "kind": "p", "start": 0.0, "end": 2.0, "status": "done"},
        {"text": "Next.", "kind": "p", "start": None, "end": None, "status": "pending"},
    ]
    hub = LiveHub(session)
    hub.audio = [({"index": 0}, np.zeros(8, dtype="<f4").tobytes())]
    handler = partial(_Handler, wav=wav, session=session, hub=hub)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return server, hub, base


def _payload(result) -> dict:
    assert result.is_error is False, result
    body = json.loads(result.content[0].text)
    assert isinstance(body, dict), body
    structured = result.structured_content
    if isinstance(structured, dict):
        assert structured == body
    return body


def _error(result) -> str:
    assert result.is_error is True, result
    return result.content[0].text


def test_mcp_tools_cover_the_booth(tmp_path: Path, monkeypatch):
    server, hub, base = _server(tmp_path, monkeypatch)
    mcp = build_server(base)

    def fake_tabs():
        return [{"id": 7, "url": "https://example.com/chapter", "title": "Chapter"}]

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

    monkeypatch.setattr("rhapsode.desk.list_chrome_tabs", fake_tabs)
    monkeypatch.setattr("rhapsode.desk.read_chrome_tab", fake_read)

    from mcp.types import CallToolRequestParams

    async def call(name: str, arguments: dict | None = None):
        # The stdio handler returns this result. call_tool() raises instead.
        return await mcp._handle_call_tool(
            None, CallToolRequestParams(name=name, arguments=arguments or {})
        )

    async def exercise() -> None:
        names = {tool.name for tool in await mcp.list_tools()}
        assert names == EXPECTED

        hub.remember_place(1.0, 1.0)
        status = _payload(await call("status"))
        assert status["line"]["index"] == 0
        assert status["line"]["text"] == "Hello."
        assert status["playhead"] == 1.0

        lines = _payload(await call("lines", {"start": 0, "count": 20}))["lines"]
        assert [line["text"] for line in lines] == ["Hello.", "Next."]
        assert lines[0]["start"] == 0.0
        assert lines[1]["start"] is None

        missing = _error(await call("seek_line", {"index": 1}))
        assert "no audio yet" in missing
        jumped = _payload(await call("seek_line", {"index": 0}))
        assert jumped["playhead"] == 0.0

        skipped = _payload(await call("skip", {"seconds": -5}))
        assert skipped["playhead"] == 0.0
        hub.remember_place(12.0, 1.0)
        skipped = _payload(await call("skip", {"seconds": 15}))
        assert skipped["playhead"] == 27.0

        hub.remember_place(1.0, 1.0)
        voiced = _payload(await call("voice", {"name": "bf_emma"}))
        assert voiced["voice"] == "bf_emma"
        assert hub.cut_from == 0
        voiced = _payload(await call("voice", {"name": "af_heart", "line": 1}))
        assert hub.cut_from == 1
        assert hub.voice == "af_heart"

        playing = _payload(await call("play", {}))
        assert playing["transport"] == "playing"
        paused = _payload(await call("pause", {}))
        assert paused["transport"] == "paused"
        sped = _payload(await call("speed", {"value": 1.25}))
        assert sped["speed"] == 1.25
        sought = _payload(await call("seek", {"seconds": 1.5}))
        assert sought["playhead"] == 1.5

        voices = _payload(await call("voices", {}))["voices"]
        assert any(voice["id"] == "af_heart" for voice in voices)
        assert len(voices) == 28

        empty = _error(await call("export", {"start": 1, "end": 2}))
        assert "no audio" in empty
        saved = _payload(await call("export", {"start": 0, "end": 1, "name": "chapter.wav"}))
        path = Path(saved["path"])
        assert path.is_file()
        assert path.read_bytes().startswith(b"RIFF")
        assert saved["included"] == 1
        assert saved["requested"] == 1
        assert saved["partial"] is False
        assert saved["bytes"] == path.stat().st_size
        assert str(tmp_path) in str(path)

        hub.place = None
        blind = _error(await call("skip", {}))
        assert "has not reported a playhead" in blind
        hub.remember_place(1.0, 1.0)

        tabs = _payload(await call("tabs", {}))["tabs"]
        assert tabs[0]["url"] == "https://example.com/chapter"

        refused = _error(await call("open_tab", {"url": "ftp://no"}))
        assert "http" in refused
        opened = _payload(await call("open_tab", {"url": "https://example.com/chapter"}))
        assert opened["title"] == "Picked chapter"
        assert hub.session["title"] == "Picked chapter"

    try:
        asyncio.run(exercise())
    finally:
        server.shutdown()
        server.server_close()
