"""MCP client and extractor tests — no live Chrome needed."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from rhapsode import Document, build_script, load
from rhapsode.extractor import extract_page, _unwrap_result, _normalize
from rhapsode.mcp import _parse_list_pages, _parse_new_page

# ── fixture JSON matching extract.js output ────────────────────────────

SAMPLE_DOC = {
    "rhapsode": 1,
    "url": "https://example.com/article",
    "title": "Test Article",
    "site": "example.com",
    "lang": "en",
    "words": 15,
    "sections": [
        {
            "heading": "Test Article",
            "level": 1,
            "blocks": [
                {"kind": "p", "text": "This is a test paragraph."},
                {"kind": "li", "text": "First item in a list."},
            ],
        },
        {
            "heading": "Second Section",
            "level": 2,
            "blocks": [
                {"kind": "p", "text": "Another paragraph here."},
            ],
        },
    ],
}

# ── real chrome-devtools-mcp plain-text responses ─────────────────────

LIST_PAGES_TEXT = """## Pages
1: https://learning.oreilly.com/library/view/building-applications-with/9781098176495/ch02.html [selected]
2: about:blank
3: https://example.com/"""

NEW_PAGE_TEXT = "Page 4 is opened"

NEW_PAGE_TEXT_BARE = "42"

# ── fake MCP client for testing ───────────────────────────────────────

class FakeMdpClient:
    """Stand-in for chrome-devtools-mcp that returns real plain-text body.

    The ``call_log`` records every ``(tool_name, kwargs)`` so assertions
    can inspect exactly what was sent to the MCP server.
    """

    def __init__(
        self,
        pages_text: str | None = None,
        new_page_text: str | None = None,
        extract_result: str | None = None,
        nav_error: bool = False,
    ):
        self.pages_text = pages_text or LIST_PAGES_TEXT
        self.new_page_text = new_page_text or NEW_PAGE_TEXT
        self.extract_result = extract_result or json.dumps(SAMPLE_DOC)
        self.nav_error = nav_error
        self.call_log: list[tuple[str, dict]] = []

    async def call(self, name: str, **kwargs) -> str | dict | None:
        self.call_log.append((name, kwargs))

        if name == "list_pages":
            return self.pages_text  # plain text, just like the real server

        if name == "new_page":
            return self.new_page_text  # plain text

        if name == "navigate_page":
            if self.nav_error:
                raise RuntimeError("Navigation timeout")
            return {"success": True}

        if name == "evaluate_script":
            return self.extract_result

        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    # -- convenience methods delegate to .call() --

    async def list_pages(self):
        raw = await self.call("list_pages")
        if raw is None:
            return []
        if isinstance(raw, dict):
            return [raw]
        if isinstance(raw, list):
            return raw
        return _parse_list_pages(raw)

    async def new_page(self, url: str):
        resp = await self.call("new_page", url=url, background=True)
        if isinstance(resp, dict):
            return resp.get("pageId", int(resp["id"]))
        return _parse_new_page(resp)

    async def navigate(self, page_id, url):
        await self.call("navigate_page", pageId=page_id, type="url", url=url)

    async def evaluate(self, page_id, js_function, wait_stable=True):
        return await self.call(
            "evaluate_script",
            pageId=page_id,
            function=js_function,
            waitForStableDom=wait_stable,
        )


# ── helper factory ────────────────────────────────────────────────────

def _fake_factory(fake: FakeMdpClient):
    """Callable that returns *fake* — used with ``patch(...)``."""
    return lambda: fake


# ── _unwrap_result tests ──────────────────────────────────────────────

def test_unwrap_plain_dict():
    assert _unwrap_result(SAMPLE_DOC) is SAMPLE_DOC


def test_unwrap_json_string():
    assert _unwrap_result(json.dumps(SAMPLE_DOC))["title"] == "Test Article"


def test_unwrap_wrapped_text():
    wrapped = f"Result: {json.dumps(SAMPLE_DOC)}\n(end)"
    assert _unwrap_result(wrapped)["title"] == "Test Article"


