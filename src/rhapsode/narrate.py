"""Kokoro-82M narration: utterances in, PCM pieces out.

Each piece can be written to a WAV, streamed to the speaker, or both.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
import warnings
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from .cache import load_or_render, origin_for
from .document import Utterance, Document
from .speech import Lexicon

SAMPLE_RATE = 24_000
KOKORO_REPO = "hexgrad/Kokoro-82M"
# First letter of a Kokoro voice id selects its language pipeline.
LANGS = {"a": "American English", "b": "British English", "e": "Spanish", "f": "French",
         "h": "Hindi", "i": "Italian", "j": "Japanese", "p": "Brazilian Portuguese", "z": "Mandarin"}

#: Voices that exist in hexgrad/Kokoro-82M. Eric is am_eric, not af_eric.
ENGLISH_VOICES = [
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam",
    "am_michael", "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
]


@dataclass
class Timing:
    start: float  # seconds
    end: float  # seconds, excluding the trailing pause


# ── Voice info helpers (no Kokoro imports needed) ─────────────────

def voice_info(voice_id: str) -> dict:
    """Name, accent, and gender for one Kokoro id. ``af_heart`` is Heart, an American woman."""
    accent = {"a": "American", "b": "British"}.get(voice_id[:1], LANGS.get(voice_id[:1], "English"))
    gender = {"f": "woman", "m": "man"}.get(voice_id[1:2], "")
    name = voice_id.split("_", 1)[-1].replace("_", " ").title() or voice_id
    who = f"{accent} {gender}".strip()
    group = f"{accent} {gender}s".replace("mans", "men") if gender else accent
    return {
        "id": voice_id,
        "name": name,
        "accent": accent,
        "gender": gender,
        "group": group,
        "label": f"{name} · {who}" if who else name,
    }


def voice_label(voice_id: str) -> str:
    """Return a readable label such as ``Heart · American woman``."""
    if voice_id not in ENGLISH_VOICES and voice_id[:1] not in LANGS:
        return f"Unknown voice {voice_id}"
    return voice_info(voice_id)["label"]


# ── Narrator ──────────────────────────────────────────────────────

class Narrator:
    def __init__(self, voice: str = "af_heart", speed: float = 1.0, device: str | None = None, cache=None) -> None:
        if voice[:1] not in LANGS:
            raise ValueError(f"unknown Kokoro voice {voice!r}; ids start with one of {''.join(LANGS)}")
        warnings.filterwarnings("ignore", module=r"torch\..*")
        warnings.filterwarnings("ignore", category=FutureWarning)
        import torch
        from kokoro import KModel, KPipeline

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.voice, self.speed = voice, speed
        self.cache = cache
        self._lang_code = voice[:1]
        if self.device.startswith("cuda"):
            # Build the lexicon before the model touches CUDA. Loading Kokoro
            # onto the GPU first corrupts misaki's phoneme check.
            # cuDNN 9.19 also segfaults in Kokoro's weight-norm Conv1d on Blackwell.
            torch.backends.cudnn.enabled = False
            self.pipe = KPipeline(lang_code=self._lang_code, repo_id=KOKORO_REPO, model=False, device=self.device)
            self.pipe.model = KModel(repo_id=KOKORO_REPO).to(self.device).eval()
        else:
            self.pipe = KPipeline(lang_code=self._lang_code, repo_id=KOKORO_REPO, device=self.device)

    # ── voice switching ───────────────────────────────────────────

    def set_voice(self, voice: str) -> None:
        """Live-override the active voice.

        Same-language swap (e.g. ``af_bella`` → ``am_adam``) just changes the
        voice tensor reference — no pipeline rebuild. Cross-language requires
        a new KPipeline (slow, ~1–2 s download).
        """
        if voice[:1] not in LANGS:
            raise ValueError(f"unknown Kokoro voice {voice!r}")
        new_lang = voice[:1]
        if new_lang != self._lang_code:
            # Cross-language: rebuild pipeline.
            from kokoro import KModel, KPipeline
            self._lang_code = new_lang
            if self.device.startswith("cuda"):
                self.pipe = KPipeline(lang_code=new_lang, repo_id=KOKORO_REPO, model=False, device=self.device)
                self.pipe.model = KModel(repo_id=KOKORO_REPO).to(self.device).eval()
            else:
                self.pipe = KPipeline(lang_code=new_lang, repo_id=KOKORO_REPO, device=self.device)
        self.pipe.load_single_voice(voice)
        self.voice = voice

    # ── synthesis ─────────────────────────────────────────────────

    def iter_pcm(self, text: str) -> Iterator[np.ndarray]:
        """Yield each synthesized piece as a mono float32 buffer."""
        for audio, _words, _tokens in self.iter_voiced(text):
            yield audio

    def iter_voiced(self, text: str, origin=None) -> Iterator[tuple[np.ndarray, list[dict], int]]:
        """Yield cleaned audio, word times, and the phoneme tokens Kokoro spoke.

        The token count is the phoneme string length fed to the model. When a
        result has no phoneme string, it is the number of grapheme tokens.
        """

        def _render():
            for result in self.pipe(text, voice=self.voice, speed=self.speed):
                samples = _owned_pcm(getattr(result, "audio", None))
                if samples.size == 0:
                    continue
                phonemes = getattr(result, "phonemes", None) or ""
                raw_tokens = getattr(result, "tokens", None) or []
                token_count = len(phonemes) if phonemes else len(raw_tokens)
                cleaned, spans = tighten_silence_map(samples)
                words: list[dict] = []
                for tok in raw_tokens:
                    label = (getattr(tok, "text", None) or "").strip()
                    start_ts = getattr(tok, "start_ts", None)
                    end_ts = getattr(tok, "end_ts", None)
                    if not label or start_ts is None or end_ts is None:
                        continue
                    start = _map_sample(spans, int(start_ts * SAMPLE_RATE), len(samples))
                    end = _map_sample(spans, int(end_ts * SAMPLE_RATE), len(samples))
                    if end <= start:
                        continue
                    words.append({
                        "text": label,
                        "start": start / SAMPLE_RATE,
                        "end": end / SAMPLE_RATE,
                    })
                yield cleaned, words, token_count

        pieces = load_or_render(
            self.cache, self.voice, self.speed, text,
            SAMPLE_RATE, KOKORO_REPO, _render,
            origin,
        )
        yield from pieces

    def render(self, text: str) -> np.ndarray:
        parts = list(self.iter_pcm(text))
        return np.concatenate(parts) if parts else np.zeros(0, np.float32)

    def narrate(
        self,
        script: list[Utterance],
        wav: Path | None,
        lexicon: Lexicon,
        progress: bool = True,
        play: bool = False,
        speaker: "PcmSpeaker | None" = None,
        doc: Document | None = None,
    ) -> list[Timing]:
        """Send every PCM piece to a WAV, the speaker, or both.

        `wav` writes 16-bit PCM to disk. `play` streams each piece to the
        speaker as soon as Kokoro returns it. Timings mark spoken audio and
        leave out the silence that follows an utterance.
        """
        if wav is None and not play:
            raise ValueError("narrate needs a wav path, play=True, or both")
        if not play:
            speaker = None
        timings: list[Timing] = []
        pos, t0, total = 0, time.monotonic(), len(script)
        own_speaker = speaker is None and play
        if own_speaker:
            speaker = PcmSpeaker()
        wav_cm = (
            sf.SoundFile(wav, "w", samplerate=SAMPLE_RATE, channels=1, subtype="PCM_16")
            if wav is not None
            else nullcontext()
        )
        try:
            with wav_cm as out:
                for i, utt in enumerate(script, 1):
                    spoken = 0
                    for audio, _words, _tokens in self.iter_voiced(
                        lexicon.apply(utt.text),
                        origin=origin_for(doc, utt, i - 1) if doc is not None else None,
                    ):
                        _emit(out, speaker, audio)
                        spoken += len(audio)
                    timings.append(Timing(pos / SAMPLE_RATE, (pos + spoken) / SAMPLE_RATE))
                    pos += spoken
                    gap = np.zeros(int(utt.pause * SAMPLE_RATE), np.float32)
                    if gap.size:
                        _emit(out, speaker, gap)
                        pos += len(gap)
                    if progress and (i % 20 == 0 or i == total):
                        el = time.monotonic() - t0
                        print(f"\r  narrating {i}/{total}  {pos / SAMPLE_RATE / 60:5.1f} min of audio"
                              f"  ({pos / SAMPLE_RATE / max(el, 1e-6):.0f}x realtime)", end="", file=sys.stderr)
        finally:
            if own_speaker and speaker is not None:
                speaker.close()
        if progress:
            print(file=sys.stderr)
        return timings


# ── low-level helpers ─────────────────────────────────────────────

def _owned_pcm(audio) -> np.ndarray:
    """Copy model audio into a buffer Kokoro cannot reuse or free."""
    if audio is None:
        return np.zeros(0, np.float32)
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    elif hasattr(audio, "numpy"):
        audio = audio.numpy()
    return np.array(np.asarray(audio, dtype=np.float32).reshape(-1), dtype=np.float32, copy=True)


def tighten_silence_map(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    """Silence-tighten and return kept-span map.

    Returns ``(cleaned_audio, spans)`` where each span is
    ``(src_start, src_end, dst_start)`` — a contiguous kept region
    in the source mapped to a position in the cleaned buffer.
    """
    audio = np.ascontiguousarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return audio, []
    silent = np.abs(audio) < 0.01
    lead_keep = int(0.030 * sample_rate)
    tail_keep = int(0.080 * sample_rate)
    edge_keep = int(0.030 * sample_rate)
    min_internal = int(0.120 * sample_rate)

    runs: list[tuple[int, int]] = []
    i = 0
    n = audio.size
    while i < n:
        if not silent[i]:
            i += 1
            continue
        j = i + 1
        while j < n and silent[j]:
            j += 1
        runs.append((i, j))
        i = j
    if not runs:
        return audio, [(0, n, 0)]

    pieces: list[np.ndarray] = []
    spans: list[tuple[int, int, int]] = []
    cursor = 0
    dst = 0
    for start, end in runs:
        start, end = int(start), int(end)
        if start > cursor:
            pieces.append(np.array(audio[cursor:start], dtype=np.float32, copy=True))
            spans.append((cursor, start, dst))
            dst += start - cursor
        dur = end - start
        if start == 0:
            keep = min(dur, lead_keep)
            if keep:
                pieces.append(audio[end - keep:end])
                spans.append((end - keep, end, dst))
                dst += keep
        elif end == n:
            keep = min(dur, tail_keep)
            if keep:
                pieces.append(audio[start:start + keep])
                spans.append((start, start + keep, dst))
                dst += keep
        elif dur > min_internal:
            edge = min(edge_keep, dur // 2)
            pieces.append(audio[start:start + edge])
            pieces.append(audio[end - edge:end])
            spans.append((start, start + edge, dst))
            dst += edge
            spans.append((end - edge, end, dst))
            dst += edge
        else:
            pieces.append(audio[start:end])
            spans.append((start, end, dst))
            dst += end - start
        cursor = end
    if cursor < n:
        pieces.append(audio[cursor:])
        spans.append((cursor, n, dst))
        dst += n - cursor
    return np.concatenate(pieces) if pieces else audio[:0], spans


def tighten_silence(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Shorten the quiet holes Kokoro leaves inside a single buffer.

    Leading silence is cut to 30 ms, trailing silence to 80 ms. An internal
    run quieter than 0.01 and longer than 120 ms is cut to 60 ms, keeping
    both edges so the fade in and out stay intact.
    """
    cleaned, _ = tighten_silence_map(audio, sample_rate)
    return cleaned


