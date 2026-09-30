"""Proofreading by ear: transcribe each narrated utterance with faster-whisper
and compare it to the text it was meant to say.

Kokoro is deterministic, so re-rendering a bad line changes nothing. The
proof's job is to find the words it gets wrong so they can go in the lexicon.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .cuda import preload_cuda12
from .document import Utterance
from .narrate import Timing
from .paths import whisper_models_dir

_NUM = re.compile(r"\d+(?:\.\d+)?")


def normalize(text: str) -> list[str]:
    """Lowercased word tokens with numbers spelled out, for comparing texts."""
    from num2words import num2words

    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("’", "'").replace("%", " percent ").replace("&", " and ")
    text = _NUM.sub(lambda m: " " + num2words(float(m.group()) if "." in m.group() else int(m.group())) + " ", text)
    text = re.sub(r"'s\b", "s", text).replace("'", "")
    return re.findall(r"[a-z0-9]+", text)


@dataclass
class Op:
    op: str  # eq | sub | del | ins
    said: str = ""  # expected word
    heard: str = ""


def align(expected: list[str], heard: list[str]) -> list[Op]:
    """Word-level Levenshtein alignment, then forgive split/joined compounds
    ("langgraph" heard as "lang graph")."""
    n, m = len(expected), len(heard)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if expected[i - 1] == heard[j - 1] else 1
            d[i][j] = min(d[i - 1][j - 1] + cost, d[i - 1][j] + 1, d[i][j - 1] + 1)
    ops: list[Op] = []
    i, j = n, m
    while i or j:
        if i and j and d[i][j] == d[i - 1][j - 1] + (expected[i - 1] != heard[j - 1]):
            ops.append(Op("eq" if expected[i - 1] == heard[j - 1] else "sub", expected[i - 1], heard[j - 1]))
            i, j = i - 1, j - 1
        elif i and d[i][j] == d[i - 1][j] + 1:
            ops.append(Op("del", said=expected[i - 1]))
            i -= 1
        else:
            ops.append(Op("ins", heard=heard[j - 1]))
            j -= 1
    ops.reverse()
    return _merge_compounds(ops)


def _merge_compounds(ops: list[Op]) -> list[Op]:
    out: list[Op] = []
    k = 0
    while k < len(ops):
        a = ops[k]
        b = ops[k + 1] if k + 1 < len(ops) else None
        if b and a.op in ("sub", "eq") and b.op == "ins" and a.said == a.heard + b.heard:
            out.append(Op("eq", a.said, a.said))
            k += 2
        elif b and a.op == "ins" and b.op in ("sub", "eq") and b.said == a.heard + b.heard:
            out.append(Op("eq", b.said, b.said))
            k += 2
        elif b and a.op in ("sub", "eq") and b.op == "del" and a.said + b.said == a.heard:
            out.append(Op("eq", a.heard, a.heard))
            k += 2
        else:
            out.append(a)
            k += 1
    return out


@dataclass
class Finding:
    index: int
    kind: str
    wer: float
    said: str
    heard: str
    misses: list[str] = field(default_factory=list)  # "said→heard" pairs


@dataclass
class ProofReport:
    model: str
    words: int
    errors: int
    findings: list[Finding]
    suspects: list[tuple[str, int]]  # expected words most often misheard

    @property
    def wer(self) -> float:
        return self.errors / max(self.words, 1)

    def to_json(self) -> dict:
        return {**asdict(self), "wer": round(self.wer, 4)}


def load_model(name: str):
    from faster_whisper import WhisperModel

    root = whisper_models_dir()
    kw = {"download_root": str(root), "local_files_only": True} if root else {}
    if preload_cuda12():
        try:
            return WhisperModel(name, device="cuda", compute_type="float16", **kw), "cuda"
        except Exception as exc:  # noqa: BLE001 - any CUDA failure falls back to CPU
            print(f"  proof: CUDA unavailable ({exc}); using CPU", file=sys.stderr)
    return WhisperModel(name, device="cpu", compute_type="int8", **kw), "cpu"


def proofread(wav: Path, script: list[Utterance], timings: list[Timing], model_name: str = "large-v3",
              threshold: float = 0.12, progress: bool = True) -> ProofReport:
    from faster_whisper import decode_audio

    model, device = load_model(model_name)
    audio = decode_audio(str(wav), sampling_rate=16_000)
    findings: list[Finding] = []
    suspects: Counter[str] = Counter()
    words = errors = 0
    for idx, (utt, t) in enumerate(zip(script, timings)):
        if utt.kind == "code":
            continue
        clip = audio[int(t.start * 16_000) : int(t.end * 16_000) + 1600]  # +0.1 s tail
        segments, _ = model.transcribe(clip, language="en", beam_size=5, vad_filter=False,
                                       condition_on_previous_text=False, without_timestamps=True)
        heard = " ".join(s.text.strip() for s in segments)
        ops = align(normalize(utt.text), normalize(heard))
        n = sum(o.op != "ins" for o in ops)
        bad = [o for o in ops if o.op != "eq"]
        words += n
        errors += len(bad)
        suspects.update(o.said for o in bad if o.said)
        wer = len(bad) / max(n, 1)
        if wer >= threshold or _skipped_run(ops):
            misses = [f"{o.said or '∅'}→{o.heard or '∅'}" for o in bad]
            findings.append(Finding(idx, utt.kind, round(wer, 3), utt.text, heard, misses))
        if progress and ((idx + 1) % 25 == 0 or idx + 1 == len(script)):
            print(f"\r  proofreading {idx + 1}/{len(script)} on {device}", end="", file=sys.stderr)
    if progress:
        print(file=sys.stderr)
    return ProofReport(model_name, words, errors, findings, suspects.most_common(15))


def _skipped_run(ops: list[Op], run: int = 3) -> bool:
    streak = 0
    for o in ops:
        streak = streak + 1 if o.op == "del" else 0
        if streak >= run:
            return True
    return False
