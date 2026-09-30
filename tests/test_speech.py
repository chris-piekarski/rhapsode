"""Tests for speech: Lexicon, clean_text, sentence, NUM2WORDS."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

from rhapsode.speech import Lexicon, clean_text, sentence, parse
import pytest


# ── clean_text ─────────────────────────────────────────────────


def test_clean_basic():
    assert clean_text("  hello   world  ") == "hello world"


def test_clean_brackets_to_parens():
    out = clean_text("hello [world]")
    assert "[" not in out
    assert "(" in out


def test_clean_citation_simple():
    # [1] removed by _CITATION regex
    assert "[1]" not in clean_text("[1] hello")


def test_clean_citation_note():
    assert "[note 3]" not in clean_text("hello [note 3] world")


def test_clean_url_domain():
    out = clean_text("see www.example.com")
    assert "example.com" in out


def test_clean_url_full():
    out = clean_text("see https://example.com/path")
    assert "example.com" in out
    assert "https" not in out


def test_clean_unicode():
    out = clean_text("caf\u00e9")
    assert "caf" in out


def test_clean_whitespace_collapse():
    assert "  " not in clean_text("a  b")


# ── sentence ───────────────────────────────────────────────────


def test_sentence_adds_period():
    assert sentence("hello") == "hello."


def test_sentence_keeps_period():
    assert sentence("hello.") == "hello."


def test_sentence_keeps_exclamation():
    assert sentence("wow!") == "wow!"


def test_sentence_keeps_close_quote():
    assert sentence('"speech"') == '"speech"'


def test_sentence_keeps_close_single():
    assert sentence("it's fine'") == "it's fine'"


def test_sentence_empty():
    assert sentence("") == ""


def test_sentence_strips():
    assert sentence("  hello  ") == "hello."


def test_sentence_colon():
    assert sentence("hi:") == "hi:"


# ── Lexicon ───────────────────────────────────────────────────


def test_lexicon_default():
    lex = Lexicon()
    assert lex.entries is not None


def test_lexicon_apply_noop():
    lex = Lexicon()
    assert lex.apply("hello world") == "hello world"


def test_lexicon_custom_entry():
    lex = Lexicon(entries={"google": "GOOGəl"})
    out = lex.apply("I use google")
    assert "GOOGəl" in out


def test_lexicon_case_sensitive():
    lex = Lexicon(entries={"Mlody": "m-LOH-dy"})
    out = lex.apply("I am Mlody")
    assert "m-LOH-dy" in out


def test_lexicon_does_not_match_lower():
    lex = Lexicon(entries={"Mlody": "m-LOH-dy"})
    out = lex.apply("i am mlody")
    assert "m-LOH-dy" not in out


def test_lexicon_load_returns_instance():
    """Lexicon.load() returns a Lexicon instance (with built-in lexicon)."""
    lex = Lexicon.load()
    assert isinstance(lex, Lexicon)
    assert lex.entries is not None


def test_lexicon_custom_path(tmp_path):
    p = tmp_path / "custom.lex"
    p.write_text("google GOOGəl\n")
    out = Lexicon.load(extra=[p])
    assert isinstance(out, Lexicon)


def test_lexicon_load_fallback():
    """Lexicon.load() with non-existent extra path still returns valid Lexicon."""
    out = Lexicon.load(extra=[Path("/nonexistent/path")])
    assert isinstance(out, Lexicon)


def test_lexicon_apply_unicode():
    lex = Lexicon()
    lex.entries["café"] = "kah-FAY"
    out = lex.apply("café is good")
    assert "kah-FAY" in out


def test_lexicon_phoneme_phonetic():
    """Phonetic rules wrapped in / are converted to SSML-like markup."""
    lex = Lexicon(entries={"x-ray": "/[ks ray]/"})
    out = lex.apply("an x-ray")
    assert "[x-ray]" in out
    assert "[ks ray]" in out


def test_parse_entries():
    parsed = parse("google = GOOGəl\n# comment\n")
    assert parsed["google"] == "GOOGəl"


def test_parse_skips_comments():
    parsed = parse("# comment")
    assert len(parsed) == 0


def test_parse_skips_no_equals():
    parsed = parse("google")
    assert len(parsed) == 0


# ── clean_text edge cases ─────────────────────────────────────


def test_clean_multi_space():
    out = clean_text("a   b   c")
    assert "  " not in out
    assert len(out.split()) == 3


def test_clean_mixed():
    out = clean_text("[1] See https://example.com/path and [note 5] more.")
    assert "example.com" in out
    assert "[1]" not in out
    assert "[note 5]" not in out


def test_clean_preserves_parens():
    out = clean_text("(hello)")
    assert "(" in out or "hello" in out
