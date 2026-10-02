"""Rhapsode — turn web pages into narrated audio books.

Pipeline: extract page text  →  Kokoro TTS  →  proofread with Whisper
          →  ffmpeg encode with chapters.
"""

from .version import __version__
from .document import Document, Section, Block, Utterance, build_script, load
from .narrate import Narrator, Timing
from .speech import Lexicon
from .proof import ProofReport, proofread
from .bind import Chapter, encode, ffmetadata
from .paths import output_dir, inbox_dir, work_dir, slugify
from .mcp import McpClient
from .extractor import extract_page

__all__ = [
    "__version__",
    # document
    "Document", "Section", "Block", "Utterance", "build_script", "load",
    # tts
    "Narrator", "Timing",
    # lexicon
    "Lexicon",
    # proofreading
    "ProofReport", "proofread",
    # encoding
    "Chapter", "encode", "ffmetadata",
    # paths
    "output_dir", "inbox_dir", "work_dir", "slugify",
    # mcp
    "McpClient",
    # extraction
    "extract_page",
]