def _map_sample(spans: list[tuple[int, int, int]], pos: int, n: int) -> int:
    """Map a source sample into the cleaned buffer. Deleted audio snaps forward."""
    if not spans:
        return 0
    pos = min(max(pos, 0), n)
    for src_start, src_end, dst_start in spans:
        if pos < src_start:
            return dst_start
        if pos < src_end:
            return dst_start + (pos - src_start)
    src_start, src_end, dst_start = spans[-1]
    return dst_start + (src_end - src_start)


def _emit(wav: sf.SoundFile | None, speaker: "PcmSpeaker | None", audio: np.ndarray) -> None:
    if wav is not None:
        wav.write(audio)
    if speaker is not None:
        speaker.write(audio)


class PcmSpeaker:
    """Stream float32 mono PCM to the local speakers through ffplay."""

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        ffplay = shutil.which("ffplay")
        if not ffplay:
            raise RuntimeError("ffplay is required to stream audio to the speaker")
        self._proc = subprocess.Popen(
            [
                ffplay, "-nodisp", "-autoexit", "-loglevel", "error",
                "-fflags", "nobuffer", "-flags", "low_delay",
                "-probesize", "32", "-analyzeduration", "0",
                "-f", "f32le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0",
            ],
            stdin=subprocess.PIPE,
        )

    def write(self, audio: np.ndarray) -> None:
        if audio.size == 0:
            return
        if self._proc.poll() is not None:
            raise RuntimeError(f"ffplay exited {self._proc.returncode} before playback finished")
        samples = np.ascontiguousarray(audio, dtype=np.float32).reshape(-1)
        assert self._proc.stdin is not None
        self._proc.stdin.write(samples.tobytes())
        self._proc.stdin.flush()

    def close(self) -> None:
        if self._proc.stdin is not None and not self._proc.stdin.closed:
            self._proc.stdin.close()
        try:
            code = self._proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
            raise RuntimeError("ffplay did not finish playback") from None
        if code != 0:
            raise RuntimeError(f"ffplay exited {code}")