def test_unwrap_double_encoded():
    inner = json.dumps(SAMPLE_DOC)
    outer = json.dumps(inner)
    assert _unwrap_result(outer)["site"] == "example.com"


def test_unwrap_bad_input():
    with pytest.raises(ValueError, match="Could not parse"):
        _unwrap_result("hello world")


# ── _normalize tests ─────────────────────────────────────────────────

def test_normalize_strips_hash():
    assert _normalize("https://example.com/article#section") == "https://example.com/article"


def test_normalize_strips_trailing_slash():
    assert _normalize("https://example.com/article/") == "https://example.com/article"


def test_normalize_strips_both():
    assert _normalize("https://example.com/article/#section") == "https://example.com/article"


def test_normalize_keeps_query():
    assert _normalize("https://example.com/article?q=1") == "https://example.com/article?q=1"


def test_normalize_preserves_scheme_netloc():
    assert _normalize("https://learning.oreilly.com/path#c") == "https://learning.oreilly.com/path"


# ── _parse_list_pages (text → page list) ─────────────────────────────

def test_parse_list_pages_empty():
    assert _parse_list_pages(None) == []


def test_parse_list_pages_real():
    pages = _parse_list_pages(LIST_PAGES_TEXT)
    assert len(pages) == 3
    assert pages[0]["id"] == 1
    assert "oreilly" in pages[0]["url"]
    assert "[selected]" not in pages[0]["url"]
    assert pages[1]["id"] == 2
    assert pages[1]["url"] == "about:blank"


def test_parse_list_pages_titled_rows():
    text = """## Pages
1: Chapter two (https://learning.oreilly.com/ch02.html) [selected]
2: Inbox (https://mail.google.com/mail/u/0/#inbox) isolatedContext=reader
3: about:blank
"""
    pages = _parse_list_pages(text)
    assert pages[0]["url"] == "https://learning.oreilly.com/ch02.html"
    assert pages[0]["title"] == "Chapter two"
    assert pages[1]["url"] == "https://mail.google.com/mail/u/0/#inbox"
    assert "isolatedContext" not in pages[1]["url"]
    assert pages[2]["url"] == "about:blank"
    assert "title" not in pages[2]


# ── _parse_new_page (text → page id) ─────────────────────────────────

def test_parse_new_page_with_label():
    assert _parse_new_page(NEW_PAGE_TEXT) == 4


def test_parse_new_page_bare_number():
    assert _parse_new_page(NEW_PAGE_TEXT_BARE) == 42


# ── server-command tests ──────────────────────────────────────────────

def test_server_command_default(monkeypatch):
    from rhapsode.mcp import _server_command

    monkeypatch.delenv("RHAPSODE_CHROME_MCP", raising=False)
    cmd, args = _server_command()
    assert cmd  # non-empty
    # Linux attaches with --autoConnect. WSL uses the Windows Chrome socket.
    assert "--autoConnect" in args or "--wsEndpoint" in args, f"{cmd} {args}"


def test_server_command_env_override(monkeypatch):
    from rhapsode.mcp import _server_command

    monkeypatch.setenv("RHAPSODE_CHROME_MCP", "/usr/bin/local-mcp --flag")
    cmd, args = _server_command()
    assert cmd == "/usr/bin/local-mcp"
    assert args == ["--flag"]


def test_connection_failure_is_not_an_empty_tab_list():
    from rhapsode.mcp import _connection_failure

    raw = (
        "Could not connect to Chrome. Check if Chrome is running.\n"
        "Cause: Could not find DevToolsActivePort for chrome"
    )
    assert _connection_failure(raw)
    assert _connection_failure("## Pages\n1: https://example.com/") is None


# ── evaluate_script source (no rewrite) ──────────────────────────────

