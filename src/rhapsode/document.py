"""Readable documents and the speech script built from them.

A Document is what the page extractor (extract.js) produces: a title plus
sections of typed blocks. Markdown and plain text load into the same shape so
anything can be narrated. `build_script` flattens a document into Utterances,
the unit that is synthesized, timed, and proofread.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .speech import clean_text, sentence


@dataclass
class Block:
    kind: str  # p | li | quote | code | table
    text: str = ""
    caption: str = ""
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Section:
    heading: str
    level: int
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Document:
    title: str
    sections: list[Section]
    url: str = ""
    site: str = ""
    lang: str = "en"


@dataclass
class Utterance:
    text: str  # cleaned text, before lexicon markup
    kind: str  # heading | p | li | quote | code | table
    section: int  # index into Document.sections
    pause: float  # seconds of silence after this utterance


PAUSE = {"heading": 0.7, "p": 0.5, "li": 0.3, "quote": 0.6, "code": 0.6, "table": 0.35}
SECTION_GAP = 0.8  # extra silence before every heading after the first


def load(path: str | Path, text: str | None = None) -> Document:
    """Load a document from extractor JSON, Markdown, or plain text."""
    raw = text if text is not None else Path(path).read_text(encoding="utf-8")
    stripped = raw.lstrip()
    if stripped.startswith("{") or stripped.startswith("```") or '"sections"' in raw[:4000]:
        return from_json(_json_payload(raw))
    name = str(path).lower()
    if name.endswith((".md", ".markdown")) or re.search(r"^#{1,6} ", raw, re.M):
        return from_markdown(raw)
    return from_text(raw)


def _json_payload(raw: str) -> dict:
    """Parse extractor output, tolerating a Markdown fence or a preamble line."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object found in input")
    data = json.loads(raw[start : end + 1])
    if isinstance(data, str):  # double-encoded JSON string
        data = json.loads(data)
    return data


def from_json(data: dict) -> Document:
    sections = []
    for s in data.get("sections", []):
        blocks = [
            Block(
                kind=b.get("kind", "p"),
                text=b.get("text", ""),
                caption=b.get("caption", ""),
                header=list(b.get("header", [])),
                rows=[list(r) for r in b.get("rows", [])],
            )
            for b in s.get("blocks", [])
        ]
        sections.append(Section(s.get("heading", ""), int(s.get("level", 1)), blocks))
    return Document(
        title=data.get("title") or (sections[0].heading if sections else "Untitled"),
        sections=sections,
        url=data.get("url", ""),
        site=data.get("site", ""),
        lang=data.get("lang", "en"),
    )


def from_markdown(raw: str) -> Document:
    sections: list[Section] = []
    para: list[str] = []
    code: list[str] | None = None

    def cur() -> Section:
        if not sections:
            sections.append(Section("", 1))
        return sections[-1]

    def flush() -> None:
        if para:
            cur().blocks.append(Block("p", " ".join(para)))
            para.clear()

    for line in raw.splitlines():
        if line.lstrip().startswith("```"):
            flush()
            if code is None:
                code = []
            else:
                cur().blocks.append(Block("code", "\n".join(code)))
                code = None
            continue
        if code is not None:
            code.append(line)
        elif m := re.match(r"^(#{1,6})\s+(.*)", line):
            flush()
            sections.append(Section(m.group(2).strip(), len(m.group(1))))
        elif m := re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)", line):
            flush()
            cur().blocks.append(Block("li", m.group(1)))
        elif m := re.match(r"^\s*>\s?(.*)", line):
            flush()
            cur().blocks.append(Block("quote", m.group(1)))
        elif line.strip():
            para.append(line.strip())
        else:
            flush()
    flush()
    if sections and not sections[0].heading:
        sections[0].heading = "Untitled"
    title = next((s.heading for s in sections if s.level == 1), sections[0].heading if sections else "Untitled")
    return Document(title=title, sections=sections)


def from_text(raw: str) -> Document:
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", raw)]
    paras = [p for p in paras if p]
    title = paras[0][:80] if paras else "Untitled"
    return Document(title=title, sections=[Section(title, 1, [Block("p", p) for p in paras])])


def table_sentences(block: Block, max_rows: int) -> list[str]:
    """Speak a table row by row: 'Row name. Column: value. Column: value.'"""
    out = [sentence(block.caption) if block.caption else "Table."]
    rows = block.rows[:max_rows]
    for row in rows:
        cells = [clean_text(c) for c in row]
        if block.header and len(block.header) == len(cells):
            head = [clean_text(h) for h in block.header]
            parts = [sentence(cells[0])] + [sentence(f"{h}: {v}") for h, v in zip(head[1:], cells[1:]) if v]
        else:
            parts = [sentence(", ".join(c for c in cells if c))]
        out.append(" ".join(parts))
    if len(block.rows) > max_rows:
        out.append(f"And {len(block.rows) - max_rows} more rows.")
    return out


def build_script(doc: Document, code: str = "announce", max_table_rows: int = 25) -> list[Utterance]:
    """Flatten a document into the utterances Kokoro will speak, in order."""
    script: list[Utterance] = []
    for si, sec in enumerate(doc.sections):
        if sec.heading:
            if script:
                script[-1].pause += SECTION_GAP
            script.append(Utterance(sentence(clean_text(sec.heading)), "heading", si, PAUSE["heading"]))
        for b in sec.blocks:
            if b.kind == "table":
                for line in table_sentences(b, max_table_rows):
                    script.append(Utterance(line, "table", si, PAUSE["table"]))
                if script:
                    script[-1].pause = PAUSE["p"]
                continue
            if b.kind == "code":
                if code == "skip":
                    continue
                if code == "announce":
                    script.append(Utterance("Code sample skipped.", "code", si, PAUSE["code"]))
                    continue
            text = clean_text(b.text)
            if text:
                script.append(Utterance(sentence(text), b.kind, si, PAUSE.get(b.kind, PAUSE["p"])))
    return script
