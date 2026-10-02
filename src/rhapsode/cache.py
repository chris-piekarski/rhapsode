"""Disk cache for Kokoro TTS synthesis results.

Standard library plus numpy only. No Kokoro import so tests stay fast.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from .document import Utterance
from .paths import speech_cache_dir

# ── origin tracking ───────────────────────────────────────────────────────

@dataclass
class SpeechOrigin:
    """Where a cached blob was used."""
    page_url: str = ""
    page_title: str = ""
    section_index: int = 0
    section_heading: str = ""
    utterance_index: int = 0
    kind: str = "p"
    tab_url: str = ""




def origin_for(doc, utt: Utterance, index: int, tab_url: str = "") -> SpeechOrigin:
    """Provenance record for one utterance in a document."""
    page_url = doc.url or ""
    page_title = doc.title or ""
    si = utt.section
    sh = doc.sections[si].heading if 0 <= si < len(doc.sections) else ""
    return SpeechOrigin(
        page_url=page_url,
        page_title=page_title,
        section_index=si,
        section_heading=sh,
        utterance_index=index,
        kind=utt.kind,
        tab_url=tab_url or page_url,
    )


# ── key helpers ───────────────────────────────────────────────────────────

_SENTINEL_KEY = "_n_samples"


def cache_key(voice: str, speed: float, text: str, sample_rate: int, repo: str) -> str:
    """Deterministic blob key for synthesis parameters plus text.

    Speed is rounded to 3 decimal places so ``0.8`` and ``0.8000004`` collide.
    """
    import hashlib
    payload = json.dumps(
        {"v": 1, "repo": repo, "sample_rate": sample_rate,
         "voice": voice, "speed": round(float(speed), 3), "text": text},
        separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


# ── config ────────────────────────────────────────────────────────────────

def budget_from_env() -> int:
    return int(os.environ.get("RHAPSODE_SPEECH_CACHE_MB", "200")) * 1_048_576


# ── default singleton ────────────────────────────────────────────────────

_default: SpeechCache | None = None


def default_speech_cache() -> SpeechCache:
    global _default
    if _default is None:
        _default = SpeechCache(speech_cache_dir(), budget_from_env())
    return _default


def _reset_default() -> None:
    """Tests: replace the process-wide default with None."""
    global _default
    _default = None


# ── resolve helper (used by load_or_render) ──────────────────────────────

def _resolve_cache(cache) -> SpeechCache | None:
    """Turn the Narrator.cache param into an effective cache or None.

    * ``False``  – disable caching
    * ``None``   – use the process-wide default (lazy init)
    * ``SpeechCache`` – use that instance directly
    """
    if cache is False:
        return None
    if cache is None or cache is True:
        return default_speech_cache()
    return cache  # SpeechCache instance


# ── SpeechCache ───────────────────────────────────────────────────────────

_BLOB_DDL = """
CREATE TABLE IF NOT EXISTS blobs (
    key TEXT PRIMARY KEY,
    voice TEXT    NOT NULL,
    speed REAL   NOT NULL,
    text  TEXT    NOT NULL,
    sample_rate INTEGER NOT NULL,
    repo  TEXT    NOT NULL,
    tokens INTEGER NOT NULL,
    words_json TEXT  NOT NULL,
    nbytes INTEGER NOT NULL,
    created REAL NOT NULL,
    used   REAL NOT NULL
)
"""

_ORIGIN_DDL = """
CREATE TABLE IF NOT EXISTS origins (
    blob_key       TEXT    NOT NULL REFERENCES blobs(key),
    page_url       TEXT    NOT NULL DEFAULT "",
    page_title     TEXT    NOT NULL DEFAULT "",
    section_index  INTEGER NOT NULL DEFAULT 0,
    section_heading TEXT  NOT NULL DEFAULT "",
    utterance_index INTEGER NOT NULL DEFAULT 0,
    kind           TEXT    NOT NULL DEFAULT "p",
    tab_url        TEXT    NOT NULL DEFAULT "",
    seen           REAL    NOT NULL,
    UNIQUE(blob_key, page_url, section_index, utterance_index, tab_url)
)
"""

# Latest unique key for the origins table (used to detect schema migration)
_ORIGIN_NEW_UNIQUE = "blob_key, page_url, section_index, utterance_index, tab_url"


class SpeechCache:
    """LRU disk cache backed by SQLite + .npy blobs.

    Thread-safe via a single internal lock.
    """

    def __init__(self, root: Path, budget_bytes: int) -> None:
        self._root = root.resolve()
        self._budget = budget_bytes
        self._db = root / "index.sqlite"
        self._lock = threading.Lock()
        root.mkdir(parents=True, exist_ok=True)
        (root / "blobs").mkdir(exist_ok=True)
        self._init_db()

    # -- private db helpers (require self._lock) ------------------------

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(str(self._db), timeout=5)
            try:
                conn.execute("PRAGMA foreign_keys=OFF")
                conn.execute(_BLOB_DDL)
                conn.execute(_ORIGIN_DDL)
                self._migrate_origins(conn)
                conn.commit()
            finally:
                conn.close()

    def _migrate_origins(self, conn: sqlite3.Connection) -> None:
        """Add tab_url to the origins unique index if the old schema is detected."""
        existing = conn.execute(
            "SELECT sql FROM sqlite_master"
            " WHERE type='table' AND name='origins'"
        ).fetchone()
        if existing is None:
            return
        sql = existing[0] or ""
        if _ORIGIN_NEW_UNIQUE not in sql:
            conn.execute("ALTER TABLE origins RENAME TO origins_old")
            conn.execute(_ORIGIN_DDL)
            conn.execute(
                "INSERT INTO origins SELECT * FROM origins_old"
            )
            conn.execute("DROP TABLE origins_old")


    def _blob_path(self, key: str) -> Path:
        return self._root / "blobs" / key[:2] / f"{key}.npy"

    def _remove_key(self, key: str) -> None:
        """Delete a blob row, its origins, and the file.  Calls with lock."""
        conn = sqlite3.connect(str(self._db), timeout=5)
        try:
            conn.execute("DELETE FROM origins WHERE blob_key = ?", (key,))
            conn.execute("DELETE FROM blobs WHERE key = ?", (key,))
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()
        bp = self._blob_path(key)
        bp.unlink(missing_ok=True)

    # -- public API ----------------------------------------------------

    def get(self, voice: str, speed: float, text: str, sample_rate: int, repo: str):
        """Return cached pieces or None."""
        if not text:
            return None
        key = cache_key(voice, speed, text, sample_rate, repo)
        with self._lock:
            conn = sqlite3.connect(str(self._db), timeout=5)
            try:
                row = conn.execute(
                    "SELECT words_json, tokens FROM blobs WHERE key = ?", (key,)
                ).fetchone()
                if row is None:
                    return None
                words_json_str, total_tokens = row
                now = time.time()
                conn.execute("UPDATE blobs SET used = ? WHERE key = ?", (now, key))
                conn.commit()
            finally:
                conn.close()

        # load file outside lock
        bp = self._blob_path(key)
        if not bp.exists():
            self._remove_key(key)
            return None
        try:
            audio = np.load(str(bp))
        except Exception:
            self._remove_key(key)
            return None
        try:
            words_json = json.loads(words_json_str)
        except (json.JSONDecodeError, TypeError):
            self._remove_key(key)
            return None

        # reconstruct pieces
        pieces: list[tuple[np.ndarray, list[dict], int]] = []
        offset = 0
        for entry in words_json:
            word_list: list[dict] = []
            n_samples = 0
            token_count = 0
            for item in entry:
                if _SENTINEL_KEY in item:
                    n_samples = item[_SENTINEL_KEY]
                    token_count = item.get("_tokens", 0)
                else:
                    word_list.append(dict(item))
            if offset + n_samples > len(audio):
                n_samples = max(0, len(audio) - offset)
            piece_audio = np.array(
                audio[offset: offset + n_samples], dtype=np.float32, copy=True
            )
            offset += n_samples
            pieces.append((piece_audio, word_list, token_count))
        return pieces

    def put(
        self,
        voice: str,
        speed: float,
        text: str,
        sample_rate: int,
        repo: str,
        pieces: list[tuple[np.ndarray, list[dict], int]],
        origin: SpeechOrigin | None = None,
    ) -> None:
        """Store a rendered synthesis and its origin."""
        if not text or not pieces:
            return
        key = cache_key(voice, speed, text, sample_rate, repo)

        # budget check on raw float32 bytes
        raw_bytes = sum(len(p[0]) for p in pieces) * 4
        if raw_bytes > self._budget:
            return

        all_audio = np.concatenate([p[0] for p in pieces])

        # write npy atomically (outside lock)
        bp = self._blob_path(key)
        bp.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(bp).replace(".npy", ".tmp"))
        np.save(str(tmp), all_audio)
        os.replace(str(tmp) + ".npy", str(bp))

        nbytes = bp.stat().st_size
        if nbytes > self._budget:
            bp.unlink(missing_ok=True)
            return

        total_tokens = sum(p[2] for p in pieces)
        words_json = []
        for piece in pieces:
            pw = [dict(w) for w in piece[1]]  # shallow copy
            pw.append({_SENTINEL_KEY: len(piece[0]), "_tokens": piece[2]})
            words_json.append(pw)

        now = time.time()
        words_json_str = json.dumps(words_json)
        victim_files: list[Path] = []

        with self._lock:
            conn = sqlite3.connect(str(self._db), timeout=5)
            try:
                cur_total = conn.execute(
                    "SELECT COALESCE(SUM(nbytes), 0) FROM blobs WHERE key != ?",
                    (key,),
                ).fetchone()[0]

                while cur_total + nbytes > self._budget:
                    row = conn.execute(
                        "SELECT key, nbytes FROM blobs WHERE key != ? "
                        "ORDER BY used ASC LIMIT 1",
                        (key,),
                    ).fetchone()
                    if row is None:
                        break
                    victim_key, victim_nbytes = row
                    cur_total -= victim_nbytes
                    conn.execute(
                        "DELETE FROM origins WHERE blob_key = ?", (victim_key,)
                    )
                    conn.execute("DELETE FROM blobs WHERE key = ?", (victim_key,))
                    victim_files.append(self._blob_path(victim_key))

                conn.execute(
                    "INSERT OR REPLACE INTO blobs "
                    "(key, voice, speed, text, sample_rate, repo, tokens, "
                    "words_json, nbytes, created, used) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (key, voice, speed, text, sample_rate, repo,
                     total_tokens, words_json_str, nbytes, now, now),
                )
                if origin is not None:
                    conn.execute(
                        "INSERT OR IGNORE INTO origins "
                        "(blob_key, page_url, page_title, section_index, "
                        "section_heading, utterance_index, kind, tab_url, seen) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (key, origin.page_url, origin.page_title,
                         origin.section_index, origin.section_heading,
                         origin.utterance_index, origin.kind,
                         origin.tab_url, now),
                    )
                conn.commit()
            finally:
                conn.close()

        for vf in victim_files:
            vf.unlink(missing_ok=True)

    def remember(
        self,
        voice: str,
        speed: float,
        text: str,
        sample_rate: int,
        repo: str,
        origin: SpeechOrigin | None = None,
    ) -> None:
        """Bump ``used`` timestamp and optionally add an origin row."""
        key = cache_key(voice, speed, text, sample_rate, repo)
        with self._lock:
            conn = sqlite3.connect(str(self._db), timeout=5)
            try:
                now = time.time()
                conn.execute("UPDATE blobs SET used = ? WHERE key = ?", (now, key))
                if origin is not None:
                    conn.execute(
                        "INSERT OR IGNORE INTO origins "
                        "(blob_key, page_url, page_title, section_index, "
                        "section_heading, utterance_index, kind, tab_url, seen) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (key, origin.page_url, origin.page_title,
                         origin.section_index, origin.section_heading,
                         origin.utterance_index, origin.kind,
                         origin.tab_url, now),
                    )
                conn.commit()
            finally:
                conn.close()

    def replay(self, page_url: str, voice: str | None = None) -> list[dict]:
        """Join origins to blobs that still have a file.

        Order by ``section_index, utterance_index, seen``. Returns dicts
        keyed on ``section_index, section_heading, utterance_index, kind,
        tab_url, page_title, voice, speed, words, audio``.
        """
        query = """
            SELECT o.section_index, o.section_heading, o.utterance_index,
                   o.kind, o.tab_url, o.page_title,
                   b.voice, b.speed, b.words_json, b.key
            FROM origins o
            JOIN blobs b ON o.blob_key = b.key
            WHERE o.page_url = ?
        """
        params: list = [page_url]
        if voice is not None:
            query += " AND b.voice = ?"
            params.append(voice)
        query += " ORDER BY o.section_index, o.utterance_index, o.seen"

        with self._lock:
            conn = sqlite3.connect(str(self._db), timeout=5)
            try:
                rows = conn.execute(query, params).fetchall()
            finally:
                conn.close()

        result: list[dict] = []
        for row in rows:
            (section_index, section_heading, utterance_index,
             kind, tab_url, page_title, blob_voice, blob_speed,
             words_json_str, blob_key) = row
            bp = self._blob_path(blob_key)
            if not bp.exists():
                continue
            try:
                audio = np.load(str(bp))
            except Exception:
                continue
            try:
                words_json: list = json.loads(words_json_str)
            except (json.JSONDecodeError, TypeError):
                continue
            # strip sentinels for clean words
            clean_words: list[dict] = []
            for entry in words_json:
                for item in entry:
                    if _SENTINEL_KEY not in item:
                        clean_words.append(dict(item))
            result.append({
                "section_index": section_index,
                "section_heading": section_heading,
                "utterance_index": utterance_index,
                "kind": kind,
                "tab_url": tab_url,
                "page_title": page_title,
                "voice": blob_voice,
                "speed": blob_speed,
                "words": clean_words,
                "audio": np.array(audio, dtype=np.float32, copy=True),
            })
        return result

    def stats(self) -> dict:
        with self._lock:
            conn = sqlite3.connect(str(self._db), timeout=5)
            try:
                row = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(nbytes), 0) FROM blobs"
                ).fetchone()
            finally:
                conn.close()
        return {"blobs": row[0], "bytes": row[1], "budget_bytes": self._budget}


# ── testable boundary ────────────────────────────────────────────────────

def load_or_render(
    cache,
    voice: str,
    speed: float,
    text: str,
    sample_rate: int,
    repo: str,
    render: Callable[[], Iterator[tuple[np.ndarray, list[dict], int]]],
    origin: SpeechOrigin | None = None,
) -> list[tuple[np.ndarray, list[dict], int]]:
    """Return cached pieces or call ``render``, storing the result.

    ``cache`` follows the same semantics as :attr:`Narrator.cache`:
    ``False`` disables, ``None`` uses the default, instance uses directly.
    """
    effective = _resolve_cache(cache)
    if effective is None:
        return list(render())

    cached = effective.get(voice, speed, text, sample_rate, repo)
    if cached is not None:
        effective.remember(voice, speed, text, sample_rate, repo, origin)
        return cached

    pieces = list(render())
    if pieces:
        effective.put(voice, speed, text, sample_rate, repo, pieces, origin)
    return pieces