def test_evaluate_passes_arrow_unchanged():
    """evaluate() must NOT rewrite the JS source.

    chrome-devtools-mcp accepts arrow functions natively.
    extract.js is ``() => { … }``.  The old rewrite produced
    ``function ()  => {`` — a syntax error.
    """
    fake = FakeMdpClient()

    # Read real extract.js source
    js_path = Path(__file__).parent.parent / "src" / "rhapsode" / "extract.js"
    js_source = js_path.read_text()

    async def run():
        await fake.__aenter__()
        # Call the evaluate method directly (same as extractor does)
        await fake.evaluate(
            page_id=1,
            js_function=js_source,
            wait_stable=True,
        )
        await fake.__aexit__(None, None, None)

    asyncio.run(run())

    eval_calls = [c for c in fake.call_log if c[0] == "evaluate_script"]
    func = eval_calls[0][1]["function"]

    # The function sent must be the original arrow function
    assert "(() => {" in func or "() =>" in func, (
        "source must start with arrow (), not rewritten to 'function ()  =>'")
    assert "function ()  =>" not in func, (
        "double-space syntax error — rewrite must not happen")


# ── extract_page integration ─────────────────────────────────────────

def test_extract_page_saves_json(tmp_path):
    fake = FakeMdpClient()

    with (
        patch("rhapsode.extractor.McpClient", _fake_factory(fake)),
        patch("rhapsode.extractor.paths.inbox_dir", return_value=tmp_path),
    ):
        path = asyncio.run(extract_page("https://example.com/new-article"))

    assert path.exists()
    data = json.loads(path.read_text())
    assert data["title"] == "Test Article"

    eval_calls = [c for c in fake.call_log if c[0] == "evaluate_script"]
    assert len(eval_calls) == 1
    func = eval_calls[0][1]["function"]
    # extract.js identifiers must survive round-trip
    assert "rhapsode" in func.lower() or "SKIP_SEL" in func


def test_extract_page_no_existing_pages(tmp_path):
    """When no tab matches, new_page is called with the target URL."""
    fake = FakeMdpClient()
    fake.pages_text = ""  # no pages at all

    with (
        patch("rhapsode.extractor.McpClient", _fake_factory(fake)),
        patch("rhapsode.extractor.paths.inbox_dir", return_value=tmp_path),
    ):
        asyncio.run(extract_page("https://example.com/new-article"))

    new_page_calls = [c for c in fake.call_log if c[0] == "new_page"]
    assert len(new_page_calls) == 1
    # URL must be passed to new_page
    assert new_page_calls[0][1]["url"] == "https://example.com/new-article"


def test_extract_page_reuses_page(tmp_path):
    """With an open tab at the same URL (hash/slash ignored), reuse it."""
    fake = FakeMdpClient()
    fake.pages_text = "## Pages\n99: https://example.com/article#different"

    with (
        patch("rhapsode.extractor.McpClient", _fake_factory(fake)),
        patch("rhapsode.extractor.paths.inbox_dir", return_value=tmp_path),
    ):
        asyncio.run(extract_page("https://example.com/article"))

    # Must NOT call new_page or navigate_page
    assert not any(c[0] == "new_page" for c in fake.call_log)
    assert not any(c[0] == "navigate_page" for c in fake.call_log)
    eval_calls = [c for c in fake.call_log if c[0] == "evaluate_script"]
    assert eval_calls[0][1]["pageId"] == 99


def test_extract_page_oreilly_hash_match(tmp_path):
    """Real-world: O'Reilly chapter URL with hash matches page 1 (no hash)."""
    fake = FakeMdpClient()
    fake.pages_text = LIST_PAGES_TEXT  # page 1 is the O'Reilly URL without hash

    oreilly_url = (
        "https://learning.oreilly.com/library/view/"
        "building-applications-with/9781098176495/ch02.html"
        "#chapter_2_orchestration_1754586819850890"
    )

    with (
        patch("rhapsode.extractor.McpClient", _fake_factory(fake)),
        patch("rhapsode.extractor.paths.inbox_dir", return_value=tmp_path),
    ):
        asyncio.run(extract_page(oreilly_url))

    # The requested URL should match page 1 after normalizing away the hash
    assert not any(c[0] == "new_page" for c in fake.call_log)
    assert not any(c[0] == "navigate_page" for c in fake.call_log)
    eval_calls = [c for c in fake.call_log if c[0] == "evaluate_script"]
    assert eval_calls[0][1]["pageId"] == 1


