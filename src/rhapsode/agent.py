"""MCP server an agent uses to read and drive the live booth.

The booth keeps playing in the browser. This process speaks MCP on stdin and
forwards each tool call to that booth over HTTP.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .narrate import ENGLISH_VOICES


def interpret_command(msg: dict, speed: float) -> dict:
    """Turn an agent command into the fields the operator page should apply."""
    kind = msg.get("type")
    if kind == "transport":
        action = msg.get("action")
        if action not in {"play", "pause"}:
            raise ValueError("transport action must be play or pause")
        return {"transport": "playing" if action == "play" else "paused"}
    if kind == "speed":
        raw = msg.get("value")
        value = speed if raw is None else float(raw)
        if not 0.5 <= value <= 2.0:
            raise ValueError("speed must be between 0.5 and 2.0")
        return {"speed": value}
    if kind == "seek":
        seconds = float(msg.get("seconds") or 0)
        if seconds < 0:
            raise ValueError("seek cannot move before the start")
        return {"seek": seconds}
    if kind == "voice":
        voice = str(msg.get("value") or "")
        if voice not in ENGLISH_VOICES:
            raise ValueError(f"unknown voice {voice!r}")
        line = int(msg.get("line") or 0)
        if line < 0:
            raise ValueError("voice line cannot be negative")
        return {"voice": voice, "line": line}
    raise ValueError(f"unknown command {kind!r}")


def agent_view(session: dict) -> dict:
    """The slice of booth state an agent needs, without the whole script."""
    lines = session.get("lines") or []
    place = session.get("place") or {}
    playhead = place.get("playhead")
    current = _current_line(lines, playhead)
    source = session.get("source") or {}
    mcp = session.get("mcp") or {}
    label = source.get("label") or ""
    progress: dict[str, int] = {}
    for line in lines:
        status = line.get("status") or "pending"
        progress[status] = progress.get(status, 0) + 1
    spoken = lines[current] if current is not None else None
    return {
        "transport": session.get("transport") or "paused",
        "speed": session.get("speed"),
        "voice": session.get("voice"),
        "playhead": playhead,
        "title": session.get("title") or "",
        "page": source.get("detail") or source.get("identity") or label,
        "page_url": label if str(label).startswith("http") else "",
        "profile": mcp.get("profile") or "",
        "account": mcp.get("account") or "",
        "chrome_connected": bool(mcp.get("connected")),
        "chrome_active": bool(mcp.get("active")),
        "lines": len(lines),
        "progress": progress,
        "line": None
        if spoken is None
        else {
            "index": current,
            "kind": spoken.get("kind") or "",
            "status": spoken.get("status") or "pending",
            "text": spoken.get("text") or "",
        },
        "nearby": _nearby(lines, current),
    }


def _current_line(lines: list[dict], playhead: float | None) -> int | None:
    if playhead is not None:
        for index, line in enumerate(lines):
            start, end = line.get("start"), line.get("end")
            if start is None or end is None:
                continue
            if float(start) <= float(playhead) < float(end):
                return index
    for index, line in enumerate(lines):
        if line.get("status") == "rendering":
            return index
    return None


def _nearby(lines: list[dict], current: int | None) -> list[dict]:
    if current is None:
        return []
    window = []
    for index in range(max(0, current - 1), min(len(lines), current + 3)):
        line = lines[index]
        window.append({
            "index": index,
            "kind": line.get("kind") or "",
            "status": line.get("status") or "pending",
            "text": line.get("text") or "",
            "current": index == current,
        })
    return window


class Booth:
    """HTTP client for the operator desk the agent is driving."""

    def __init__(self, base: str = "http://127.0.0.1:8765") -> None:
        self.base = base.rstrip("/")

    def status(self) -> dict:
        return agent_view(self._json("GET", "/session"))

    def command(self, msg: dict) -> dict:
        return self._json("POST", "/control", msg)

    def voices(self) -> list[dict]:
        payload = self._json("GET", "/voices")
        voices = payload.get("voices") or []
        return voices if isinstance(voices, list) else []

    def _json(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"} if data else {}
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()
            try:
                detail = json.loads(detail).get("error") or detail
            except json.JSONDecodeError:
                pass
            raise RuntimeError(detail or f"booth returned {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("The booth is not running. Start it with: rhapsode live DOCUMENT") from exc
        if not isinstance(body, dict):
            raise RuntimeError("booth returned a response that was not an object")
        return body


def serve(base: str = "http://127.0.0.1:8765") -> None:
    """Speak MCP on stdin until the agent disconnects."""
    import asyncio

    from mcp.server.mcpserver import MCPServer

    booth = Booth(base)
    server = MCPServer(
        "rhapsode",
        instructions=(
            "Read and control the live Rhapsode booth. "
            "Call status to see the page, the line being spoken, transport, speed, voice, and Chrome. "
            "The booth must already be running."
        ),
    )

    @server.tool(description="What the booth is doing now: page, spoken line, transport, speed, voice, and Chrome.")
    def status() -> dict:
        return booth.status()

    @server.tool(description="Start playback in the open operator page.")
    def play() -> dict:
        return booth.command({"type": "transport", "action": "play"})

    @server.tool(description="Pause playback in the open operator page.")
    def pause() -> dict:
        return booth.command({"type": "transport", "action": "pause"})

    @server.tool(description="Set the playback speed. 1.0 is normal; the booth usually uses 0.8 to 1.6.")
    def speed(value: float) -> dict:
        return booth.command({"type": "speed", "value": value})

    @server.tool(description="Jump playback to a time in seconds on the 1.0× timeline.")
    def seek(seconds: float) -> dict:
        return booth.command({"type": "seek", "seconds": seconds})

    @server.tool(description="Skip forward or back by seconds from the saved playhead. Negative moves back.")
    def skip(seconds: float = 15) -> dict:
        playhead = float(booth.status().get("playhead") or 0)
        return booth.command({"type": "seek", "seconds": max(0.0, playhead + seconds)})

    @server.tool(description="Switch the Kokoro voice from the given line onward. Use a voice id such as af_heart.")
    def voice(name: str, line: int = 0) -> dict:
        return booth.command({"type": "voice", "value": name, "line": line})

    @server.tool(description="List the Kokoro voices the booth can speak with.")
    def voices() -> list[dict]:
        return booth.voices()

    asyncio.run(server.run_stdio_async())
