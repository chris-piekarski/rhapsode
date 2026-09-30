"""Kokoro-82M narration: utterances in, a timed WAV out."""

from __future__ import annotations

import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from .document import Utterance
from .speech import Lexicon

SAMPLE_RATE = 24_000
KOKORO_REPO = "hexgrad/Kokoro-82M"
# First letter of a Kokoro voice id selects its language pipeline.
LANGS = {"a": "American English", "b": "British English", "e": "Spanish", "f": "French",
         "h": "Hindi", "i": "Italian", "j": "Japanese", "p": "Brazilian Portuguese", "z": "Mandarin"}


@dataclass
class Timing:
    start: float  # seconds
    end: float  # seconds, excluding the trailing pause


class Narrator:
    def __init__(self, voice: str = "af_heart", speed: float = 1.0, device: str | None = None) -> None:
        if voice[:1] not in LANGS:
            raise ValueError(f"unknown Kokoro voice {voice!r}; ids start with one of {''.join(LANGS)}")
        warnings.filterwarnings("ignore", module=r"torch\..*")
        warnings.filterwarnings("ignore", category=FutureWarning)
        import torch
        from kokoro import KPipeline

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.voice, self.speed = voice, speed
        self.pipe = KPipeline(lang_code=voice[0], repo_id=KOKORO_REPO, device=self.device)

    def render(self, text: str) -> np.ndarray:
        parts = [r.audio.numpy() for r in self.pipe(text, voice=self.voice, speed=self.speed) if r.audio is not None]
        return np.concatenate(parts).astype(np.float32) if parts else np.zeros(0, np.float32)

    def narrate(self, script: list[Utterance], wav: Path, lexicon: Lexicon, progress: bool = True) -> list[Timing]:
        """Speak every utterance into `wav`, returning where each one landed."""
        timings: list[Timing] = []
        pos, t0, total = 0, time.monotonic(), len(script)
        with sf.SoundFile(wav, "w", samplerate=SAMPLE_RATE, channels=1, subtype="PCM_16") as out:
            for i, utt in enumerate(script, 1):
                audio = self.render(lexicon.apply(utt.text))
                out.write(audio)
                timings.append(Timing(pos / SAMPLE_RATE, (pos + len(audio)) / SAMPLE_RATE))
                pos += len(audio)
                gap = np.zeros(int(utt.pause * SAMPLE_RATE), np.float32)
                out.write(gap)
                pos += len(gap)
                if progress and (i % 20 == 0 or i == total):
                    el = time.monotonic() - t0
                    print(f"\r  narrating {i}/{total}  {pos / SAMPLE_RATE / 60:5.1f} min of audio"
                          f"  ({pos / SAMPLE_RATE / max(el, 1e-6):.0f}x realtime)", end="", file=sys.stderr)
        if progress:
            print(file=sys.stderr)
        return timings
