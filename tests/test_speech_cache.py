"""Tests for src/rhapsode/cache.py (stdlib + numpy only)."""

from __future__ import annotations

import sqlite3

import numpy as np

from rhapsode.cache import (
    SpeechCache,
    SpeechOrigin,
    _reset_default,
    budget_from_env,
    cache_key,
    default_speech_cache,
    load_or_render,
    origin_for,
)
from rhapsode.document import Section, Utterance


# ── helpers ─────────────────────────────────────────────────────

def _pieces(n_parts: int = 2) -> list[tuple[np.ndarray, list[dict], int]]:
    pieces: list[tuple[np.ndarray, list[dict], int]] = []
    for i in range(n_parts):
        audio = np.array([0.1] * (100 + i * 50), dtype=np.float32)
        words = [
            {"text": f"word{i}a", "start": 0.0, "end": 0.1},
            {"text": f"word{i}b", "start": 0.1, "end": 0.2},
        ]
        tokens = 10 + i * 3
        pieces.append((audio, words, tokens))
    return pieces


def _origin(page_url="https://example.com", section_heading="Intro",
            section_index=0, utterance_index=0, kind="p", tab_url=""):
    return SpeechOrigin(
        page_url=page_url,
        page_title="Example",
        section_index=section_index,
        section_heading=section_heading,
        utterance_index=utterance_index,
        kind=kind,
        tab_url=tab_url or page_url,
    )


VOICE = "af_heart"
SPEED = 1.0
TEXT = "Hello world"
SAMPLE_RATE = 24000
REPO = "hexgrad/Kokoro-82M"
TEXT2 = "Second utterance"


# ── test 1: put then get returns same samples/words/tokens ─────


def test_put_then_get(tmp_path):
    c = SpeechCache(tmp_path, budget_bytes=10 * 1024 * 1024)
    pieces = _pieces(2)
    c.put(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO, pieces)
    hit = c.get(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO)
    assert hit is not None
    assert len(hit) == 2
    for orig, got in zip(pieces, hit):
        np.testing.assert_array_equal(orig[0], got[0])
        assert got[1] == orig[1]
        assert got[2] == orig[2]


# ── test 2: restart cache on same dir still hits ──────────────


def test_restart_cache_hit(tmp_path):
    c1 = SpeechCache(tmp_path, budget_bytes=10 * 1024 * 1024)
    c1.put(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO, _pieces())
    c2 = SpeechCache(tmp_path, budget_bytes=10 * 1024 * 1024)
    hit = c2.get(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO)
    assert hit is not None
    assert len(hit) > 0


# ── test 3: different voice/speed miss, same key + different origins = 1 blob ──


def test_key_uniqueness(tmp_path):
    c = SpeechCache(tmp_path, budget_bytes=10 * 1024 * 1024)
    c.put(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO, _pieces(1), _origin(section_heading="A"))
    # miss on different voice
    assert c.get("bf_alice", SPEED, TEXT, SAMPLE_RATE, REPO) is None
    # miss on different speed
    assert c.get(VOICE, 2.0, TEXT, SAMPLE_RATE, REPO) is None
    c.put(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO, _pieces(1), _origin(section_heading="B"))
    # same key still 1 blob
    stats = c.stats()
    assert stats["blobs"] == 1


# ── test 4: replay(page_url) returns both sections ordered ─────


def test_replay_ordered(tmp_path):
    c = SpeechCache(tmp_path, budget_bytes=10 * 1024 * 1024)
    pieces_a = _pieces(1)
    origin_a = _origin(section_index=0, section_heading="First")
    pieces_b = _pieces(2)
    origin_b = _origin(section_index=1, section_heading="Second", utterance_index=1)
    c.put(VOICE, SPEED, TEXT + " a", SAMPLE_RATE, REPO, pieces_a, origin_a)
    c.put(VOICE, SPEED, TEXT + " b", SAMPLE_RATE, REPO, pieces_b, origin_b)
    results = c.replay(origin_a.page_url)
    assert len(results) == 2
    assert results[0]["section_index"] == 0
    assert results[1]["section_index"] == 1


