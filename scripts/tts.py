"""Synthesize text to speech for make text-to-speech target.

Usage:
    make text-to-speech text="hello there"
    make text-to-speech text="hello there" voice="bf_emma"
"""
import sys
from pathlib import Path
from rhapsode.document import Document, Section, Block, build_script
from rhapsode.narrate import Narrator
from rhapsode.speech import Lexicon

text = sys.argv[1] if len(sys.argv) > 1 else "What is so special about 42?"
voice = sys.argv[2] if len(sys.argv) > 2 else "af_heart"
device = sys.argv[3] if len(sys.argv) > 3 else "cpu"
out = Path("text-to-speech.wav")

doc = Document(
    title="TTS",
    sections=[Section(heading="Ad-hoc", level=1, blocks=[Block(kind="p", text=text)])],
)
script = build_script(doc)
timings = Narrator(voice=voice, device=device).narrate(
    script, out, Lexicon(), progress=False
)
print(f"Done: {out}  ({timings[-1].end:.1f}s)")
