"""Smoke tests for rhapsode module surface.

Only exercises the Python API — no TTS, whisper, or ffmpeg needed."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from rhapsode import (
    Document, Section, Block, Utterance,
    build_script, load, Lexicon,
    Chapter, ffmetadata,
    slugify,
)


# ── document loading ────────────────────────────────────────────────


def test_booth_files_ship_with_the_package() -> None:
    root = files("rhapsode")
    for name in ("lexicon.txt", "operator.html", "icon.svg", "extract.js"):
        assert root.joinpath(name).is_file()


def test_load_plain_text(tmp_path: Path) -> None:
    f = tmp_path / "note.txt"
    f.write_text("Hello World\n\nThis is the second paragraph.")
    doc = load(f)
    assert doc.title == "Hello World"
    assert len(doc.sections) == 1


def test_load_markdown(tmp_path: Path) -> None:
    f = tmp_path / "page.md"
    f.write_text("# Title\n\nSome text.\n\n## Subheading\nMore text\n")
    doc = load(f)
    assert doc.title == "Title"
    assert len(doc.sections) == 2
    assert doc.sections[1].heading == "Subheading"


def test_load_json(tmp_path: Path) -> None:
    f = tmp_path / "doc.json"
    f.write_text('{"title":"Test","sections":[{"heading":"S1","level":1,'
                 '"blocks":[{"kind":"p","text":"hello"}]}]}')
    doc = load(f)
    assert doc.title == "Test"
    assert len(doc.sections) == 1


def test_load_from_string(tmp_path: Path) -> None:
    doc = load("not-a-file", text="# Hello\n\nWorld\n")
    assert doc.title == "Hello"


# ── build_script ────────────────────────────────────────────────────


def test_script_basic() -> None:
    doc = Document(title="T", sections=[
        Section("Intro", 1, [Block("p", "First para."), Block("li", "Item one")]),
        Section("Details", 2, [Block("p", "More info.")]),
    ])
    script = build_script(doc)
    assert all(isinstance(u, Utterance) for u in script)
    assert len(script) == 5  # heading + p + li + heading + p
    # at minimum headings and paragraphs
    kinds = {u.kind for u in script}
    assert "heading" in kinds


def test_script_skips_code() -> None:
    doc = Document(title="T", sections=[
        Section("S", 1, [Block("code", "x=1")]),
    ])
    script = build_script(doc, code="skip")
    # code skipped entirely
    assert len(script) == 1  # only heading


def test_script_table() -> None:
    doc = Document(title="T", sections=[
        Section("S", 1, [
            Block("table", rows=[["A", "1"], ["B", "2"]],
                  header=["Name", "Value"], caption="Numbers")
        ]),
    ])
    script = build_script(doc)
    assert any(u.kind == "table" for u in script)


# ── lexicon ─────────────────────────────────────────────────────────


def test_lexicon_roundtrip() -> None:
    lex = Lexicon()
    out = lex.apply("The URL https://example.com is cool")
    assert out  # non-empty


def test_lexicon_custom_entry(tmp_path: Path) -> None:
    lex_file = tmp_path / "lex.txt"
    lex_file.write_text("myword = [mī'wərd]\n")
    lex = Lexicon.load(extra=[lex_file])
    assert "myword" in lex.entries


# ── chapter / metadata ─────────────────────────────────────────────


def test_ffmetadata_minimal() -> None:
    meta = ffmetadata("Title", "Art", "Comment", [])
    assert "[CHAPTER]" not in meta
    assert "TITLE=Title" not in meta  # FFMETADATA uses lowercase title=
    assert "title=Title" in meta


def test_ffmetadata_with_chapters() -> None:
    ch = [Chapter(0, 5, "Intro"), Chapter(5, 10, "Conclusion")]
    meta = ffmetadata("Doc", "Rhapsode", "test", ch)
    assert meta.count("[CHAPTER]") == 2


# ── paths ───────────────────────────────────────────────────────────


def test_slugify() -> None:
    assert slugify("Hello World!") == "Hello-World"
    assert slugify("  spaces  ") == "spaces"
    assert "-" in slugify("a---b")
    assert slugify("") == "untitled"


def test_booth_page_has_the_living_seal() -> None:
    html = files("rhapsode").joinpath("operator.html").read_text(encoding="utf-8")
    assert 'id="seal"' in html
    assert 'id="seal-needle"' in html
    assert "function paintSeal" in html
    assert "function needleX" in html
    assert "function threadColor" in html
    assert "is-muted" in html
    assert "prefers-reduced-motion" in html
    assert "--stitch" in html
    assert "w-active" in html


def test_booth_page_fills_the_section_chart() -> None:
    html = files("rhapsode").joinpath("operator.html").read_text(encoding="utf-8")
    assert "gantt-fill" in html
    assert "function sectionFill" in html
    assert "function slideSectionName" in html
    assert "stitch-flash" in html
    assert "section-in" in html
    assert 'id="gantt"' in html
    assert 'id="section-name"' in html
    assert "gantt-head" in html


def test_booth_page_draws_the_waveform() -> None:
    html = files("rhapsode").joinpath("operator.html").read_text(encoding="utf-8")
    assert 'id="wave"' in html
    assert 'id="wave-row"' in html
    assert 'id="sync"' in html
    assert "function paintWave" in html
    assert "function placeWave" in html
    assert "function syncToPlaying" in html
    assert "function wavePeaks" in html
    assert "function clipAtPlayhead" in html
    assert "is-muted" in html
    assert "getChannelData" in html
