"""Text cleanup for the ear, and the pronunciation lexicon.

Kokoro's G2P (misaki) already handles numbers, acronyms, and most proper
nouns. The lexicon is for the rest: each entry either respells a word or pins
its phonemes with misaki's inline markup, `[word](/phonemes/)`.
"""

from __future__ import annotations

import os
import re
import unicodedata
from importlib import resources
from pathlib import Path

_CITATION = re.compile(r"\[(?:\d+(?:[,–-]\s*\d+)*|citation needed|note \d+)\]", re.I)
_URL = re.compile(r"https?://(?:www\.)?([^\s/)\]>]+)[^\s)\]>]*")
_SPACE = re.compile(r"\s+")


def clean_text(text: str) -> str:
    """Make page text speakable: drop citation marks, shorten URLs to domains."""
    text = unicodedata.normalize("NFKC", text)
    text = _CITATION.sub("", text)
    text = _URL.sub(lambda m: m.group(1), text)
    # Square brackets are reserved for the lexicon's phoneme markup.
    text = text.replace("[", "(").replace("]", ")")
    return _SPACE.sub(" ", text).strip()


def sentence(text: str) -> str:
    """Close a fragment (heading, table cell) with a period so prosody falls."""
    text = text.strip()
    if text and text[-1] not in ".!?:;…\"'”’)":
        text += "."
    return text


class Lexicon:
    """Whole-word, case-sensitive pronunciation overrides."""

    def __init__(self, entries: dict[str, str] | None = None) -> None:
        self.entries = dict(entries or {})
        self._pattern: re.Pattern[str] | None = None

    @classmethod
    def load(cls, extra: list[Path] | None = None) -> "Lexicon":
        """Built-in lexicon, then ~/.config/rhapsode/lexicon.txt, then `extra`."""
        lex = cls(parse(resources.files("rhapsode").joinpath("lexicon.txt").read_text(encoding="utf-8")))
        config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "rhapsode" / "lexicon.txt"
        for path in [config, *(extra or [])]:
            if path.is_file():
                lex.entries.update(parse(path.read_text(encoding="utf-8")))
        return lex

    def apply(self, text: str) -> str:
        if not self.entries:
            return text
        if self._pattern is None:
            words = sorted(self.entries, key=len, reverse=True)
            self._pattern = re.compile(r"(?<![\w/])(" + "|".join(map(re.escape, words)) + r")(?![\w])")
        return self._pattern.sub(self._replace, text)

    def _replace(self, m: re.Match[str]) -> str:
        word, value = m.group(1), self.entries[m.group(1)]
        if value.startswith("/") and value.endswith("/") and len(value) > 2:
            return f"[{word}]({value})"
        return value


def parse(source: str) -> dict[str, str]:
    """Parse `word = replacement` lines; `#` starts a comment."""
    entries: dict[str, str] = {}
    for raw in source.splitlines():
        line = raw.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        word, value = (part.strip() for part in line.split("=", 1))
        if word and value:
            entries[word] = value
    return entries
