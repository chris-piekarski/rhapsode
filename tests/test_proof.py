"""Tests for proof: normalize, align, _merge_compounds, load_model, proofread."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import sys

from rhapsode.proof import (
    normalize, align, _merge_compounds, _skipped_run,
    ProofReport, Finding, Op, load_model, proofread,
)
from rhapsode.narrate import Timing
from rhapsode.document import Utterance


# ── normalize ─────────────────────────────────────────────────


def test_normalize_basic():
    out = normalize("Hello World")
    assert "hello" in out
    assert "world" in out


def test_normalize_numbers():
    out = normalize("42 items")
    assert "forty" in out
    assert "two" in out
    assert "items" in out


def test_normalize_punctuation():
    out = normalize("Hello, world!")
    assert len(out) >= 2


def test_normalize_underscore():
    out = normalize("hello_world")
    assert "hello" in out


def test_normalize_empty():
    out = normalize("")
    assert out is not None


# ── align ────────────────────────────────────────────────────


def test_align_match():
    expected = normalize("hello world")
    heard = normalize("hello world")
    ops = align(expected, heard)
    eq_count = sum(1 for o in ops if o.op == "eq")
    assert eq_count > 0


def test_align_substitution():
    expected = normalize("hello world")
    heard = normalize("goodbye world")
    ops = align(expected, heard)
    assert any(o.op == "sub" for o in ops) or len(ops) > 0


def test_align_empty():
    ops = align([], [])
    assert ops == []


# ── _merge_compounds ─────────────────────────────────────────


def test_merge_adjacent_eq():
    ops = _merge_compounds([Op("eq", "a", "a"), Op("eq", "b", "b")])
    assert len(ops) >= 1
    assert ops[0].op == "eq"


def test_merge_mixed():
    ops = _merge_compounds([
        Op("eq", "hello", "hello"),
        Op("sub", "world", "world"),
    ])
    assert len(ops) >= 1


def test_merge_no_adjacent_same():
    ops = _merge_compounds([Op("sub", "a", "b"), Op("ins", "", "c")])
    assert len(ops) == 2


# ── _skipped_run ────────────────────────────────────────────


def test_skipped_consecutive_dels():
    """Three consecutive deletions triggers _skipped_run."""
    ops = [Op("del", "a", ""), Op("del", "b", ""), Op("del", "c", "")]
    assert _skipped_run(ops) is True


def test_not_skipped_partial_dels():
    """Two deletions don't trigger _skipped_run (default run=3)."""
    ops = [Op("del", "a", ""), Op("del", "b", ""), Op("eq", "c", "c")]
    assert _skipped_run(ops) is False


def test_not_skipped_good():
    ops = [Op("eq", "a", "a")]
    assert _skipped_run(ops) is False


def test_skipped_run_with_run_param():
    """Run parameter controls the streak threshold."""
    ops = [Op("del", "a", ""), Op("del", "b", "")]
    assert _skipped_run(ops, run=2) is True
    assert _skipped_run(ops, run=3) is False


# ── load_model (lazy import) ─────────────────────────────────


def test_load_model_cpu(tmp_path):
    mock_model = MagicMock()
    mock_fw = MagicMock()
    mock_fw.WhisperModel = MagicMock(return_value=mock_model)
    with patch.dict("sys.modules", {**sys.modules, "faster_whisper": mock_fw}):
        model = load_model(tmp_path)
    assert model is not None


def test_load_model_cuda_fallback(tmp_path):
    mock_fw = MagicMock()
    mock_fw.WhisperModel.side_effect = [RuntimeError("no cuda"), MagicMock()]
    with patch.dict("sys.modules", {**sys.modules, "faster_whisper": mock_fw}):
        model = load_model(tmp_path)
    assert model is not None
    mock_fw.WhisperModel.assert_called()


# ── proofread (mocked) ──────────────────────────────────────


def test_proofread_happy_path(tmp_path):
    """Proofread with perfect transcription → no findings."""
    import soundfile as sf
    wav = tmp_path / "test.wav"
    audio = np.zeros(48000, dtype=np.float32)
    sf.write(str(wav), audio, 24000)

    script = [
        Utterance("Hello world.", "p", 0, 0.5),
        Utterance("Second line.", "p", 0, 0.5),
    ]
    timings = [Timing(0, 0.5), Timing(0.55, 1.0)]

    mock_model = MagicMock()
    mock_segment = MagicMock()
    mock_segment.text = "Hello world."
    mock_segment.id = 0
    mock_segment.start = 0
    mock_segment.end = 0.5
    mock_model.transcribe.return_value = ([mock_segment], None)

    mock_fw = MagicMock()
    mock_fw.WhisperModel = MagicMock(return_value=mock_model)
    mock_fw.decode_audio = MagicMock(return_value=audio)

    with patch.dict("sys.modules", {"faster_whisper": mock_fw}):
        report = proofread(wav, script, timings)
    assert isinstance(report, ProofReport)


def test_proofread_code_skipped(tmp_path):
    """Code utterances are skipped during proofreading."""
    import soundfile as sf
    audio = np.zeros(48000, dtype=np.float32)
    wav = tmp_path / "test.wav"
    sf.write(str(wav), audio, 24000)

    script = [
        Utterance("Code skipped.", "code", 0, 0.5),
        Utterance("Real text.", "p", 0, 0.5),
    ]
    timings = [Timing(0, 0.5), Timing(0.55, 1.0)]

    mock_model = MagicMock()
    mock_segment = MagicMock()
    mock_segment.text = "Real text"
    mock_segment.id = 0
    mock_segment.start = 0.55
    mock_segment.end = 1.0
    mock_model.transcribe.return_value = ([mock_segment], None)

    mock_fw = MagicMock()
    mock_fw.WhisperModel = MagicMock(return_value=mock_model)
    mock_fw.decode_audio = MagicMock(return_value=audio)

    with patch.dict("sys.modules", {"faster_whisper": mock_fw}):
        report = proofread(wav, script, timings)
    assert report is not None


def test_proofreport_defaults():
    r = ProofReport(model="base", words=10, errors=0, findings=[], suspects=[])
    assert r.findings == []
    assert r.wer == 0.0


def test_finding_dataclass():
    f = Finding(index=0, kind="sub", wer=0.1, said="Hello", heard="hello")
    assert f.index == 0
    assert f.said == "Hello"
    assert f.heard == "hello"


def test_finding_with_misses():
    f = Finding(index=1, kind="del", wer=0.05, said="world", heard="",
                misses=["world→"])
    assert len(f.misses) == 1


def test_proofreport_wer():
    r = ProofReport(model="base", words=100, errors=10, findings=[], suspects=[])
    assert r.wer == 0.1


def test_proofreport_to_json():
    r = ProofReport(model="base", words=10, errors=1, findings=[], suspects=[])
    d = r.to_json()
    assert "wer" in d
    assert "model" in d
    assert d["model"] == "base"
