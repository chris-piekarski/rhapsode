"""Tests for narrate: Narrator init, render, narrate."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from rhapsode.narrate import Narrator, Timing, SAMPLE_RATE, KOKORO_REPO, LANGS
from rhapsode import build_script, load
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
        n = Narrator()
    assert n.device == "cpu"
    assert n.voice == "af_heart"
    assert n.speed == 1.0


def test_cuda_autodetect():
    with patch.dict("sys.modules", {"torch": _torch(True), "kokoro": _kokoro()}):
        n = Narrator()
    assert n.device == "cuda"


def test_device_override():
    with patch.dict("sys.modules", {"torch": _torch(True), "kokoro": _kokoro()}):
        n = Narrator(device="cpu")
    assert n.device == "cpu"


def test_bad_voice():
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": _kokoro()}):
        with pytest.raises(ValueError, match="unknown Kokoro voice"):
            Narrator(voice="xyz")  # 'x' not in LANGS


def test_voice_b():
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": _kokoro()}):
        n = Narrator(voice="bf_emma")
    assert n.voice == "bf_emma"


# ── render ─────────────────────────────────────────────────────


def test_render_empty():
    mk = _kokoro()
    mk.KPipeline.return_value.return_value = iter([])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator()
    audio = n.render("hi")
    assert audio.shape == (0,)


def test_render_one_part():
    mk = _kokoro()
    mp = mk.KPipeline.return_value
    part = MagicMock()
    part.audio.numpy.return_value = np.array([0.5, 0.5])
    mp.return_value = iter([part])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator()
    audio = n.render("hi")
    assert len(audio) == 2


def test_render_none_skipped():
    mk = _kokoro()
    mp = mk.KPipeline.return_value
    p1 = MagicMock()
    p1.audio = None
    p2 = MagicMock()
    p2.audio.numpy.return_value = np.array([1.0])
    mp.return_value = iter([p1, p2])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator()
    audio = n.render("hi")
    assert len(audio) == 1


# ── narrate ────────────────────────────────────────────────────


def test_narrate_produces_wav(tmp_path):
    mk = _kokoro()
    mp = mk.KPipeline.return_value
    chunk = np.ones(2400, dtype=np.float32)
    part = MagicMock()
    part.audio.numpy.return_value = chunk
    mp.return_value = iter([part])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator()
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
    part.audio.numpy.return_value = np.ones(10, dtype=np.float32)
    mp.return_value = iter([part])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator()
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
    part.audio.numpy.return_value = chunk
    mp.return_value = iter([part])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator()
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
    part.audio.numpy.return_value = np.ones(10)
    mp.return_value = iter([part])
    with patch.dict("sys.modules", {"torch": _torch(), "kokoro": mk}):
        n = Narrator()
        doc = load("x", text="A.\n\nB.\n\nC.\n")
        script = build_script(doc)
        n.narrate(script, Path("/tmp/_p.wav"),
                  lexicon=Lexicon(), progress=True)
    out = capsys.readouterr()
    assert "narrating" in out.err


# ── constants ──────────────────────────────────────────────────


def test_sample_rate():
    assert SAMPLE_RATE == 24_000


def test_repo():
    assert "Kokoro" in KOKORO_REPO


def test_langs():
    assert "a" in LANGS
    assert "b" in LANGS
