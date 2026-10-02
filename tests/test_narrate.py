"""Tests for narrate: Narrator init, render, narrate."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from rhapsode.narrate import Narrator, SAMPLE_RATE, KOKORO_REPO, LANGS, tighten_silence, tighten_silence_map, _map_sample, ENGLISH_VOICES, voice_label
from rhapsode.chrome_link import (
    install_version,
    instance_name,
    match_profile,
    page_identity,
    preferred_profile,
    profile_for_page,
    parse_browser_endpoint,
)
from rhapsode.desk import SPEEDS, page_html, parse_byte_range, reading_session
from rhapsode import Utterance, build_script, load
from rhapsode.speech import Lexicon


def _torch(cuda: bool = False):
    m = MagicMock()
    m.cuda.is_available.return_value = cuda
    return m


def _kokoro():
    return MagicMock()


# ── init ─────────────────────────────────────────────────────


def test_default():
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": _kokoro()}):
        n = Narrator(cache=False)
    assert n.device == "cpu"
    assert n.voice == "af_heart"
    assert n.speed == 1.0


def test_cuda_autodetect():
    with patch.dict("sys.modules", {"torch": _torch(True), "kokoro": _kokoro()}):
        n = Narrator(cache=False)
    assert n.device == "cuda"


def test_device_override():
    with patch.dict("sys.modules", {"torch": _torch(True), "kokoro": _kokoro()}):
        n = Narrator(cache=False, device="cpu")
    assert n.device == "cpu"


def test_bad_voice():
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": _kokoro()}):
        with pytest.raises(ValueError, match="unknown Kokoro voice"):
            Narrator(voice="xyz", cache=False)  # 'x' not in LANGS


def test_voice_b():
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": _kokoro()}):
        n = Narrator(voice="bf_emma", cache=False)
    assert n.voice == "bf_emma"


# ── render ─────────────────────────────────────────────────────


def test_render_empty():
    mk = _kokoro()
    mk.KPipeline.return_value.side_effect = lambda *a, **k: iter([])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator(cache=False)
    audio = n.render("hi")
    assert audio.shape == (0,)


def test_render_one_part():
    mk = _kokoro()
    mp = mk.KPipeline.return_value
    part = MagicMock()
    part.audio.detach.return_value.cpu.return_value.numpy.return_value = np.array([0.5, 0.5])
    mp.side_effect = lambda *a, **k: iter([part])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator(cache=False)
        audio = n.render("hi")
    assert len(audio) == 2


def test_render_none_skipped():
    mk = _kokoro()
    mp = mk.KPipeline.return_value
    p1 = MagicMock()
    p1.audio = None
    p2 = MagicMock()
    p2.audio.detach.return_value.cpu.return_value.numpy.return_value = np.array([1.0])
    mp.side_effect = lambda *a, **k: iter([p1, p2])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator(cache=False)
        audio = n.render("hi")
    assert len(audio) == 1


# ── narrate ────────────────────────────────────────────────────


def test_narrate_produces_wav(tmp_path):
    mk = _kokoro()
    mp = mk.KPipeline.return_value
    chunk = np.ones(2400, dtype=np.float32)
    part = MagicMock()
    part.audio.detach.return_value.cpu.return_value.numpy.return_value = chunk
    mp.side_effect = lambda *a, **k: iter([part])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator(cache=False)
        doc = load("x", text="First.\n")
        script = build_script(doc)
        wav = tmp_path / "out.wav"
        timings = n.narrate(script, wav, lexicon=Lexicon(), progress=False)
    assert wav.exists()
    assert len(timings) == len(script)


def test_timings_increasing(tmp_path):
    mk = _kokoro()
    mp = mk.KPipeline.return_value
    part = MagicMock()
    part.audio.detach.return_value.cpu.return_value.numpy.return_value = np.ones(10, dtype=np.float32)
    mp.side_effect = lambda *a, **k: iter([part])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator(cache=False)
        doc = load("x", text="A.\n\nB.\n")
        script = build_script(doc)
        timings = n.narrate(script, tmp_path / "t.wav",
                            lexicon=Lexicon(), progress=False)
    for i in range(1, len(timings)):
        assert timings[i].start > timings[i - 1].start


def test_timing_values():
    mk = _kokoro()
    mp = mk.KPipeline.return_value
    N = 2400
    chunk = np.ones(N, dtype=np.float32)
    part = MagicMock()
    part.audio.detach.return_value.cpu.return_value.numpy.return_value = chunk
    mp.side_effect = lambda *a, **k: iter([part])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator(cache=False)
        doc = load("x", text="Hi.\n")
        script = build_script(doc)
        timings = n.narrate(script, Path("/tmp/_t.wav"),
                            lexicon=Lexicon(), progress=False)
        assert timings[0].start == 0
        assert timings[0].end >= N / SAMPLE_RATE


def test_narrate_progress_stderr(capsys):
    mk = _kokoro()
    mp = mk.KPipeline.return_value
    part = MagicMock()
    part.audio.detach.return_value.cpu.return_value.numpy.return_value = np.ones(10)
    mp.side_effect = lambda *a, **k: iter([part])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator(cache=False)
        doc = load("x", text="A.\n\nB.\n\nC.\n")
        script = build_script(doc)
        n.narrate(script, Path("/tmp/_p.wav"),
                  lexicon=Lexicon(), progress=True)
    out = capsys.readouterr()
    assert "narrating" in out.err


# ── constants ──────────────────────────────────────────────────


class _Speaker:
    def __init__(self) -> None:
        self.chunks: list[np.ndarray] = []

    def write(self, audio: np.ndarray) -> None:
        self.chunks.append(np.array(audio, copy=True))

    def close(self) -> None:
        pass


def test_narrate_wav_and_speaker(tmp_path):
    """Each PCM piece is written to disk and handed to the speaker."""
    mk = _kokoro()
    first = np.ones(4, dtype=np.float32)
    second = np.full(6, 0.25, dtype=np.float32)
    p1, p2 = MagicMock(), MagicMock()
    p1.audio.detach.return_value.cpu.return_value.numpy.return_value = first
    p2.audio.detach.return_value.cpu.return_value.numpy.return_value = second
    mp = mk.KPipeline.return_value
    mp.side_effect = lambda *a, **k: iter([p1, p2])
    speaker = _Speaker()
    script = [Utterance("hi", "p", 0, 0.5)]
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator(cache=False)
        wav = tmp_path / "both.wav"
        n.narrate(script, wav, lexicon=Lexicon(), progress=False, play=True, speaker=speaker)
        assert len(speaker.chunks) == 3
        np.testing.assert_array_equal(speaker.chunks[0], first)
        np.testing.assert_array_equal(speaker.chunks[1], second)
        assert speaker.chunks[2].shape == (int(0.5 * SAMPLE_RATE),)
    assert wav.exists()


def test_narrate_speaker_only():
    mk = _kokoro()
    part = MagicMock()
    part.audio.detach.return_value.cpu.return_value.numpy.return_value = np.ones(8, dtype=np.float32)
    mp = mk.KPipeline.return_value
    mp.side_effect = lambda *a, **k: iter([part])
    speaker = _Speaker()
    script = [Utterance("hi", "p", 0, 0.0)]
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator(cache=False)
        n.narrate(script, None, lexicon=Lexicon(), progress=False, play=True, speaker=speaker)
        assert len(speaker.chunks) == 1
        assert speaker.chunks[0].shape == (8,)


def test_narrate_requires_a_sink():
    mk = _kokoro()
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator(cache=False)
        with pytest.raises(ValueError, match="wav path"):
            n.narrate([], None, lexicon=Lexicon(), progress=False, play=False)


def test_reading_session_names_page_and_mcp(tmp_path: Path):
    doc = tmp_path / "page.json"
    doc.write_text(
        '{"title":"Chapter","url":"https://learning.oreilly.com/ch02.html",'
        '"site":"O\'Reilly Online Learning",'
        '"sections":[{"heading":"Chapter","level":1,"blocks":[{"kind":"p","text":"Most practitioners begin here."}]}]}',
        encoding="utf-8",
    )
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"")
    session = reading_session(
        wav,
        doc,
        "ws://127.0.0.1:9222/devtools/browser/abc",
        spoken="Most practitioners begin here.",
    )
    assert session["source"]["kind"] == "page"
    assert session["source"]["label"].startswith("https://learning.oreilly.com/")
    assert session["source"]["identity"].startswith("learning.oreilly.com/")
    assert session["source"]["detail"] == "Chapter"
    assert session["source"]["site"] == "O'Reilly Online Learning"
    assert session["mcp"]["connected"] is False
    assert "ws://" not in str(session["mcp"])
    assert session["mcp"]["instance"] == "Chrome"
    spoken_line = next(line for line in session["lines"] if "Most practitioners" in line["text"])
    assert spoken_line["start"] == 0.0
    html = page_html()
    assert 'id="script"' in html
    assert 'id="mcp-instance"' in html and 'id="mcp-traffic"' in html
    assert '<a id="src-where"' in html and 'id="src-title"' in html
    assert 'id="mcp-endpoint"' not in html
    assert 'id="seek-back"' in html and 'id="seek-fwd"' in html
    assert "[rhapsode]" in html and "console.error" in html
    assert "rhapsode.place" in html
    assert 'className = "export"' in html
    assert 'id="export-wav"' not in html
    assert 'id="track"' in html and 'id="gantt"' in html and 'id="section-name"' in html
    assert 'id="mute"' in html and 'id="level"' in html and "node.connect(master)" in html
    assert "tab-title" in html and "tab-url" in html
    assert "function paintSource" in html
    assert '"The text came from a file.": true' in html
    assert 'name="viewport"' in html
    assert 'id="about"' in html and "rhapsōidos" in html
    assert "w-active" in html


def test_page_identity_leads_with_the_url():
    identity, detail = page_identity(
        "https://learning.oreilly.com/library/view/designing-agent-systems/ch02.html",
        "Chapter 2. Designing Agent Systems",
    )
    assert identity == "learning.oreilly.com/library/view/designing-agent-systems/ch02.html"
    assert detail == "Chapter 2. Designing Agent Systems"
    assert page_identity("", "Chapter 2. Designing Agent Systems") == (
        "Chapter 2. Designing Agent Systems",
        "",
    )
    hashed = page_identity(
        "https://learning.oreilly.com/library/view/building-applications-with/9781098176495/ch02.html"
        "#chapter_2_orchestration_1754586819850890",
        "Chapter 2. Designing Agent Systems",
    )
    assert "#" not in hashed[0]
    assert hashed[0].endswith("/ch02.html")


def test_local_chrome_identity_without_devtools(tmp_path: Path):
    assert install_version(["PlatformExperienceHelper", "154.0.8037.58", "120.0.1.0"]) == "154.0.8037.58"
    profiles = [
        {"directory": "Default", "label": "example.com", "email": "ada@example.com"},
        {"directory": "Profile 1", "label": "example.org", "email": "grace@example.org"},
    ]
    front = preferred_profile(profiles, ["Profile 1", "Default"])
    assert front is not None and front["label"] == "example.org"
    for directory, visited in (("Default", 50), ("Profile 1", 10)):
        db = tmp_path / directory
        db.mkdir()
        import sqlite3

        con = sqlite3.connect(db / "History")
        con.execute("create table urls (url text, last_visit_time integer)")
        con.execute(
            "insert into urls values (?, ?)",
            ("https://learning.oreilly.com/library/view/book/ch02.html#chapter", visited),
        )
        con.commit()
        con.close()
    opened = profile_for_page(
        tmp_path,
        profiles,
        "https://learning.oreilly.com/library/view/book/ch02.html#chapter",
    )
    assert opened is not None and opened["label"] == "example.com"


def test_chrome_link_names_instance_and_profile():
    assert parse_browser_endpoint("ws://127.0.0.1:9222/devtools/browser/abc") == ("127.0.0.1", 9222, "abc")
    assert instance_name("Chrome/154.0.8037.58", "Chrome/154") == "Chrome 154.0.8037.58"
    assert instance_name("Chrome/154.0.8037.58", "HeadlessChrome/154").startswith("Headless")
    profiles = [
        {"directory": "Default", "label": "example.com", "email": "ada@example.com"},
        {"directory": "Profile 1", "label": "example.org", "email": "grace@example.org"},
    ]
    opened = match_profile(profiles, ["grace@example.org"], used_default=False)
    assert opened is not None and opened["label"] == "example.org"
    fallback = match_profile(profiles, [], used_default=True)
    assert fallback is not None and fallback["label"] == "example.com"


def test_place_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("rhapsode.desk.place_file", lambda _label: tmp_path / "place.json")
    from rhapsode.desk import load_place, save_place

    saved = save_place("https://learning.oreilly.com/ch02", 42.5, 0.8)
    loaded = load_place("https://learning.oreilly.com/ch02")
    assert saved["playhead"] == 42.5
    assert loaded["playhead"] == 42.5
    assert loaded["speed"] == 0.8
    assert loaded["id"] == "https://learning.oreilly.com/ch02"


def test_export_wav_keeps_audio_after_a_gap_and_the_full_length():
    import io
    import wave

    import numpy as np

    from rhapsode.desk import export_wav

    first = np.full(24000 * 3, 0.25, dtype=np.float32).tobytes()
    later = np.full(24000 * 5, 0.5, dtype=np.float32).tobytes()
    wav, included, requested = export_wav(
        [({"index": 0}, first), ({"index": 2}, later)],
        0,
        3,
    )
    assert (included, requested) == (2, 3)
    with wave.open(io.BytesIO(wav)) as handle:
        assert handle.getframerate() == 24000
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getnframes() == 24000 * 8
        assert handle.getnframes() / handle.getframerate() == 8


def test_operator_speeds_and_range():
    assert SPEEDS == (0.8, 1.0, 1.2, 1.4, 1.6)
    html = page_html()
    for speed in SPEEDS:
        assert f'data-speed="{speed:.1f}"' in html
    assert 'aria-pressed="true"' in html
    assert parse_byte_range(None, 100) is None
    assert parse_byte_range("bytes=0-9", 100) == (0, 9)
    assert parse_byte_range("bytes=90-", 100) == (90, 99)


def test_word_time_survives_kept_audio_and_snaps_across_a_hole():
    """Word in kept audio retains positive duration; word in deleted silence region snaps.

    Layout: 1s tone (voiced) + 300ms silence + 1s tone (voiced).
    Internal silence > 120 ms is trimmed: 30 ms edges kept, middle removed.
    """
    sr = SAMPLE_RATE
    tone = np.full(sr, 0.2, dtype=np.float32)
    hole = np.zeros(int(0.3 * sr), dtype=np.float32)
    audio = np.concatenate([tone, hole, tone])
    _cleaned, spans = tighten_silence_map(audio)
    # Position 0.5s lives well inside the first tone -> maps to position > 0
    kept = _map_sample(spans, int(0.5 * sr), audio.size)
    assert 0 < kept < _cleaned.size
    # Position ~1.15s is in the DELETED middle of the silence
    # (silence spans 1.0-1.3s; edges at 1.0-1.03s and 1.27-1.3s are kept;
    #  middle 1.03-1.27s is deleted). Position 1.15s = 27600 samples = in middle.
    snapped = _map_sample(spans, int(1.15 * sr), audio.size)
    # Must snap to start of the third span (right edge of silence = start of second tone)
    assert snapped == spans[2][2], "deleted-middle snaps to dst start of right-edge span"
    # Verify the snapped value is not in the deleted duration range
    # i.e. it doesn't carry the ~1.15s position offset
    assert snapped >= spans[2][2]  # exactly the join point


def test_tighten_silence_collapses_holes():
    sr = SAMPLE_RATE
    tone = np.full(int(0.2 * sr), 0.2, dtype=np.float32)
    lead = np.zeros(int(0.5 * sr), dtype=np.float32)
    hole = np.zeros(int(0.3 * sr), dtype=np.float32)
    tail = np.zeros(int(0.5 * sr), dtype=np.float32)
    cleaned = tighten_silence(np.concatenate([lead, tone, hole, tone, tail]))
    # 30 ms lead + 200 ms tone + 60 ms hole + 200 ms tone + 80 ms tail
    expect = int((0.030 + 0.2 + 0.060 + 0.2 + 0.080) * sr)
    assert abs(cleaned.size - expect) <= 2


def test_sample_rate():
    assert SAMPLE_RATE == 24_000


def test_repo():
    assert "Kokoro" in KOKORO_REPO


def test_langs():
    assert "a" in LANGS
    assert "b" in LANGS


# ── tighten_silence_map ──────────────────────────────────────────


def test_map_no_silence():
    """Audio with no silence returns full span."""
    tone = np.full(4800, 0.3, dtype=np.float32)
    cleaned, spans = tighten_silence_map(tone)
    assert cleaned.shape == tone.shape
    assert np.array_equal(cleaned, tone)
    assert len(spans) == 1
    assert spans[0] == (0, 4800, 0)


def test_map_internal_hole():
    """Internal silence > 120 ms is cropped and spans reflect kept edges."""
    sr = SAMPLE_RATE
    tone = np.full(int(0.2 * sr), 0.2, dtype=np.float32)
    hole = np.zeros(int(0.3 * sr), dtype=np.float32)
    combined = np.concatenate([tone, hole, tone])
    cleaned, spans = tighten_silence_map(combined)
    # There should be 3 spans: tone, hole-edges, tone
    assert len(spans) >= 2
    # Destination positions must be contiguous
    for i in range(1, len(spans)):
        assert spans[i][2] == spans[i - 1][2] + (spans[i - 1][1] - spans[i - 1][0])
    # Total destination extent equals cleaned length
    last = spans[-1]
    assert last[2] + (last[1] - last[0]) == len(cleaned)


def test_map_preserves_total_length():
    """Sum of span lengths equals cleaned size."""
    sr = SAMPLE_RATE
    lead = np.zeros(int(0.5 * sr))
    tone = np.full(int(0.2 * sr), 0.4)
    mid = np.zeros(int(0.1 * sr))
    tone2 = np.full(int(0.15 * sr), 0.5)
    tail = np.zeros(int(0.4 * sr))
    buf = np.concatenate([lead, tone, mid, tone2, tail]).astype(np.float32)
    cleaned, spans = tighten_silence_map(buf)
    total_dst = sum(s[1] - s[0] for s in spans)
    assert total_dst == len(cleaned)


def test_map_sample_basic():
    """_map_sample maps positions through spans correctly."""
    sr = SAMPLE_RATE
    # 100 ms voiced + 300 ms silent + 100 ms voiced
    tone = np.full(int(0.1 * sr), 0.3)
    hole = np.zeros(int(0.3 * sr))
    combined = np.concatenate([tone, hole, tone]).astype(np.float32)
    cleaned, spans = tighten_silence_map(combined)
    n = len(combined)

    # Start of first tone maps to 0
    assert _map_sample(spans, 0, n) == 0
    # Position in deleted hole snaps to start of next kept region
    hole_mid = int(0.1 * sr) + int(0.15 * sr)
    mapped = _map_sample(spans, hole_mid, n)
    # Should be at or before end of cleaned
    assert mapped < len(cleaned)


def test_map_sample_word_survives_hole():
    """Words that span a deleted gap retain positive duration after mapping."""
    sr = SAMPLE_RATE
    # Voiced A, silence, voiced B
    a = np.full(int(0.05 * sr), 0.3)
    hole = np.zeros(int(0.25 * sr))
    b = np.full(int(0.05 * sr), 0.3)
    buf = np.concatenate([a, hole, b]).astype(np.float32)
    _, spans = tighten_silence_map(buf)
    n = len(buf)

    w_start = int(0.03 * sr)   # well inside first voiced
    w_end = int(0.05 * sr) + int(0.15 * sr)  # in the hole
    dst_s = _map_sample(spans, w_start, n)
    dst_e = _map_sample(spans, w_end, n)
    assert dst_e > dst_s, "mapped word must retain positive duration"


def test_map_sample_word_in_hole_snaps():
    """Words entirely within deleted silence snap to a join without
    retaining deleted duration."""
    sr = SAMPLE_RATE
    tone = np.full(int(0.2 * sr), 0.2)
    big_hole = np.zeros(int(0.5 * sr))
    buf = np.concatenate([tone, big_hole, tone]).astype(np.float32)
    _, spans = tighten_silence_map(buf)
    n = len(buf)

    # Word deep inside the deleted silence
    hole_start = int(0.2 * sr)
    word_s = hole_start + int(0.1 * sr)
    word_e = hole_start + int(0.2 * sr)
    dst_s = _map_sample(spans, word_s, n)
    dst_e = _map_sample(spans, word_e, n)
    assert dst_s == dst_e or (dst_e - dst_s) < int(0.06 * sr), (
        "Word inside deleted silence should have near-zero mapped duration"
    )


# ── iter_voiced smoke test ───────────────────────────────────────


def test_iter_voiced_yields_tuples():
    """iter_voiced yields (audio, words) pairs."""
    mk = _kokoro()
    mp = mk.KPipeline.return_value
    r = MagicMock()
    r.audio.detach.return_value.cpu.return_value.numpy.return_value = np.full(2400, 0.2, dtype=np.float32)
    fake_tok = MagicMock()
    fake_tok.text = "hello"
    fake_tok.start_ts = 0.02  # seconds
    fake_tok.end_ts = 0.25
    r.tokens = [fake_tok]
    r.phonemes = "həlˈoʊ"
    mp.side_effect = lambda *a, **k: iter([r])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator(cache=False)
        out = list(n.iter_voiced("hello"))
    assert len(out) == 1
    audio, words, tokens = out[0]
    assert audio.dtype == np.float32
    assert len(words) >= 1
    assert words[0]["text"] == "hello"
    assert words[0]["end"] > words[0]["start"]  # positive duration after mapping
    assert tokens == len("həlˈoʊ")


# ── voice infrastructure ────────────────────────────────────────


def test_english_voices_has_heart():
    """ENGLISH_VOICES includes the default voice "af_heart"."""
    assert "af_heart" in ENGLISH_VOICES
    assert len(ENGLISH_VOICES) >= 20


def test_voice_label_formats():
    """voice_label names the speaker, the accent, and woman or man."""
    assert voice_label("af_heart") == "Heart · American woman"
    assert voice_label("bm_george") == "George · British man"
    assert voice_label("am_eric") == "Eric · American man"
    assert "af_eric" not in ENGLISH_VOICES
    assert voice_label("not-a-voice").startswith("Unknown")


def test_set_voice_lives():
    """set_voice swaps the voice attribute on the Narrator."""
    mk = _kokoro()
    mp = mk.KPipeline.return_value
    mp.side_effect = lambda *a, **k: iter([])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator(voice="af_heart", cache=False)
        assert n.voice == "af_heart"
        n.set_voice("bf_emma")
        assert n.voice == "bf_emma"


def test_set_voice_cross_language_rebuilds_pipe():
    """set_voice with a cross-language voice rebuilds KPipeline."""
    mk = _kokoro()
    mp = mk.KPipeline.return_value
    mp.side_effect = lambda *a, **k: iter([])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator(voice="af_heart", cache=False)
        original_lang = n._lang_code
        # Spanish voice triggers cross-language rebuild
        n.set_voice("ef_sofia")  # starts with 'e' = Spanish in LANGS
        assert n._lang_code != original_lang
        assert n.voice == "ef_sofia"


def test_english_voices_all_valid():
    """Every voice_id in ENGLISH_VOICES is a valid English voice."""
    for v in ENGLISH_VOICES:
        assert len(v) >= 3
        assert v[0] in ("a", "b"), f"{v} should be English prefix"
        assert "_" in v, f"{v} should contain underscore separator"


def test_voice_label_returns_str():
    """voice_label is non-empty for all known English voices."""
    for v in ENGLISH_VOICES:
        label = voice_label(v)
        assert isinstance(label, str)
        assert len(label) > 2