# ── test 5: LRU eviction (A touched, B untouched, C added → B evicted) ───


def test_lru_eviction(tmp_path):
    c = SpeechCache(tmp_path, budget_bytes=40 * 1024)
    audio_a = np.full(100 * 50, 0.1, dtype=np.float32)
    audio_b = np.full(100 * 50, 0.2, dtype=np.float32)
    audio_c = np.full(100 * 50, 0.3, dtype=np.float32)

    import time as _time

    # B inserted first (oldest)
    c.put("v2", 1.0, "text_b", SAMPLE_RATE, REPO,
          [(audio_b, [], 1)], _origin(page_url="B"))
    _time.sleep(0.02)

    # Then A (touched more recently than B)
    c.put("v1", 1.0, "text_a", SAMPLE_RATE, REPO,
          [(audio_a, [], 1)], _origin(page_url="A"))
    _time.sleep(0.02)

    # Then C (newest)
    c.put("v3", 1.0, "text_c", SAMPLE_RATE, REPO,
          [(audio_c, [], 1)], _origin(page_url="C"))

    # add a new large blob that pushes over budget
    audio_d = np.full(100 * 50, 0.4, dtype=np.float32)
    c.put("v4", 1.0, "text_d", SAMPLE_RATE, REPO,
          [(audio_d, [], 1)], _origin(page_url="D"))

    # B should be evicted as least recently used (oldest used)
    assert c.get("v2", 1.0, "text_b", SAMPLE_RATE, REPO) is None


# ── test 6: missing npy file triggers cleanup + None ───────────


def test_missing_npy_cleanup(tmp_path):
    c = SpeechCache(tmp_path, budget_bytes=10 * 1024 * 1024)
    c.put(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO, _pieces())
    bp = c._blob_path(cache_key(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO))
    assert bp.exists()
    bp.unlink()
    assert c.get(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO) is None
    # DB row also removed
    assert c.stats()["blobs"] == 0


# ── test 7: corrupt JSON triggers cleanup + None ───────────────


def test_corrupt_json_cleanup(tmp_path):
    c = SpeechCache(tmp_path, budget_bytes=10 * 1024 * 1024)
    c.put(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO, _pieces())
    key = cache_key(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO)
    with c._lock:
        conn = sqlite3.connect(str(c._db))
        conn.execute("UPDATE blobs SET words_json = 'corrupt' WHERE key = ?", (key,))
        conn.commit()
        conn.close()
    assert c.get(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO) is None
    assert c.stats()["blobs"] == 0


# ── test 8: single payload > budget skips storage ─────────────


def test_over_budget_skip(tmp_path):
    c = SpeechCache(tmp_path, budget_bytes=64)
    pieces = _pieces(1)
    c.put(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO, pieces)
    assert c.get(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO) is None


# ── test 9: load_or_render without/with/miss ───────────────────


def test_load_or_render(tmp_path):
    # reset default singleton to avoid hitting real disk
    _reset_default()

    text = "cache load_or_render"
    c = SpeechCache(tmp_path, budget_bytes=10 * 1024 * 1024)

    # bypass (cache=False)

    def render_fn():
        yield np.array([0.1] * 50, dtype=np.float32), [{"text": "hello", "start": 0.0, "end": 0.1}], 5

    result = load_or_render(False, VOICE, SPEED, text, SAMPLE_RATE, REPO, render_fn, _origin())
    assert len(result) == 1

    # miss → render then store
    result2 = load_or_render(c, VOICE, SPEED, text, SAMPLE_RATE, REPO, render_fn, _origin())
    assert len(result2) == 1

    # hit → uses cache
    result3 = load_or_render(c, VOICE, SPEED, text, SAMPLE_RATE, REPO, render_fn, _origin())
    assert len(result3) == 1


# ── extra: default_speech_cache uses tmp path via env ─────────


