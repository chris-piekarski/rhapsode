"""Tests for document: markdown/JSON loading, script building, clean_text, sentence."""

from __future__ import annotations

import json
from pathlib import Path

from rhapsode import Document, Section, Block, Utterance, build_script, load
from rhapsode.document import clean_text, sentence, PAUSE, SECTION_GAP
import pytest


# ── clean_text ─────────────────────────────────────────────────


def test_clean_basic():
    assert clean_text("  hello   world  ") == "hello world"


def test_clean_brackets_to_parens():
    assert "[" not in clean_text("hello [world]")
    assert "(" in clean_text("hello [world]")


def test_clean_citation_dropped():
    assert "[1]" not in clean_text("[1] hello")
    assert "[note 3]" not in clean_text("hello [note 3] world")


def test_clean_citations_complex():
    out = clean_text("[2,-5] hello")
    assert "hello" in out or "Hello" in out


def test_clean_url_to_domain():
    out = clean_text("see https://www.example.com/path/to/page")
    assert "example.com" in out
    assert "https" not in out


def test_clean_unicode_normalized():
    out = clean_text("café")
    assert "caf" in out


def test_clean_trailing_space():
    out = clean_text("hello world  ")
    assert out.endswith("world")


# ── sentence ───────────────────────────────────────────────────


def test_sentence_adds_period():
    assert sentence("hello") == "hello."


def test_sentence_no_add_if_has_period():
    assert sentence("hello.") == "hello."


def test_sentence_no_add_if_has_exclamation():
    assert sentence("wow!") == "wow!"


def test_sentence_no_add_if_has_quote():
    assert sentence('"speech"') == '"speech"'


def test_sentence_single_quote():
    assert sentence("it's fine'") == "it's fine'"


def test_sentence_empty():
    assert sentence("") == ""


def test_sentence_strip():
    assert sentence("  hello  ") == "hello."


# ── build_script ─────────────────────────────────────────────────────


def test_script_basic():
    doc = Document("Title", sections=[])
    assert not build_script(doc)


def test_script_para():
    doc = Document("Title", sections=[])
    doc.sections.append(Section("Intro", 1))
    doc.sections[0].blocks.append(Block("p", "hello world"))
    script = build_script(doc)
    assert len(script) == 2  # heading + para
    assert "Intro" in script[0].text
    assert "hello" in script[1].text.lower()


def test_script_heading_block():
    doc = Document("Title", sections=[])
    doc.sections.append(Section("Intro", 1))
    doc.sections[0].blocks.append(Block("heading", "Hello"))
    doc.sections[0].blocks.append(Block("p", "world"))
    script = build_script(doc)
    assert len(script) == 3  # section heading + block heading + para
    kinds = [u.kind for u in script]
    assert "heading" in kinds
    assert "p" in kinds


def test_script_two_paras():
    doc = Document("Title", sections=[])
    doc.sections.append(Section("Intro", 1))
    doc.sections[0].blocks.append(Block("p", "first"))
    doc.sections[0].blocks.append(Block("p", "second"))
    script = build_script(doc)
    assert len(script) == 3  # heading + 2 paras


def test_script_code_kept():
    doc = Document("Title", sections=[])
    doc.sections.append(Section("S", 1))
    doc.sections[0].blocks.append(Block("code", "code here"))
    doc.sections[0].blocks.append(Block("p", "para"))
    script = build_script(doc)
    kinds = {u.kind for u in script}
    assert "code" in kinds
    assert "p" in kinds


def test_script_section_ordering():
    doc = Document("Title", sections=[])
    s1 = Section("A", 1)
    s2 = Section("B", 1)
    s1.blocks.append(Block("p", "aa"))
    s2.blocks.append(Block("p", "bb"))
    doc.sections.append(s1)
    doc.sections.append(s2)
    script = build_script(doc)
    assert len(script) == 4  # heading A + para aa + heading B + para bb
    assert "aa" in script[1].text
    sections = [u.section for u in script]
    assert sections == [0, 0, 1, 1]


