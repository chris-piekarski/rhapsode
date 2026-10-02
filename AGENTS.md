# Rhapsode — Agent Reference

Verified facts about this repository. Use this file as the single source of truth when working on Rhapsode code.

## What It Does

Rhapsode converts web pages and documents into narrated audiobooks. It reads a document (extracted from Chrome, or Markdown / plain text), runs it through Kokoro-82M TTS, optionally proofreads with faster-whisper, then encodes the WAV into an M4B with chapter markers.

A live operator desk (`desk.py` + `operator.html`) provides real-time playback via WebSocket with word-level timing for seek and highlight. An MCP server (`agent.py`) lets AI agents read and drive the live booth over stdio.

**Version**: `0.0.1` (from `pyproject.toml`). Tag: `v0.0.1`.

**License**: MIT — Copyright 2026 Chris Piekarski. See `LICENSE`.

## Build Commands

```bash
make lint       # ruff check + mypy (via .venv/bin/python -m …)
make test       # pytest with coverage (via .venv/bin/python -m pytest)
```

All tools live inside `.venv` as importable modules — **always** invoke them via `.venv/bin/python -m <tool>`. `black` and `isort` are not installed; `ruff` and `mypy` are the sole linters.

## Restrictions

- **No GPU inference in tests** — Kokoro TTS inference segfaults on CUDA in this environment. Tests mock `KPipeline` / `KModel`.
- **No speaker / ffplay in tests** — `PcmSpeaker` is mocked; do not invoke real audio output.
- **No live booth in tests** — `desk.py` routes are tested via lightweight HTTP client mocks, not a running server.
- **No Chrome / live MCP in CI** — Chrome DevTools MCP is mocked for tests; WSL2 network isolation prevents real connections.
- **Lazy imports** — `torch`, `faster_whisper`, and `mcp` are imported only inside methods, never at module level.

## Key Modules

| file | role |
|------|------|
| `desk.py` | Operator HTTP + WebSocket server — live booth |
| `agent.py` | Rhapsode MCP server (`serve()`) — AI agents drive the booth |
| `mcp.py` | Chrome DevTools MCP **client** (`McpClient`) — reads page content |
| `cache.py` | Disk cache for Kokoro TTS synthesis results (SQLite + NumPy, no Kokoro imports) |
| `cli.py` | Typer CLI entry point (`rhapsode run`, `rhapsode live`, …) |
| `narrate.py` | Kokoro inference, silence tightening, `PcmSpeaker` |
| `paths.py` | Directory resolution (WSL interop), `speech_cache_dir()` |

## Operator HTTP Server

The live desk serves on `http://127.0.0.1:8765` by default.

**Launch**: `rhapsode live DOCUMENT.json` (CLI `live` command → `desk.serve_live()`).

**Static mode**: `rhapsode play output.wav` (CLI `play` command → `desk.serve()`).

## Rhapsode MCP Server

`agent.serve()` runs on stdio. It speaks the [Model Context Protocol](https://modelcontextprotocol.io/) and provides 8 tools that forward to the operator desk over HTTP:

| tool | params | routes to |
|------|--------|-----------|
| `status` | — | `GET /session` → `agent.agent_view()` |
| `play` | — | `POST /control` `{type: transport, action: play}` |
| `pause` | — | `POST /control` `{type: transport, action: pause}` |
| `speed` | `value: float` (0.5–2.0) | `POST /control` `{type: speed, value: N}` |
| `seek` | `seconds: float` | `POST /control` `{type: seek, seconds: N}` |
| `skip` | `seconds: float` (default 15) | computes absolute → `POST /control` `{type: seek, seconds: N}` |
| `voice` | `name: str`, `line: int` (default 0) | `POST /control` `{type: voice, value: name, line: N}` |
| `voices` | — | `GET /voices` (28 English Kokoro voices) |

The MCP server base URL defaults to `http://127.0.0.1:8765`. Override via `agent.serve(base="…")`.

## Chrome DevTools MCP Client

`mcp.McpClient` is an **async context manager** that talks to the separate `chrome-devtools-mcp` stdio process for page extraction.

```python
async with McpClient() as client:
    pages = await client.list_pages()      # list open Chrome tabs
    page_id = await client.new_page(url)   # open a new tab
    await client.evaluate(page_id, js)     # execute JS, get result
```

Controlled by `RHAPSODE_CHROME_MCP` env var (default: `npx -y chrome-devtools-mcp@latest --autoConnect`).

## Speech Cache

- **Module**: `cache.py` — stdlib + NumPy only; **no Kokoro imports**.
- **Class**: `SpeechCache(root: Path, budget_bytes: int)`.
- **Default path**: `paths.speech_cache_dir()` → `~/.cache/rhapsode/speech/` (override: `RHAPSODE_SPEECH_CACHE` env var).
- **Default budget**: `RHAPSODE_SPEECH_CACHE_MB` env var, default **200 MB** (200 × 1 048 576 bytes).
- **Key**: `cache_key(voice, speed, text, sample_rate, repo)` — SHA-256 of compact JSON. Speed is rounded to 3 decimal places to handle float precision drift.
- **Storage**: SQLite for metadata, individual `.npy` blobs for audio. Atomic writes via `.npy.tmp` → `.npy` with `os.replace`. `threading.Lock` guards all DB and file ops.
- **Eviction**: LRU by `used` timestamp; when budget exceeded, oldest entries are trimmed.
- **Origins**: `(blob_key, page_url, section_index, utterance_index, tab_url)` — UNIQUE constraint. `page_title`, `section_heading`, and `kind` are stored but are not part of the unique key. `_migrate_origins` handles schema migration from prior version without `tab_url`.
- **`Narrator`**: `cache=None` (default) → uses default disk cache. `cache=False` → disabled. `cache=SpeechCache(…)` → explicit instance. In tests, always pass `cache=False` to isolate mocks.
- **Boundary**: `load_or_render(cache, …, render, origin)` is the testable boundary between cache and Kokoro rendering.

## Live Scope

Operator desk voice switching supports only the **28 English voices** (`ENGLISH_VOICES` in `narrate.py`).

## WSL Notes

`is_wsl()` returns `True` on WSL2. This affects:
- `output_dir()` → `~/Music/Rhapsode` on Windows side.
- `inbox_dir()` → `%LocalAppData%/Temp/rhapsode` on Windows side.
- `whisper_models_dir()` → looks on Windows side for `~/voxium/models`.