def test_default_speech_cache_env(tmp_path, monkeypatch):
    _reset_default()
    monkeypatch.setenv("RHAPSODE_SPEECH_CACHE", str(tmp_path))
    monkeypatch.setenv("RHAPSODE_SPEECH_CACHE_MB", "1")
    dc = default_speech_cache()
    assert isinstance(dc, SpeechCache)
    assert dc._root == tmp_path.resolve()
    _reset_default()


# ── extra: origin_for construction ─────────────────────────────


def test_origin_for():
    from rhapsode.document import Document
    doc = Document(title="Page Title", sections=[Section(heading="First", level=1)], url="https://example.com/page")
    utt = Utterance(text="Hello", kind="p", pause=0.5, section=0)
    origin = origin_for(doc, utt, 0, "https://example.com/tab")
    assert origin.page_url == "https://example.com/page"
    assert origin.page_title == "Page Title"
    assert origin.tab_url == "https://example.com/tab"
    assert origin.section_heading == "First"


# ── extra: budget_from_env ─────────────────────────────────────


def test_budget_from_env(monkeypatch):
    monkeypatch.setenv("RHAPSODE_SPEECH_CACHE_MB", "50")
    assert budget_from_env() == 50 * 1_048_576


# ── extra: stats tracking ─────────────────────────────────────


def test_stats(tmp_path):
    c = SpeechCache(tmp_path, budget_bytes=10 * 1024 * 1024)
    c.put(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO, _pieces())
    s = c.stats()
    assert s["blobs"] == 1
    assert s["bytes"] > 0
    assert s["budget_bytes"] == 10 * 1024 * 1024



# ── extra: cache_key rounds speed to 3 decimals ──────────────


def test_cache_key_speed_rounding():
    k1 = cache_key(VOICE, 0.8, TEXT, SAMPLE_RATE, REPO)
    k2 = cache_key(VOICE, 0.8000004, TEXT, SAMPLE_RATE, REPO)
    assert k1 == k2, "speed 0.8 and 0.8000004 should share the same key"
    k3 = cache_key(VOICE, 1.0, TEXT, SAMPLE_RATE, REPO)
    assert k1 != k3, "speed 0.8 and 1.0 should have different keys"


# ── extra: origins migration adds tab_url to unique index ────


def test_origins_migration(tmp_path):
    """Old databases without tab_url in the UNIQUE constraint are migrated."""
    from rhapsode.cache import _BLOB_DDL, _ORIGIN_NEW_UNIQUE
    db = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(_BLOB_DDL)
    conn.execute(
        "CREATE TABLE origins (blob_key TEXT, page_url TEXT, page_title TEXT,"
        " section_index INTEGER, section_heading TEXT, utterance_index INTEGER,"
        " kind TEXT, tab_url TEXT, seen REAL,"
        " UNIQUE(blob_key, page_url, section_index, utterance_index))"
    )
    conn.commit()
    conn.close()
    c = SpeechCache(tmp_path, budget_bytes=10 * 1024 * 1024)
    with c._lock:
        conn = sqlite3.connect(str(c._db))
        row = conn.execute(
            "SELECT sql FROM sqlite_master"
            " WHERE type='table' AND name='origins'"
        ).fetchone()
        conn.close()
    assert row is not None
    assert _ORIGIN_NEW_UNIQUE in row[0], f"new unique not present: {row[0]}"


# ── extra: Narrator cache default is None ────────────────────


def test_narrator_cache_default():
    """Narrator should default cache=None (enabled via default_speech_cache)."""
    import inspect
    from rhapsode.narrate import Narrator
    sig = inspect.signature(Narrator.__init__)
    assert sig.parameters["cache"].default is None


# ── extra: empty text / empty pieces ──────────────────────────


def test_empty_put(tmp_path):
    c = SpeechCache(tmp_path, budget_bytes=10 * 1024 * 1024)
    c.put(VOICE, SPEED, "", SAMPLE_RATE, REPO, _pieces())
    assert c.stats()["blobs"] == 0
    c.put(VOICE, SPEED, TEXT, SAMPLE_RATE, REPO, [])
    assert c.stats()["blobs"] == 0
