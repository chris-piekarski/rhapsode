"""Operator desk: play, pause, speed, and the text being read."""

from __future__ import annotations

import base64
import functools
import hashlib
import io
import json
import sys
import threading
import time
import traceback
import wave
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .chrome_link import ChromeLink, blank_mcp, page_identity
from .narrate import ENGLISH_VOICES, voice_info

SPEEDS = (0.8, 1.0, 1.2, 1.4, 1.6)
DEFAULT_VOICE = "af_heart"


def place_file(label: str) -> Path:
    from .paths import slugify, work_dir

    return work_dir(slugify(label or "untitled")) / "place.json"


def load_place(label: str) -> dict | None:
    try:
        data = json.loads(place_file(label).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("playhead"), (int, float)):
        return None
    data["id"] = label
    return data


def save_place(label: str, playhead: float, speed: float) -> dict:
    body = {
        "id": label,
        "playhead": max(0.0, float(playhead)),
        "speed": float(speed),
        "at": time.time(),
    }
    path = place_file(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    return body


def export_wav(
    audio: list[tuple[dict, bytes]],
    start: int,
    end: int,
    sample_rate: int = 24_000,
) -> tuple[bytes, int, int]:
    """Stitch spoken lines into a 16-bit WAV. Stop at the first line that is not ready."""
    import numpy as np

    ready = {
        meta["index"]: payload
        for meta, payload in audio
        if isinstance(meta.get("index"), int) and start <= meta["index"] < end
    }
    chunks: list[bytes] = []
    included = 0
    for index in range(start, end):
        payload = ready.get(index)
        if not payload:
            continue
        chunks.append(payload)
        included += 1
    requested = max(0, end - start)
    if not chunks:
        return b"", included, requested
    samples = np.concatenate([np.frombuffer(chunk, dtype="<f4") for chunk in chunks])
    np.clip(samples, -1.0, 1.0, out=samples)
    pcm = (samples * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buf.getvalue(), included, requested


def _log(level: str, message: str) -> None:
    """Booth events go to stderr. The page repeats them in the browser console."""
    print(f"[rhapsode] {level}: {message}", file=sys.stderr, flush=True)


def reading_session(
    wav: Path,
    doc: Path | None = None,
    mcp_endpoint: str | None = None,
    page_url: str | None = None,
    spoken: str | None = None,
) -> dict:
    """Describe the recording, where its text came from, and the lines to follow."""
    lines: list[dict] = []
    title = wav.stem
    url = page_url or ""
    site = ""
    if doc is not None:
        from .document import build_script, load

        loaded = load(doc)
        title = loaded.title or title
        url = page_url or loaded.url or ""
        site = loaded.site or ""
        lines = [
            {"text": utt.text, "kind": utt.kind, "start": None, "end": None}
            for utt in build_script(loaded)
            if utt.text
        ]
    if spoken:
        _mark_spoken(lines, spoken, _wav_seconds(wav))
    if not lines and spoken:
        lines = [{"text": spoken, "kind": "p", "start": 0.0, "end": _wav_seconds(wav)}]
    where = url or (str(doc) if doc else str(wav))
    identity, detail = page_identity(where, title)
    return {
        "title": title,
        "source": {
            "kind": "page" if where.startswith("http") else "file",
            "label": where,
            "identity": identity,
            "detail": detail,
            "site": site,
        },
        "mcp": blank_mcp(bool(mcp_endpoint)),
        "lines": lines,
    }


def _mark_spoken(lines: list[dict], spoken: str, duration: float | None) -> None:
    needle = spoken.strip()
    for line in lines:
        text = line["text"]
        if needle == text or needle in text or text in needle:
            line["start"] = 0.0
            line["end"] = duration
            return
    lines.insert(0, {"text": needle, "kind": "p", "start": 0.0, "end": duration})


def _wav_seconds(wav: Path) -> float | None:
    try:
        import soundfile as sf

        info = sf.info(str(wav))
    except Exception:
        return None
    if not info.samplerate:
        return None
    return info.frames / info.samplerate


def page_html() -> str:
    path = Path(__file__).with_name("operator.html")
    if path.is_file():
        return path.read_text(encoding="utf-8")
    buttons = "\n".join(
        '<button type="button" data-speed="{speed:.1f}"{pressed}>{speed:.1f}×</button>'.format(
            speed=speed,
            pressed=' aria-pressed="true"' if speed == SPEEDS[0] else "",
        )
        for speed in SPEEDS
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Rhapsode</title>
<style>
  body {{ margin: 0; font: 18px/1.4 system-ui, sans-serif; background: #1c1b19; color: #f4f1ea; }}
  main {{ max-width: 46rem; margin: 2rem auto; padding: 0 1.25rem 2rem; }}
  h1 {{ font-size: 1.4rem; font-weight: 600; margin: 0 0 0.25rem; }}
  h2 {{ font-size: 0.85rem; letter-spacing: 0.04em; text-transform: uppercase; color: #c8c2b4; margin: 0 0 0.35rem; }}
  p {{ margin: 0 0 0.4rem; }}
  .row {{ display: flex; flex-wrap: wrap; gap: 0.6rem; margin: 1rem 0; }}
  button {{ font: inherit; border: 0; border-radius: 8px; padding: 0.7rem 1rem; background: #3a362f; color: inherit; cursor: pointer; }}
  button[aria-pressed="true"] {{ background: #e7d7b1; color: #1c1b19; }}
  #play {{ min-width: 7rem; }}
  #source {{ background: #262420; border-radius: 10px; padding: 0.9rem 1rem; margin-bottom: 1.25rem; }}
  #source p {{ color: #f4f1ea; overflow-wrap: anywhere; }}
  #mcp-state {{ color: #c8c2b4; }}
  #script {{ list-style: none; margin: 0; padding: 0 0 3rem; border-top: 1px solid #3a362f; }}
  #script li {{ padding: 0.55rem 0.4rem; border-bottom: 1px solid #3a362f; color: #c8c2b4; }}
  #script li[aria-current="true"] {{ background: #3a362f; color: #f4f1ea; }}
  .kind {{ display: inline-block; min-width: 4.5rem; font-size: 0.75rem; letter-spacing: 0.03em; text-transform: uppercase; color: #a39886; }}
</style>
</head>
<body>
<main>
  <h1 id="title">Rhapsode</h1>
  <p id="state">Paused · 0.8×</p>
  <section id="source">
    <h2>Source</h2>
    <p id="src-where">Loading…</p>
    <h2 id="mcp-instance">Chrome</h2>
    <p id="mcp-profile"></p>
    <p id="mcp-traffic"></p>
  </section>
  <div class="row">
    <button type="button" id="play">Play</button>
  </div>
  <div class="row" id="speeds">
    {buttons}
  </div>
  <ol id="script"></ol>
  <audio id="audio" src="/audio" preload="auto"></audio>
</main>
<script>
const audio = document.querySelector("#audio");
const play = document.querySelector("#play");
const state = document.querySelector("#state");
const speedButtons = [...document.querySelectorAll("[data-speed]")];
const script = document.querySelector("#script");
let speed = 0.8;
let lines = [];

function label() {{
  state.textContent = (audio.paused ? "Paused" : "Playing") + " · " + speed.toFixed(1) + "×";
  play.textContent = audio.paused ? "Play" : "Pause";
}}

function setSpeed(next) {{
  speed = next;
  audio.playbackRate = speed;
  for (const button of speedButtons) {{
    button.setAttribute("aria-pressed", String(Number(button.dataset.speed) === speed));
  }}
  label();
}}

function follow() {{
  const t = audio.currentTime;
  let current = null;
  for (const item of script.children) {{
    const start = item.dataset.start;
    const end = item.dataset.end;
    const on = start !== "" && end !== "" && t >= Number(start) && t < Number(end);
    if (on) item.setAttribute("aria-current", "true");
    else item.removeAttribute("aria-current");
    if (on) current = item;
  }}
  if (current) current.scrollIntoView({{block: "nearest"}});
}}

function render(session) {{
  document.querySelector("#title").textContent = session.title || "Rhapsode";
  const source = session.source || {{}};
  document.querySelector("#src-where").textContent = source.label || "Unknown source";
  const mcp = session.mcp || {{}};
  document.querySelector("#mcp-instance").textContent = mcp.instance || "Chrome";
  const account = mcp.account || "";
  const profile = mcp.profile || "";
  document.querySelector("#mcp-profile").textContent = account && profile ? profile + " · " + account : (profile || account || "");
  const inn = Number(mcp.bytes_in) || 0;
  const out = Number(mcp.bytes_out) || 0;
  document.querySelector("#mcp-traffic").textContent = "In " + inn + " B · Out " + out + " B";
  lines = session.lines || [];
  script.replaceChildren();
  for (const line of lines) {{
    const item = document.createElement("li");
    const kind = document.createElement("span");
    kind.className = "kind";
    kind.textContent = line.kind || "text";
    item.append(kind, document.createTextNode(line.text || ""));
    item.dataset.start = line.start == null ? "" : String(line.start);
    item.dataset.end = line.end == null ? "" : String(line.end);
    script.append(item);
  }}
}}

play.addEventListener("click", () => {{
  if (audio.paused) audio.play();
  else audio.pause();
}});
audio.addEventListener("play", () => {{ label(); follow(); }});
audio.addEventListener("pause", label);
audio.addEventListener("timeupdate", follow);
for (const button of speedButtons) {{
  button.addEventListener("click", () => setSpeed(Number(button.dataset.speed)));
}}
document.addEventListener("keydown", (event) => {{
  if (event.code === "Space" && event.target === document.body) {{
    event.preventDefault();
    play.click();
  }}
}});
setSpeed(1);
label();
fetch("/session").then((response) => response.json()).then((session) => {{ render(session); follow(); }});
</script>
</body>
</html>
"""


def parse_byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Return an inclusive byte range, or None when the request is for the whole file."""
    if not header or not header.startswith("bytes=") or size <= 0:
        return None
    spec = header.removeprefix("bytes=").split(",", 1)[0].strip()
    start_s, _, end_s = spec.partition("-")
    if start_s == "":
        tail = int(end_s or "0")
        if tail <= 0:
            return None
        start = max(0, size - tail)
        end = size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    if start < 0 or start >= size:
        return None
    return start, min(end, size - 1)


_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _read_exact(sock, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("socket closed")
        data += chunk
    return data


def _ws_send(sock, payload: bytes, opcode: int) -> None:
    header = bytearray([0x80 | opcode])
    size = len(payload)
    if size < 126:
        header.append(size)
    elif size < 65536:
        header.append(126)
        header += size.to_bytes(2, "big")
    else:
        header.append(127)
        header += size.to_bytes(8, "big")
    sock.sendall(header + payload)


def _ws_recv(sock) -> tuple[int, bytes]:
    first, second = _read_exact(sock, 2)
    opcode = first & 0x0F
    length = second & 0x7F
    if length == 126:
        length = int.from_bytes(_read_exact(sock, 2), "big")
    elif length == 127:
        length = int.from_bytes(_read_exact(sock, 8), "big")
    mask = _read_exact(sock, 4) if second & 0x80 else None
    data = _read_exact(sock, length) if length else b""
    if mask:
        data = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
    return opcode, data


class LiveHub:
    """Shared live reading: scraped lines, Chrome status, and PCM for the booth."""

    def __init__(self, session: dict) -> None:
        self._lock = threading.Lock()
        self.session = session
        self.speed = SPEEDS[0]
        self.transport = "paused"
        source = session.get("source") or {}
        self.place_id = source.get("label") or session.get("title") or ""
        self.place = load_place(self.place_id)
        if self.place and isinstance(self.place.get("speed"), (int, float)):
            self.speed = float(self.place["speed"])
        self.voice = DEFAULT_VOICE
        self.cut_from: int | None = None
        self.voice_generation = 0
        self._respeak = threading.Event()
        self.clients: list = []
        self.audio: list[tuple[dict, bytes]] = []
        self.speech_tokens = 0
        self.wav: Path | None = None
        self.doc: Path | None = None
        self.doc_generation = 0

    def request_voice(self, voice: str, line: int) -> None:
        with self._lock:
            self.voice = voice
            self.cut_from = max(0, int(line))
            self.voice_generation += 1
            label = voice_info(voice)["label"]
            cut = self.cut_from
        self._respeak.set()
        self.event("info", f"voice {label} from line {cut}")

    def drop_from(self, index: int) -> None:
        with self._lock:
            self.audio = [(meta, payload) for meta, payload in self.audio if meta.get("index", -1) < index]
            for line in self.session["lines"][index:]:
                line["start"] = None
                line["end"] = None
                line["words"] = []
                line["status"] = "pending"
        self._send_json({"type": "cut", "from": index, "generation": self.doc_generation})

    def remember_place(self, playhead: float, speed: float) -> None:
        self.place = save_place(self.place_id, playhead, speed)
        if isinstance(speed, (int, float)):
            with self._lock:
                self.speed = float(speed)

    def control(self, msg: dict) -> dict:
        """Apply a booth command and tell every open operator page."""
        from .agent import agent_view, interpret_command

        fields = interpret_command(msg, self.speed)
        if "transport" in fields:
            with self._lock:
                self.transport = fields["transport"]
            self.event("info", f"transport {fields['transport']}")
        if "speed" in fields:
            with self._lock:
                self.speed = float(fields["speed"])
            self.event("info", f"speed {fields['speed']}")
        if "seek" in fields:
            self.remember_place(float(fields["seek"]), self.speed)
            self.event("info", f"seek {fields['seek']}")
        if "voice" in fields:
            self.request_voice(str(fields["voice"]), int(fields.get("line") or 0))
        self._send_json({"type": "control", **fields})
        return agent_view(self.snapshot())

    def event(self, level: str, message: str) -> None:
        _log(level, message)
        self._send_json({"type": "log", "level": level, "message": message})

    def set_mcp(self, mcp: dict) -> None:
        body = {key: value for key, value in mcp.items() if key != "type"}
        with self._lock:
            self.session["mcp"] = body
        payload = dict(body)
        payload["type"] = "mcp"
        self._send_json(payload)

    def add_speech_tokens(self, count: int) -> None:
        """Add phoneme tokens Kokoro has already spoken and tell the booth."""
        if count <= 0:
            return
        with self._lock:
            self.speech_tokens += count
            total = self.speech_tokens
        self._send_json({"type": "speech", "tokens": total})

    def snapshot(self) -> dict:
        with self._lock:
            body = dict(self.session)
            body["type"] = "state"
            body["speed"] = self.speed
            body["transport"] = self.transport
            body["voice"] = self.voice
            body["place"] = self.place
            body["speech_tokens"] = self.speech_tokens
            body["generation"] = self.doc_generation
            return body

    def set_status(self, index: int, status: str) -> None:
        with self._lock:
            if 0 <= index < len(self.session["lines"]):
                self.session["lines"][index]["status"] = status
        self._send_json({
            "type": "progress",
            "index": index,
            "status": status,
            "generation": self.doc_generation,
        })

    def publish_audio(self, index: int, samples, start: float, end: float, words: list[dict] | None = None) -> None:
        import numpy as np

        payload = np.ascontiguousarray(samples, dtype="<f4").tobytes()
        meta = {"type": "audio", "index": index, "start": start, "end": end, "words": words or []}
        with self._lock:
            meta["generation"] = self.doc_generation
            self.audio.append((meta, payload))
            if 0 <= index < len(self.session["lines"]):
                self.session["lines"][index]["start"] = start
                self.session["lines"][index]["end"] = end
                self.session["lines"][index]["words"] = words or []
        self._send_pair(meta, payload)

    def add_client(self, sock) -> None:
        with self._lock:
            self.clients.append(sock)
            state = dict(self.session)
            state["type"] = "state"
            state["speed"] = self.speed
            state["transport"] = self.transport
            state["voice"] = self.voice
            state["place"] = self.place
            state["speech_tokens"] = self.speech_tokens
            state["generation"] = self.doc_generation
            backlog = list(self.audio)
            try:
                _ws_send(sock, json.dumps(state).encode(), 1)
                for meta, payload in backlog:
                    _ws_send(sock, json.dumps(meta).encode(), 1)
                    _ws_send(sock, payload, 2)
            except OSError:
                self.clients = [client for client in self.clients if client is not sock]
                _log("warn", "client dropped during backlog")
                return
        self.event("info", f"client connected, backlog {len(backlog)} clips, speed {self.speed}")

    def drop_client(self, sock) -> None:
        with self._lock:
            self.clients = [client for client in self.clients if client is not sock]

    def replace_document(self, doc: Path, wav: Path | None = None) -> dict:
        """Swap the chapter being spoken and tell every open booth."""
        wav_path = wav or self.wav or doc
        fresh = reading_session(wav_path, doc)
        for line in fresh["lines"]:
            line["status"] = "pending"
        with self._lock:
            mcp = self.session.get("mcp")
            self.doc = doc
            self.wav = wav_path
            self.doc_generation += 1
            self.session["title"] = fresh["title"]
            self.session["source"] = fresh["source"]
            self.session["lines"] = fresh["lines"]
            if mcp:
                self.session["mcp"] = mcp
            self.audio.clear()
            self.cut_from = 0
            source = fresh.get("source") or {}
            self.place_id = source.get("label") or fresh.get("title") or ""
            self.place = load_place(self.place_id)
            if self.place and isinstance(self.place.get("speed"), (int, float)):
                self.speed = float(self.place["speed"])
            payload = dict(self.session)
            payload["type"] = "document"
            payload["speed"] = self.speed
            payload["transport"] = self.transport
            payload["voice"] = self.voice
            payload["place"] = self.place
            payload["speech_tokens"] = self.speech_tokens
            payload["generation"] = self.doc_generation
        self._respeak.set()
        self._send_json(payload)
        self.event("info", f"opened {payload['title']}")
        return payload

    def _send_json(self, payload: dict) -> None:
        self._send_locked(json.dumps(payload).encode(), None)

    def _send_pair(self, meta: dict, payload: bytes) -> None:
        self._send_locked(json.dumps(meta).encode(), payload)

    def _send_locked(self, raw: bytes, payload: bytes | None) -> None:
        with self._lock:
            dead: list = []
            for sock in self.clients:
                try:
                    _ws_send(sock, raw, 1)
                    if payload is not None:
                        _ws_send(sock, payload, 2)
                except OSError:
                    dead.append(sock)
            if dead:
                self.clients = [sock for sock in self.clients if sock not in dead]
                _log("warn", f"dropped {len(dead)} client(s) while sending")


def _narrate_into(hub: LiveHub, doc: Path) -> None:
    while True:
        try:
            _speak(hub, doc)
            return
        except Exception:
            hub.event("error", traceback.format_exc())
            time.sleep(0.5)


def _speak(hub: LiveHub, doc: Path) -> None:
    from .cache import origin_for
    from .document import build_script, load
    from .narrate import Narrator, SAMPLE_RATE
    from .speech import Lexicon

    narrator = Narrator(voice=hub.voice, device="cuda")
    lexicon = Lexicon()
    import numpy as np

    announced = -1
    while True:
        with hub._lock:
            active = hub.doc or doc
            doc_gen = hub.doc_generation
            voice = hub.voice
            start = 0 if hub.cut_from is None else hub.cut_from
            hub.cut_from = None
            generation = hub.voice_generation
            hub._respeak.clear()
        doc_obj = load(active)
        script = [utt for utt in build_script(doc_obj) if utt.text]
        if doc_gen != announced:
            hub.event("info", f"speaking {len(script)} lines")
            announced = doc_gen
        if narrator.voice != voice:
            try:
                narrator.set_voice(voice)
            except Exception:
                hub.event("error", f"could not load voice {voice}\n" + traceback.format_exc())
                with hub._lock:
                    hub.voice = narrator.voice
                    hub.cut_from = None
                hub._respeak.wait()
                continue
        if generation > 0:
            hub.drop_from(start)
        clock = 0.0
        if start:
            with hub._lock:
                ends = [meta.get("end") or 0 for meta, _ in hub.audio if meta.get("index", -1) < start]
            clock = max(ends) if ends else 0.0
        finished = True
        switched = False
        for index in range(start, len(script)):
            with hub._lock:
                if hub.doc_generation != doc_gen:
                    switched = True
                    break
                if hub.voice_generation != generation:
                    finished = False
                    break
            utt = script[index]
            hub.set_status(index, "rendering")
            try:
                pieces = list(narrator.iter_voiced(
                    lexicon.apply(utt.text),
                    origin=origin_for(doc_obj, utt, index),
                ))
            except Exception:
                hub.event("error", f"line {index} failed\n" + traceback.format_exc())
                hub.set_status(index, "done")
                continue
            spoken_tokens = 0
            parts_with_words: list[tuple[np.ndarray, list[dict]]] = []
            for piece in pieces:
                spoken_tokens += int(piece[2]) if len(piece) > 2 else 0
                parts_with_words.append((piece[0], piece[1] if len(piece) > 1 else []))
            if spoken_tokens:
                hub.add_speech_tokens(spoken_tokens)
            with hub._lock:
                if hub.doc_generation != doc_gen:
                    switched = True
                    break
                if hub.voice_generation != generation:
                    finished = False
                    break
            if not parts_with_words:
                hub.event("warn", f"line {index} produced no audio")
                hub.set_status(index, "done")
                continue
            chunks: list[np.ndarray] = []
            words: list[dict] = []
            origin = clock
            for cleaned, part_words in parts_with_words:
                for word in part_words:
                    words.append({
                        "text": word["text"],
                        "start": word["start"] + clock,
                        "end": word["end"] + clock,
                    })
                chunks.append(cleaned)
                clock += len(cleaned) / SAMPLE_RATE
            hub.publish_audio(index, np.concatenate(chunks), origin, clock, words)
            hub.set_status(index, "queued")
        if switched:
            continue
        if finished:
            hub.event("info", "speaking finished")
            hub._respeak.wait()


def _watch_chrome(session: dict, endpoint: str | None, hub: LiveHub | None) -> ChromeLink | None:
    """Start the live Chrome/MCP probe when the booth was given a DevTools URL."""
    if not endpoint:
        return None
    label = (session.get("source") or {}).get("label") or ""
    page = label if str(label).startswith("http") else ""

    def publish(mcp: dict) -> None:
        if hub is not None:
            hub.set_mcp(mcp)
        else:
            session["mcp"] = mcp

    link = ChromeLink(endpoint, page, publish, session.get("title") or "")
    link.start()
    return link


def list_chrome_tabs() -> list[dict]:
    """Open tabs in the running Chrome, as ``{id, url}`` dicts.

    The booth page itself is left out. ``chrome-devtools-mcp`` has to be able
    to see that Chrome.
    """
    import asyncio

    from .mcp import McpClient

    async def _run() -> list[dict]:
        async with McpClient() as client:
            pages = await client.list_pages()
        tabs = []
        for page in pages:
            url = str(page.get("url") or "").strip()
            lowered = url.lower()
            if not lowered.startswith("http"):
                continue
            if "127.0.0.1:8765" in lowered or "localhost:8765" in lowered:
                continue
            tab = {"id": page.get("id"), "url": url}
            title = str(page.get("title") or "").strip()
            if title:
                tab["title"] = title
            tabs.append(tab)
        return tabs

    return asyncio.run(_run())


def read_chrome_tab(url: str) -> Path:
    """Read one open tab the way ``rhapsode read`` does, and return its file."""
    import asyncio

    from .extractor import extract_page

    return asyncio.run(extract_page(url))


def serve_live(
    wav: Path,
    doc: Path,
    mcp_endpoint: str | None,
    page_url: str | None,
    host: str = "0.0.0.0",
    port: int = 8765,
) -> None:
    """Serve a live booth and speak `doc` into every connected operator."""
    session = reading_session(wav, doc, mcp_endpoint, page_url)
    for line in session["lines"]:
        line["status"] = "pending"
    hub = LiveHub(session)
    hub.doc = doc
    hub.wav = wav.resolve()
    link = _watch_chrome(session, mcp_endpoint, hub)
    handler = functools.partial(_Handler, wav=wav.resolve(), session=session, hub=hub)
    server = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=_narrate_into, args=(hub, doc), daemon=True).start()
    print(f"operator desk  http://127.0.0.1:{port}/  ({doc.name})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if link is not None:
            link.stop()
        server.server_close()


def serve(
    wav: Path,
    host: str = "0.0.0.0",
    port: int = 8765,
    doc: Path | None = None,
    mcp_endpoint: str | None = None,
    page_url: str | None = None,
    spoken: str | None = None,
) -> None:
    """Serve the operator page until interrupted."""
    wav = wav.resolve()
    session = reading_session(wav, doc, mcp_endpoint, page_url, spoken)
    link = _watch_chrome(session, mcp_endpoint, None)
    handler = functools.partial(_Handler, wav=wav, session=session)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"operator desk  http://127.0.0.1:{port}/  ({wav.name})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if link is not None:
            link.stop()
        server.server_close()


class _Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, wav: Path, session: dict, hub: LiveHub | None = None, **kwargs) -> None:
        self._wav = wav
        self._session = session
        self._hub = hub
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/ws" and self._hub is not None:
            self._websocket()
            return
        if path in ("/", "/index.html"):
            body = page_html().encode()
            self._send(200, "text/html; charset=utf-8", body)
            return
        if path == "/icon.svg":
            icon = Path(__file__).with_name("icon.svg")
            if not icon.is_file():
                self.send_error(404)
                return
            self._send(200, "image/svg+xml", icon.read_bytes())
            return
        if path == "/session":
            body = json.dumps(self._hub.snapshot() if self._hub else self._session).encode()
            self._send(200, "application/json", body)
            return
        if path == "/voices":
            body = json.dumps({"voices": [voice_info(voice) for voice in ENGLISH_VOICES]}).encode()
            self._send(200, "application/json", body)
            return
        if path == "/tabs":
            if self._hub is None:
                self._send(409, "application/json", b'{"error":"the booth is not live"}')
                return
            try:
                tabs = list_chrome_tabs()
            except Exception as exc:
                self._send(502, "application/json", json.dumps({"error": str(exc)[:300]}).encode())
                return
            self._send(200, "application/json", json.dumps({"tabs": tabs}).encode())
            return
        if path == "/export.wav":
            self._send_export()
            return
        if path == "/audio":
            self._send_audio()
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in ("/control", "/tab"):
            self.send_error(404)
            return
        if self._hub is None:
            self._send(409, "application/json", b'{"error":"the booth is not live"}')
            return
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > 8192:
            self._send(400, "application/json", b'{"error":"command body is missing or too large"}')
            return
        try:
            msg = json.loads(self.rfile.read(length).decode())
        except (json.JSONDecodeError, ValueError) as exc:
            self._send(400, "application/json", json.dumps({"error": str(exc)}).encode())
            return
        if not isinstance(msg, dict):
            self._send(400, "application/json", b'{"error":"command body must be an object"}')
            return
        if path == "/tab":
            self._open_tab(msg)
            return
        try:
            body = json.dumps(self._hub.control(msg)).encode()
        except ValueError as exc:
            self._send(400, "application/json", json.dumps({"error": str(exc)}).encode())
            return
        self._send(200, "application/json", body)

    def _open_tab(self, msg: dict) -> None:
        url = str(msg.get("url") or "").strip()
        if not url.lower().startswith("http"):
            self._send(400, "application/json", json.dumps({"error": "Pick a tab by its http address."}).encode())
            return
        if self._hub is None:
            self._send(409, "application/json", b'{"error":"the booth is not live"}')
            return
        try:
            doc = read_chrome_tab(url)
        except Exception as exc:
            self._hub.event("error", f"could not read tab {url}\n" + traceback.format_exc())
            self._send(502, "application/json", json.dumps({"error": str(exc)[:300]}).encode())
            return
        payload = self._hub.replace_document(doc, self._wav)
        body = {"ok": True, "title": payload.get("title") or "", "url": url}
        self._send(200, "application/json", json.dumps(body).encode())

    def _send_export(self) -> None:
        if self._hub is None:
            self._send(409, "application/json", b'{"error":"live audio is not ready"}')
            return
        query = parse_qs(urlparse(self.path).query)
        try:
            start = int(query.get("from", ["0"])[0])
            end = int(query.get("to", ["0"])[0])
        except ValueError:
            self._send(400, "application/json", b'{"error":"from and to must be line numbers"}')
            return
        name = query.get("name", ["rhapsode.wav"])[0]
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in name).strip("-") or "rhapsode.wav"
        if not safe.endswith(".wav"):
            safe += ".wav"
        with self._hub._lock:
            audio = list(self._hub.audio)
        wav, included, requested = export_wav(audio, start, end)
        if included == 0:
            _log("warn", f"export {safe} has no audio for lines {start}-{end}")
            self._send(409, "application/json", b'{"error":"that range has no audio yet"}')
            return
        _log("info", f"export {safe} lines {included}/{requested}")
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav)))
        self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
        self.send_header("X-Rhapsode-Lines", f"{included}/{requested}")
        if included < requested:
            self.send_header("X-Rhapsode-Partial", "1")
        self.end_headers()
        self.wfile.write(wav)

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_audio(self) -> None:
        data = self._wav.read_bytes()
        size = len(data)
        span = parse_byte_range(self.headers.get("Range"), size)
        if span is None and self.headers.get("Range"):
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        if span is None:
            start, end = 0, size - 1
            status = 200
        else:
            start, end = span
            status = 206
        chunk = data[start : end + 1]
        self.send_response(status)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(chunk)))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        self.wfile.write(chunk)

    def _websocket(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        sock = self.connection
        assert self._hub is not None
        try:
            self._hub.add_client(sock)
            while True:
                opcode, data = _ws_recv(sock)
                if opcode == 8:
                    break
                if opcode == 9:
                    _ws_send(sock, data, 10)
                    continue
                if opcode != 1:
                    continue
                msg = json.loads(data.decode())
                if msg.get("type") == "place":
                    playhead = float(msg.get("playhead") or 0)
                    speed = float(msg.get("speed") or self._hub.speed)
                    self._hub.remember_place(playhead, speed)
                elif msg.get("type") in {"speed", "transport", "voice", "seek"}:
                    try:
                        self._hub.control(msg)
                    except ValueError as exc:
                        self._hub.event("warn", str(exc))
        except (ConnectionError, OSError):
            pass
        except (json.JSONDecodeError, ValueError) as exc:
            self._hub.event("warn", f"bad client message: {exc}")
        finally:
            self._hub.drop_client(sock)
            self._hub.event("info", "client disconnected")

    def log_message(self, fmt: str, *args) -> None:
        return