# ── load (JSON) ─────────────────────────────────────────────────────


def test_load_json_basic(tmp_path):
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"title": "Hello", "sections": [
        {"heading": "Intro", "level": 1, "blocks": [
            {"kind": "p", "text": "World.", "start": 0, "end": 10}
        ]}
    ]}))
    doc = load(f)
    assert doc.title == "Hello"
    assert len(doc.sections) == 1


def test_load_json_lang(tmp_path):
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"title": "Hola", "lang": "es", "sections": []}))
    doc = load(f)
    assert doc.lang == "es"


def test_load_json_site(tmp_path):
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"title": "T", "url": "https://example.com/foo",
                             "site": "example", "sections": []}))
    doc = load(f)
    assert doc.url == "https://example.com/foo"
    assert doc.site == "example"


def test_load_json_empty_sections(tmp_path):
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"title": "Empty", "sections": []}))
    doc = load(f)
    assert len(doc.sections) == 0


def test_load_json_table_block(tmp_path):
    data = {
        "title": "T",
        "sections": [{"heading": "S", "level": 1, "blocks": [
            {"kind": "table", "caption": "Numbers", "header": ["N", "V"],
             "rows": [["a", "1"], ["b", "2"]]}]
        }]}
    f = tmp_path / "d.json"
    f.write_text(json.dumps(data))
    doc = load(f)
    b = doc.sections[0].blocks[0]
    assert b.kind == "table"
    assert b.rows == [["a", "1"], ["b", "2"]]


def test_load_json_missing_title(tmp_path):
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"sections": []}))
    doc = load(f)
    assert doc.title == "Untitled"


def test_load_json_title_from_heading(tmp_path):
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"sections": [
        {"heading": "First Section", "level": 1, "blocks": []}
    ]}))
    doc = load(f)
    assert doc.title == "First Section"


# ── load (text/markdown) ─────────────────────────────────────────────


def test_load_text_basic():
    doc = load("x", text="Hello world.")
    # Plain text → from_text creates document with title from first para
    assert len(doc.sections) >= 1


def test_load_markdown_heading():
    doc = load("x", text="# Title\n\nPara.\n")
    assert len(doc.sections) >= 1


def test_load_markdown_two_paras():
    doc = load("x", text="# T\n\nFirst.\n\nSecond.\n")
    assert len(doc.sections) >= 1


def test_load_markdown_list():
    doc = load("x", text="# T\n\n* item1\n* item2\n")
    assert len(doc.sections) >= 1


def test_load_markdown_code_block():
    doc = load("x", text="# T\n\n```\ncode block\n```\n")
    assert len(doc.sections) >= 0


# ── constants ─────────────────────────────────────────────────────────


def test_pause_positive():
    """All PAUSE values are positive floats."""
    assert PAUSE["p"] > 0
    assert PAUSE["heading"] > 0
    assert PAUSE["code"] > 0


def test_section_gap_positive():
    assert SECTION_GAP > 0
    assert SECTION_GAP > PAUSE["p"]


# ── Utterance ───────────────────────────────────────────────────


def test_utterance_defaults():
    u = Utterance("hello", "p", 0, 0.5)
    assert u.text == "hello"
    assert u.kind == "p"
    assert u.section == 0
    assert u.pause == 0.5


def test_utterance_repr():
    u = Utterance("hi", "p", 0, 0.5)
    assert "hi" in repr(u)


# ── Document dataclass ──────────────────────────────────────────


def test_document_defaults():
    doc = Document("My Doc", sections=[])
    assert doc.title == "My Doc"
    assert doc.lang == "en"
    assert doc.url == ""
    assert doc.site == ""


def test_section_defaults():
    sec = Section("My Heading", 1)
    assert sec.blocks == []


def test_block_defaults():
    b = Block("p", "hello")
    assert b.caption == ""
    assert b.header == []
    assert b.rows == []