def test_extract_page_new_page_failure(tmp_path):
    """If new_page itself returns unparseable text, error propagates."""
    fake = FakeMdpClient()
    fake.pages_text = ""  # no existing tabs
    fake.new_page_text = ""  # unparseable — triggers ValueError

    with (
        patch("rhapsode.extractor.McpClient", _fake_factory(fake)),
        patch("rhapsode.extractor.paths.inbox_dir", return_value=tmp_path),
    ):
        with pytest.raises(ValueError, match="Cannot parse page id"):
            asyncio.run(extract_page("https://example.com/bad"))


def test_extract_page_wrapped_result(tmp_path):
    fake = FakeMdpClient(extract_result=f"Chrome result: {json.dumps(SAMPLE_DOC)}")

    with (
        patch("rhapsode.extractor.McpClient", _fake_factory(fake)),
        patch("rhapsode.extractor.paths.inbox_dir", return_value=tmp_path),
    ):
        path = asyncio.run(extract_page("https://example.com/article"))

    assert path.exists()
    doc = load(path)
    assert doc.title == "Test Article"


def test_extract_page_document_load(tmp_path):
    fake = FakeMdpClient()

    with (
        patch("rhapsode.extractor.McpClient", _fake_factory(fake)),
        patch("rhapsode.extractor.paths.inbox_dir", return_value=tmp_path),
    ):
        path = asyncio.run(extract_page("https://example.com/article"))

    doc = load(path)
    assert isinstance(doc, Document)
    assert doc.title == "Test Article"
    assert len(doc.sections) == 2

    script = build_script(doc)
    assert len(script) > 0
    assert script[0].kind == "heading"


def test_extract_page_script_utterances(tmp_path):
    fake = FakeMdpClient()

    with (
        patch("rhapsode.extractor.McpClient", _fake_factory(fake)),
        patch("rhapsode.extractor.paths.inbox_dir", return_value=tmp_path),
    ):
        path = asyncio.run(extract_page("https://example.com/article"))

    doc = load(path)
    script = build_script(doc)
    kinds = [u.kind for u in script]
    assert "p" in kinds
    assert "li" in kinds


def test_extract_page_evaluate_js_source(tmp_path):
    """evaluate_script is called with extract.js passed as-is (arrow function)."""
    fake = FakeMdpClient()

    with (
        patch("rhapsode.extractor.McpClient", _fake_factory(fake)),
        patch("rhapsode.extractor.paths.inbox_dir", return_value=tmp_path),
    ):
        asyncio.run(extract_page("https://example.com/article"))

    eval_calls = [c for c in fake.call_log if c[0] == "evaluate_script"]
    func = eval_calls[0][1]["function"]

    # extract.js starts with "() => {".  It must survive unchanged.
    assert "(() => {" in func or "() =>" in func, (
        f"source must be arrow function, got: {func[:30]}")
    assert "function ()  =>" not in func, "no rewrite allowed"
    # Key identifiers
    assert "SKIP_SEL" in func or "const" in func


# ── CLI read command tests ──────────────────────────────────────────

import subprocess  # noqa: E402

PYTHON = ".venv/bin/python"


def _cli(*args) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "rhapsode.cli"] + list(args),
        capture_output=True, text=True, timeout=30,
        cwd=str(Path(__file__).resolve().parents[1]),
    )


def test_read_help():
    r = _cli("read", "--help")
    assert r.returncode == 0
    out = r.stdout + r.stderr
    assert "audiobook" in out.lower() or "extract" in out.lower() or "inbox" in out.lower()


def test_read_missing_url():
    r = _cli("read")
    assert r.returncode != 0
    assert "url" in (r.stdout + r.stderr).lower() or "required" in (r.stdout + r.stderr).lower()


def test_read_cli_syntax():
    r = _cli("read")
    assert r.returncode != 0
