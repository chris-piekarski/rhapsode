"""Lightweight MCP stdio client — talks to chrome-devtools-mcp over stdin/stdout.

Only exposes the four tools the Rhapsode page-extraction flow needs:
  list_pages, new_page, navigate_page, evaluate_script.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# Lazy imports — the ``mcp`` package has pydantic compat issues that block
# module-level import during test collection.  We only need these at runtime.

_MCP_SCRIPT = (
    "chrome-devtools-mcp/build/src/bin/chrome-devtools-mcp.js"
)
_SKIP_WINDOWS_USERS = {"Public", "Default", "Default User", "All Users"}


def _wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def _windows_devtools_endpoint() -> str | None:
    """WebSocket of the Windows Chrome the user is actually looking at.

    ``chrome-devtools-mcp --autoConnect`` dials ``ws://localhost``, and on this
    machine that name resolves to ``::1``. A headless Chrome already owns that
    address, so the attach lands on the wrong browser and the real tabs, on
    ``127.0.0.1``, never appear. The port file names the right socket.
    """
    users = Path("/mnt/c/Users")
    if not users.is_dir():
        return None
    files = [
        path
        for path in users.glob("*/AppData/Local/Google/Chrome/User Data/DevToolsActivePort")
        if path.parts[4] not in _SKIP_WINDOWS_USERS
    ]
    if not files:
        return None
    port_file = max(files, key=lambda path: path.stat().st_mtime)
    lines = [
        line.strip()
        for line in port_file.read_text(encoding="ascii", errors="replace").splitlines()
        if line.strip()
    ]
    if len(lines) < 2 or not lines[0].isdigit() or not lines[1].startswith("/devtools/"):
        return None
    return f"ws://127.0.0.1:{lines[0]}{lines[1]}"


def _windows_node() -> str | None:
    for candidate in (
        Path("/mnt/c/Program Files/nodejs/node.exe"),
        Path("/mnt/c/Program Files (x86)/nodejs/node.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _node_arg(path: Path) -> str:
    """Argument form Windows Node keeps when the parent is WSL.

    WSL rewrites a ``/mnt/c/...`` argument into a ``\\\\wsl.localhost\\\\...\\\\mnt\\\\c``
    path, and Node cannot load the module from there. ``C:/...`` is passed
    through. A ``/home/...`` path is fine: WSL rewrites that one once.
    """
    mount = re.match(r"^/mnt/([a-zA-Z])/(.*)$", path.as_posix())
    if mount:
        return f"{mount.group(1).upper()}:/{mount.group(2)}"
    return str(path)


def _mcp_script() -> str | None:
    """chrome-devtools-mcp entrypoint Node can execute.

    Prefer the Windows npm cache. A UNC path from the Linux cache also loads,
    but the native path is the one Windows Node already runs.
    """
    found: list[Path] = []
    roots = (
        Path("/mnt/c/Users").glob(
            f"*/AppData/Local/npm-cache/_npx/*/node_modules/{_MCP_SCRIPT}"
        ),
        Path.home().glob(f".npm/_npx/*/node_modules/{_MCP_SCRIPT}"),
    )
    for group in roots:
        found.extend(group)
    native = [path for path in found if str(path).startswith("/mnt/")]
    pool = native or found
    if not pool:
        return None
    return _node_arg(max(pool, key=lambda path: path.stat().st_mtime))


def _windows_server_command() -> tuple[str, list[str]] | None:
    """Run chrome-devtools-mcp where ``127.0.0.1`` is Windows, on the live socket."""
    if not _wsl():
        return None
    endpoint = _windows_devtools_endpoint()
    node = _windows_node()
    script = _mcp_script()
    if not endpoint or not node or not script:
        return None
    return node, [script, "--wsEndpoint", endpoint, "--no-usage-statistics"]


def _server_command() -> tuple[str, list[str]]:
    """Return (command, args) for the MCP server. Override via RHAPSODE_CHROME_MCP.

    Default includes ``--autoConnect`` so we attach to an already-running
    Chrome instance instead of launching a fresh one that can't see existing tabs.
    Under WSL that default dials Linux ``localhost`` and misses Windows Chrome,
    so the Windows Node build is used instead when it can see the port file.
    """
    raw = os.environ.get("RHAPSODE_CHROME_MCP", "").strip()
    if not raw:
        windows = _windows_server_command()
        if windows is not None:
            return windows
        raw = "npx -y chrome-devtools-mcp@latest --autoConnect"

    # Allow a bare path (no args)
    if "/" in raw and " " not in raw:
        return raw, []

    import shlex

    tokens = shlex.split(raw)
    return tokens[0], tokens[1:]


# ---------------------------------------------------------------------------
# Plain-text helpers — chrome-devtools-mcp tools return human-readable text,
# not JSON.  We must parse the responses.
# ---------------------------------------------------------------------------

def _parse_list_pages(text: str | None) -> list[dict[str, Any]]:
    """Parse the ``list_pages`` plain-text response into [{id, url, title?}, ...].

    Typical body::

        ## Pages
        1: https://example.com/foo [selected]
        2: Chapter title (https://example.com/bar)
    """
    if text is None:
        return []

    pages: list[dict[str, Any]] = []
    for entry in re.finditer(r"^(\d+):\s+(.+)$", text, re.MULTILINE):
        url, title = _page_target(entry.group(2).strip())
        page: dict[str, Any] = {"id": int(entry.group(1)), "url": url}
        if title:
            page["title"] = title
        pages.append(page)
    return pages


def _page_target(body: str) -> tuple[str, str]:
    """Split one page line into ``(url, title)``.

    A titled row looks like ``Chapter (https://example.com/a) [selected]``.
    A bare row is just the URL. ``isolatedContext=`` is a tool annotation.
    """
    body = re.sub(r"\s+isolatedContext=\S+$", "", body).strip()
    if body.endswith(" [selected]"):
        body = body[: -len(" [selected]")].strip()
    match = re.search(r" \(([^()\s]+)\)$", body)
    captured = match.group(1) if match else ""
    if captured.startswith(("http://", "https://", "about:", "chrome:", "file:", "data:")):
        return captured, body[: match.start()].strip() if match else ""
    return body, ""


def _connection_failure(text: str) -> str | None:
    """User-facing text when the tool reports that Chrome could not be attached."""
    if "Could not connect to Chrome" in text or "Could not find DevToolsActivePort" in text:
        return (
            "Chrome is open, but remote debugging is not available. "
            "Open chrome://inspect/#remote-debugging, turn it on, and choose the tab again."
        )
    return None


def _parse_new_page(text: str) -> int:
    """Parse the page id from the ``new_page`` text response.

    Typical body: ``Page 14 is opened``  or ``14`` alone.
    """
    # Try "Page <N> is opened"
    m = re.search(r"Page\s+(\d+)", text)
    if m:
        return int(m.group(1))
    # Try bare number
    m = re.search(r"^(\d+)$", text.strip())
    if m:
        return int(m.group(1))
    raise ValueError(f"Cannot parse page id from new_page response: {text!r}")


# ---------------------------------------------------------------------------

class McpClient:
    """Context-managed, stdio-backed MCP client for chrome-devtools-mcp server.

    Usage::

        async with McpClient() as client:
            pages = await client.list_pages()
            page_id = await client.new_page(url)
    """

    def __init__(self) -> None:
        from mcp import StdioServerParameters

        cmd, args = _server_command()
        self._params = StdioServerParameters(command=cmd, args=args)
        self._session: Any = None  # ClientSession | None, lazily loaded

    async def __aenter__(self) -> "McpClient":
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        self._stdio = stdio_client(self._params)
        self._read, self._write = await self._stdio.__aenter__()
        self._session = ClientSession(self._read, self._write)
        await self._session.__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._session:
            await self._session.__aexit__(*exc)
        if "_stdio" in self.__dict__:
            await self._stdio.__aexit__(*exc)

    # ── generic tool call ────────────────────────────────────────────────

    async def call(self, name: str, **kwargs: Any) -> str | dict | None:
        """Call an MCP tool by name.

        Returns:
            Parsed text (str), parsed JSON (dict), or None.
        """
        if self._session is None:
            raise RuntimeError("McpClient not initialized")

        result = await self._session.call_tool(name, kwargs)
        content = result.content  # list[TextContent | ...]
        if not content:
            return None
        first = content[0]
        raw = getattr(first, "text", str(first))

        # Try JSON, fall back to raw text (do NOT re-raise — text responses
        # from list_pages / new_page are not JSON)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    # ── convenience methods ──────────────────────────────────────────────

    async def list_pages(self) -> list[dict[str, Any]]:
        """Return list of open pages.  Each entry has ``id`` (int) and ``url`` (str)."""
        raw = await self.call("list_pages")
        if raw is None:
            return []
        if isinstance(raw, dict):
            # Edge: single-page JSON
            return [raw]
        if isinstance(raw, list):
            return raw
        # Plain text — parse it. A failed attach used to look like an empty list.
        pages = _parse_list_pages(raw)
        if not pages:
            failure = _connection_failure(raw)
            if failure:
                raise RuntimeError(failure)
        return pages

    async def new_page(self, url: str) -> int:
        """Open a new browser page at *url* and return its page id."""
        resp = await self.call("new_page", url=url, background=True)
        if isinstance(resp, dict):
            return resp.get("pageId", int(resp["id"]))
        if not isinstance(resp, str):
            raise ValueError(f"Cannot parse page id from new_page response: {resp!r}")
        return _parse_new_page(resp)

    async def navigate(self, page_id: int, url: str) -> None:
        """Navigate *page_id* to *url*."""
        await self.call("navigate_page", pageId=page_id, type="url", url=url)

    async def evaluate(
        self, page_id: int, js_function: str, wait_stable: bool = True
    ) -> Any:
        """Evaluate a JavaScript function expression in the given page context.

        ``js_function`` must be an arrow function (e.g. ``() => { … }``).
        The source is passed **unchanged** — chrome-devtools-mcp
        ``evaluate_script`` accepts arrow functions natively.
        """
        result = await self.call(
            "evaluate_script",
            pageId=page_id,
            function=js_function,
            waitForStableDom=wait_stable,
        )
        return result
