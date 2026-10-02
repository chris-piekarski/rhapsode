"""Page-extraction flow: open a URL in Chrome, evaluate extract.js, save JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from . import document, paths
from .mcp import McpClient

_EXTRACT_JS = Path(__file__).with_name("extract.js")


def _normalize(url: str) -> str:
    """Strip hash and trailing slash so we can match tabs against a target URL."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    query = parsed.query  # keep query string intact
    return f"{parsed.scheme}://{parsed.netloc}{path}?{query}" if query else f"{parsed.scheme}://{parsed.netloc}{path}"


async def _find_or_create_page(client: "McpClient", url: str) -> int:
    """Return a page ID for the target URL, reusing existing tabs when possible.

    If an open tab already holds the page (ignoring hash and trailing slash),
    reuse it.  Otherwise open a new tab at that URL.
    """
    target = _normalize(url)
    pages = await client.list_pages()

    for pg in pages:
        pg_url = pg.get("url", "")
        if _normalize(pg_url) == target:
            typer.echo(f"reusing tab → {pg_url}", err=True)
            return pg["id"]

    # No match — open a fresh page at the target URL
    typer.echo("opening new tab …", err=True)
    page_id = await client.new_page(url)
    return page_id


async def extract_page(url: str) -> Path:
    """Open *url* in Chrome, read the page text, save extractor JSON.

    Returns the path to the saved ``.json`` file inside ``inbox_dir()``.
    """
    js_source = _EXTRACT_JS.read_text(encoding="utf-8")

    async with McpClient() as client:
        page_id = await _find_or_create_page(client, url)

        # evaluate extract.js in the matched tab — source passed unchanged
        raw = await client.evaluate(page_id, js_source, wait_stable=True)

    # 4) parse result (server may embed JSON inside a text node)
    data = _unwrap_result(raw)

    # 5) validate by loading through document model
    doc = document.from_json(data)

    # 6) save to inbox
    inbox = paths.inbox_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    dest = inbox / (paths.slugify(doc.title) + ".json")
    dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return dest


# -- helpers ------------------------------------------------------------------

def _unwrap_result(raw: Any) -> dict:
    """Extract the inner JSON dict from an MCP tool result.

    The server may return:
      - a plain dict (already parsed)
      - a string that is pure JSON
      - a string that wraps a JSON payload (text prefix/suffix)
    """
    if isinstance(raw, dict):
        return raw

    if not isinstance(raw, str):
        # Last resort: grab a string representation
        raw = getattr(raw, "raw", str(raw))

    text = raw.strip()

    # Try direct parse first
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass

    # Scan for JSON object inside the string
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        try:
            data = json.loads(text[first : last + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # Double-encoded JSON string
    if text.startswith('"'):
        try:
            inner = json.loads(text)
            if isinstance(inner, str):
                data = json.loads(inner)
                if isinstance(data, dict):
                    return data
        except (json.JSONDecodeError, TypeError):
            pass

    raise ValueError(f"Could not parse extractor JSON from result: {text[:200]}")
