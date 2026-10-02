"""MCP server an agent uses to read and drive the live booth.

The booth keeps playing in the browser. This process speaks MCP on stdin and
forwards each tool call to that booth over HTTP.
"""

from __future__ import annotations

import json
import os
import re
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

    def session(self) -> dict:
        """Raw GET /session response (includes full lines array)."""
        return self._json("GET", "/session")

    def command(self, msg: dict) -> dict:
        return self._json("POST", "/control", msg)

    def voices(self) -> list[dict]:
        payload = self._json("GET", "/voices")
        voices = payload.get("voices") or []
        return voices if isinstance(voices, list) else []

    def lines(self, start: int = 0, count: int = 20) -> list[dict]:
        """Return a slice of the script lines."""
        start = max(0, start)
        count = max(1, min(count, 100))
        sess = self._json("GET", "/session")
        all_lines = sess.get("lines") or []
        results = []
        for i, line in enumerate(all_lines[start : start + count]):
            results.append({
                "index": start + i,
                "kind": line.get("kind") or "",
                "status": line.get("status") or "pending",
                "text": line.get("text") or "",
                "start": line.get("start"),
                "end": line.get("end"),
            })
        return results

    def seek_line(self, index: int) -> dict:
        """Seek to the start of a specific line."""
        sess = self._json("GET", "/session")
        all_lines = sess.get("lines") or []
        if index < 0 or index >= len(all_lines):
            raise RuntimeError(f"line {index} is not in the script")
        line = all_lines[index]
        line_start = line.get("start")
        if line_start is None:
            raise RuntimeError(f"line {index} has no audio yet")
        return self.command({"type": "seek", "seconds": line_start})

    def tabs(self) -> list[dict]:
        """List open Chrome tabs."""
        tabs_resp = self._json("GET", "/tabs")
        tabs = tabs_resp.get("tabs") or []
        return tabs if isinstance(tabs, list) else []

    def open_tab(self, url: str) -> dict:
        """Open and read a Chrome tab by URL."""
        if not url.lower().startswith("http"):
            raise ValueError("Pick a tab by its http address.")
        return self._json("POST", "/tab", {"url": url})

    def skip(self, seconds: float = 15) -> dict:
        """Skip forward or back by seconds from the current playhead."""
        playhead = self.status().get("playhead")
        if playhead is None:
            raise RuntimeError("the booth has not reported a playhead yet")
        return self.command({"type": "seek", "seconds": max(0.0, float(playhead) + seconds)})

    def voice(self, name: str, line: int | None = None) -> dict:
        """Switch voice from a line onward. Resolves current line when line is None."""
        if line is None:
            current = self.status().get("line")
            if current is None or current.get("index") is None:
                raise RuntimeError("no current line; pass line=")
            line = current["index"]
        return self.command({"type": "voice", "value": name, "line": line})

    def export(self, start: int = 0, end: int = 0, name: str = "rhapsode.wav") -> dict:
        """Export spoken lines to a WAV file."""
        from .paths import work_dir

        url = f"/export.wav?from={start}&to={end}&name={name}"
        request = urllib.request.Request(self.base + url, method="GET")
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()
            try:
                detail = json.loads(detail).get("error") or detail
            except json.JSONDecodeError:
                pass
            raise RuntimeError(detail or f"booth returned {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("The booth is not running. Start it with: rhapsode live DOCUMENT") from exc

        body = response.read()
        lines_hdr = str(response.headers.get("X-Rhapsode-Lines", "0/0"))
        included_str, requested_str = lines_hdr.split("/", 1)
        included = int(included_str)
        requested = int(requested_str)
        partial = response.headers.get("X-Rhapsode-Partial") == "1"
        disp = response.headers.get("Content-Disposition", "")
        fname = name
        if "filename=" in disp:
            match = re.search(r'filename="([^"]+)"', disp)
            if match:
                fname = match.group(1)
        safe = os.path.basename(fname)
        dest_dir = work_dir("export")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / safe
        dest.write_bytes(body)
        return {
            "path": str(dest),
            "bytes": len(body),
            "included": included,
            "requested": requested,
            "partial": partial,
        }

    def _json(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"} if data else {}
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                body = json.loads(resp.read().decode())
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


def _reported(fn):
    """Turn a booth refusal into a tool error the agent can read.

    A bare ``RuntimeError`` is treated as a crash, and the MCP client only
    sees ``Error executing tool <name>``. The reason stays on the server.
    """
    from functools import wraps

    from mcp.server.mcpserver.tools.base import ToolError

    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (RuntimeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapped


def build_server(base: str = "http://127.0.0.1:8765"):
    """The MCP server the agent speaks to. `serve` runs it on stdin."""
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
    @_reported
    def status() -> dict:
        return booth.status()

    @server.tool(description="Start playback in the open operator page.")
    def play() -> dict:
        return booth.command({"type": "transport", "action": "play"})

    @server.tool(description="Pause playback in the open operator page.")
    def pause() -> dict:
        return booth.command({"type": "transport", "action": "pause"})

    @server.tool(description="Set the playback speed. 0.5 to 2.0.")
    @_reported
    def speed(value: float) -> dict:
        return booth.command({"type": "speed", "value": value})

    @server.tool(description="Jump playback to a time in seconds on the 1.0\u00d7 timeline.")
    @_reported
    def seek(seconds: float) -> dict:
        return booth.command({"type": "seek", "seconds": seconds})

    @server.tool(description="Skip forward or back by seconds from the saved playhead. Requires the booth to have reported playhead first.")
    @_reported
    def skip(seconds: float = 15) -> dict:
        return booth.skip(seconds)

    @server.tool(description="Switch the Kokoro voice from the current line onward. Use a voice id such as af_heart.")
    @_reported
    def voice(name: str, line: int | None = None) -> dict:
        return booth.voice(name, line)

    @server.tool(description="List the Kokoro voices the booth can speak with.")
    @_reported
    def voices() -> dict:
        return {"voices": booth.voices()}

    @server.tool(description="Return a slice of the spoken script with index, kind, status, text, start, and end.")
    @_reported
    def lines(start: int = 0, count: int = 20) -> dict:
        return {"lines": booth.lines(start, count)}

    @server.tool(description="Seek playback to the start of a specific line by its index.")
    @_reported
    def seek_line(index: int) -> dict:
        return booth.seek_line(index)

    @server.tool(description="List open Chrome tabs.")
    @_reported
    def tabs() -> dict:
        return {"tabs": booth.tabs()}

    @server.tool(description="Open and read a Chrome page by URL.")
    @_reported
    def open_tab(url: str) -> dict:
        return booth.open_tab(url)

    @server.tool(description="Export spoken lines to a WAV file.")
    @_reported
    def export(start: int = 0, end: int = 0, name: str = "rhapsode.wav") -> dict:
        return booth.export(start, end, name)

    return server


def serve(base: str = "http://127.0.0.1:8765") -> None:
    """Speak MCP on stdin until the agent disconnects."""
    import asyncio

    asyncio.run(build_server(base).run_stdio_async())
