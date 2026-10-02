"""Synthesize text on the local GPU, to a WAV, the speaker, or both.

Usage:
    make text-to-speech text="hello there"
    make text-to-speech text="hello there" play=0
    make text-to-speech text="hello there" wav=0
"""
import argparse
import sys
from pathlib import Path

import torch

from rhapsode.document import Block, Document, Section, build_script
from rhapsode.narrate import Narrator
from rhapsode.speech import Lexicon


def main() -> None:
    parser = argparse.ArgumentParser(description="Speak text on the local CUDA GPU.")
    parser.add_argument("text")
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--wav", type=Path, default=Path("text-to-speech.wav"))
    parser.add_argument("--no-wav", action="store_true", help="Skip the WAV file")
    parser.add_argument("--play", action="store_true", help="Stream each PCM piece to the speaker")
    args = parser.parse_args()

    wav = None if args.no_wav else args.wav
    if wav is None and not args.play:
        sys.exit("pass --play, leave the WAV on, or use both")
    if args.device.startswith("cuda") and not torch.cuda.is_available():
        sys.exit("local CUDA GPU is not available")

    doc = Document(
        title="TTS",
        sections=[Section(heading="", level=1, blocks=[Block(kind="p", text=args.text)])],
    )
    script = build_script(doc)
    narrator = Narrator(voice=args.voice, device=args.device)
    if not narrator.device.startswith("cuda"):
        sys.exit(f"refusing to speak on {narrator.device}; local GPU required")
    timings = narrator.narrate(script, wav, Lexicon(), progress=False, play=args.play)
    sinks = [str(wav)] if wav is not None else []
    if args.play:
        sinks.append("speaker")
    gpu = torch.cuda.get_device_name(0)
    print(f"Done: {' + '.join(sinks)}  ({timings[-1].end:.1f}s)  device={narrator.device}  gpu={gpu}")


if __name__ == "__main__":
    main()
