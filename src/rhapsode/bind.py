"""Binding: encode the narration with ffmpeg, with chapter markers."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

CODECS = {
    ".m4b": ["-c:a", "aac", "-b:a", "64k"],
    ".m4a": ["-c:a", "aac", "-b:a", "64k"],
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "5"],
    ".opus": ["-c:a", "libopus", "-b:a", "40k"],
    ".ogg": ["-c:a", "libopus", "-b:a", "40k"],
    ".wav": ["-c:a", "pcm_s16le"],
}


@dataclass
class Chapter:
    start: float
    end: float
    title: str


def _esc(value: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", value)


def ffmetadata(title: str, artist: str, comment: str, chapters: list[Chapter]) -> str:
    lines = [";FFMETADATA1", f"title={_esc(title)}", f"album={_esc(title)}", f"artist={_esc(artist)}",
             "genre=Speech", f"comment={_esc(comment)}"]
    for ch in chapters:
        lines += ["", "[CHAPTER]", "TIMEBASE=1/1000", f"START={int(ch.start * 1000)}",
                  f"END={int(ch.end * 1000)}", f"title={_esc(ch.title)}"]
    return "\n".join(lines) + "\n"


def encode(wav: Path, out: Path, metadata: str) -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to encode audio (apt install ffmpeg)")
    codec = CODECS.get(out.suffix.lower())
    if codec is None:
        raise ValueError(f"unsupported output type {out.suffix!r}; use one of {', '.join(CODECS)}")
    meta = wav.with_suffix(".ffmeta")
    meta.write_text(metadata, encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-i", str(meta),
           "-map", "0:a", "-map_metadata", "1", "-map_chapters", "1", "-ac", "1", *codec]
    if out.suffix.lower() in (".m4b", ".m4a"):
        cmd += ["-movflags", "+faststart"]
    subprocess.run([*cmd, str(out)], check=True)
