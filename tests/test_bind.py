"""Tests for bind: FFmpeg metadata and encoding."""

from __future__ import annotations

from unittest.mock import patch
import subprocess as sp

from rhapsode import Chapter, ffmetadata, encode
from rhapsode.bind import _esc
import numpy as np


# ── ffmetadata ───────────────────────────────────────────────────


def test_ffmetadata_no_chapters():
    meta = ffmetadata("Title", "Artist", "Comment", [])
    assert "title=Title" in meta
    assert "artist=Artist" in meta
    assert "comment=Comment" in meta
    assert "[CHAPTER]" not in meta


def test_ffmetadata_two_chapters():
    chapters = [Chapter(0, 5, "Intro"), Chapter(5, 10, "Outro")]
    meta = ffmetadata("Doc", "Art", "C", chapters)
    assert meta.count("[CHAPTER]") == 2
    assert "title=Intro" in meta
    assert "title=Outro" in meta


def test_ffmetadata_three_chapters():
    chapters = [Chapter(0, 1, "A"), Chapter(1, 2, "B"), Chapter(2, 5, "C")]
    meta = ffmetadata("Title", "Art", "C", chapters)
    assert "artist=Art" in meta
    assert meta.count("[CHAPTER]") == 3


def test_ffmetadata_empty_title():
    meta = ffmetadata("", "A", "C", [])
    assert "title=" in meta


# ── _esc ───────────────────────────────────────────────────────
# _esc escapes =;#\\ and newline with a preceding backslash via regex


def test_esc_normal():
    assert _esc("hello") == "hello"


def test_esc_newline_escaped():
    """Newline character itself remains; a backslash is prepended."""
    out = _esc("a\nb")
    assert "\n" in out  # newline survives
    assert out == "a\\\nb"


def test_esc_equals():
    out = _esc("a=b")
    assert "\\=" in out
    assert out == "a\\=b"


def test_esc_semicolon():
    out = _esc("a;b")
    assert "\\;" in out


def test_esc_hash():
    out = _esc("a#b")
    assert "\\#" in out


def test_esc_backslash():
    out = _esc("a\\b")
    assert "\\\\" in out


# ── encode (mocked ffmpeg) ────────────────────────────────────


def test_encode_mp3(tmp_path):
    wav = tmp_path / "test.wav"
    out = tmp_path / "test.mp3"
    import soundfile as sf
    audio = np.zeros(48000, dtype=np.float32)
    sf.write(str(wav), audio, 24000)

    with patch("rhapsode.bind.subprocess.run") as fake_run:
        encode(wav, out, ffmetadata("Title", "Art", "C", []))
    fake_run.assert_called_once()
    args = fake_run.call_args[0][0]
    assert args[0] == "ffmpeg"
    assert "-i" in args


def test_encode_m4b(tmp_path):
    import soundfile as sf
    wav = tmp_path / "test.wav"
    out = tmp_path / "test.m4b"
    audio = np.zeros(48000, dtype=np.float32)
    sf.write(str(wav), audio, 24000)

    with patch("rhapsode.bind.subprocess.run") as fake_run:
        encode(wav, out, ffmetadata("Title", "Art", "C", [Chapter(0, 1, "S1")]))
    args = fake_run.call_args[0][0]
    assert "aac" in args  # m4b uses aac codec
    assert "-movflags" in args


def test_encode_failed(tmp_path):
    import soundfile as sf
    import numpy as np
    wav = tmp_path / "test.wav"
    out = tmp_path / "test.mp3"
    audio = np.zeros(48000, dtype=np.float32)
    sf.write(str(wav), audio, 24000)

    meta = ffmetadata("Title", "Art", "C", [])
    with patch("rhapsode.bind.subprocess.run") as fake_run:
        fake_run.side_effect = sp.CalledProcessError(1, "ffmpeg")
        import pytest
        with pytest.raises(sp.CalledProcessError):
            encode(wav, out, meta)


def test_encode_no_ffmpeg(tmp_path):
    import soundfile as sf
    wav = tmp_path / "test.wav"
    out = tmp_path / "test.mp3"
    audio = np.zeros(48000, dtype=np.float32)
    sf.write(str(wav), audio, 24000)
    import pytest
    with patch("rhapsode.bind.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="ffmpeg is required"):
            encode(wav, out, ffmetadata("T", "A", "C", []))


def test_encode_unsupported_ext(tmp_path):
    import soundfile as sf
    wav = tmp_path / "test.wav"
    out = tmp_path / "test.xyz"
    audio = np.zeros(48000, dtype=np.float32)
    sf.write(str(wav), audio, 24000)
    import pytest
    with pytest.raises(ValueError, match="unsupported"):
        encode(wav, out, ffmetadata("T", "A", "C", []))


def test_encode_with_chapters(tmp_path):
    """When chapters exist, meta file is written."""
    import soundfile as sf
    import numpy as np
    wav = tmp_path / "test.wav"
    out = tmp_path / "test.mp3"
    audio = np.zeros(48000, dtype=np.float32)
    sf.write(str(wav), audio, 24000)

    with patch("rhapsode.bind.subprocess.run"):
        encode(wav, out, ffmetadata("T", "A", "C", [Chapter(0, 1, "S1")]))
    meta_file = wav.with_suffix(".ffmeta")
    assert meta_file.exists()
    assert "[CHAPTER]" in meta_file.read_text()


# ── Chapter dataclass ──────────────────────────────────────────


def test_chapter_equality():
    c1 = Chapter(0, 5, "Intro")
    c2 = Chapter(0, 5, "Intro")
    c3 = Chapter(0, 3, "Intro")
    assert c1 == c2
    assert c1 != c3


def test_chapter_repr():
    c = Chapter(1.5, 3.0, "Ch1")
    assert "Ch1" in repr(c)


def test_chapters_boundary():
    """Consecutive chapters don't overlap."""
    ch = [Chapter(0, 5, "A"), Chapter(5, 10, "B")]
    assert ch[0].end == ch[1].start
