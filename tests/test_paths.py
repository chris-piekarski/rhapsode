"""Tests for paths: output/inbox dir resolution, slugify, models dir."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from rhapsode.paths import (
    output_dir, inbox_dir, slugify,
    whisper_models_dir, work_dir, is_wsl, to_windows, windows_home,
)


# ── slugify ─────────────────────────────────────────────────────

def test_slugify_simple():
    assert slugify("Hello World") == "Hello-World"


def test_slugify_empty():
    assert slugify("") == "untitled"


def test_slugify_strip():
    assert slugify("  spaces  ") == "spaces"


def test_slugify_multi_dash():
    s = slugify("a---b")
    assert "--" not in s
    assert "a-b" in s


def test_slugify_caps():
    assert slugify("ALL CAPS") == "ALL-CAPS"


def test_slugify_digits():
    assert "2" in slugify("part 2")


def test_slugify_limit():
    long_text = "A " * 100
    s = slugify(long_text, limit=20)
    assert len(s) <= 25  # limit + small tolerance for truncation


# ── output_dir: env override always works ──────────────────────

def test_output_dir_has_music(tmp_path, monkeypatch):
    monkeypatch.setenv("RHAPSODE_OUT", str(tmp_path))
    d = output_dir()
    assert d == tmp_path


def test_output_dir_default_contains_mUSIC(tmp_path, monkeypatch):
    """Default output dir should contain Music/Rhapsode."""
    monkeypatch.delenv("RHAPSODE_OUT", raising=False)
    d = output_dir()
    # Either Windows path or ~/Music/Rhapsode
    assert "Music" in str(d) or "Rhapsode" in str(d)
    assert "Rhapsode" in [p for p in d.parts]


# ── inbox_dir: env override always works ───────────────────────────────

def test_inbox_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("RHAPSODE_INBOX", str(tmp_path / "my_inbox"))
    d = inbox_dir()
    assert d == tmp_path / "my_inbox"


def test_inbox_default():
    d = inbox_dir()
    # On WSL → Windows temp path; on Linux → /tmp/rhapsode
    assert "rhapsode" in str(d).lower() or d == Path("/tmp/rhapsode")


# ── work_dir ───────────────────────────────────────────────────

def test_work_dir_with_slug(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = work_dir("my-slug")
    assert d == tmp_path / "rhapsode" / "my-slug"


def test_work_dir_default_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    d = work_dir("slug")
    assert "rhapsode" in str(d)
    assert "slug" in str(d)


# ── whisper_models_dir ─────────────────────────────────────────

def test_whisper_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("RHAPSODE_WHISPER_DIR", str(tmp_path / "wh"))
    assert whisper_models_dir() == tmp_path / "wh"


# ── slugify edge cases ─────────────────────────────────────────


def test_slugify_unicode_preserved():
    s = slugify("caf\u00e9")
    assert "caf" in s


# ── windows helpers (no special behavior on non-WSL) ───────────

def test_slugify_title_length():
    """Long titles are truncated."""
    s = slugify("A" * 200, limit=10)
    assert len(s) <= 15


# ── is_wsl detects current machine state ─────────────────────

def test_is_wsl_return_type():
    assert isinstance(is_wsl(), bool)


def test_is_wsl_consistent():
    """is_wsl should return same value on repeated calls."""
    r1 = is_wsl()
    r2 = is_wsl()
    assert r1 == r2


# ── windows_home ─────────────────────────────────────────────


def test_windows_home_none_on_non_wsl():
    """On non-WSL, windows_home is None."""
    from rhapsode.paths import is_wsl
    if not is_wsl():
        assert windows_home() is None  # noqa: TID251


def test_windows_home_path_or_none():
    """windows_home returns Path or None."""
    wh = windows_home()
    assert wh is None or isinstance(wh, Path)
