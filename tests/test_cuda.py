"""Tests for cuda: library preload."""

from __future__ import annotations

from unittest.mock import patch

from rhapsode.cuda import preload_cuda12, _PATTERNS


def test_patterns_exist():
    assert len(_PATTERNS) == 3
    assert any("cublas" in p for p in _PATTERNS)
    assert any("cudnn" in p for p in _PATTERNS)


def test_preload_first_call():
    result = preload_cuda12()
    assert isinstance(result, bool)


def test_preload_idempotent():
    r1 = preload_cuda12()
    r2 = preload_cuda12()
    assert r1 == r2


def test_preload_cuda12_no_cuda_dir(tmp_path):
    """When cuda libs aren't found, preload returns False."""
    import rhapsode.cuda as cuda
    cuda._done = False
    with patch.object(cuda, "_PATTERNS", []):  # no patterns → never finds anything
        result = preload_cuda12()
    assert result is False




def test_preload_cuda12_resets_done(tmp_path):
    """Resetting _done allows re-attempt."""
    import rhapsode.cuda as cuda
    cuda._done = False
    with patch.object(cuda, "_PATTERNS", []):
        r = preload_cuda12()
    assert r is False
