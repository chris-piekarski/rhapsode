"""Where Rhapsode reads from and writes to.

On WSL the Chrome DevTools MCP server and the audio player live on the
Windows side, so the inbox (extractor output) and the output folder default
to Windows paths reachable from both sides. Every location can be overridden
with an environment variable.
"""

from __future__ import annotations

import getpass
import os
import platform
import re
import subprocess
from pathlib import Path


def is_wsl() -> bool:
    return "microsoft" in platform.release().lower()


def windows_home() -> Path | None:
    if not is_wsl():
        return None
    home = Path("/mnt/c/Users") / os.environ.get("RHAPSODE_WINUSER", getpass.getuser())
    return home if home.is_dir() else None


def output_dir() -> Path:
    if env := os.environ.get("RHAPSODE_OUT"):
        return Path(env).expanduser()
    win = windows_home()
    return (win / "Music" / "Rhapsode") if win else Path.home() / "Music" / "Rhapsode"


def inbox_dir() -> Path:
    """Folder the Chrome DevTools MCP server saves extractor JSON into."""
    if env := os.environ.get("RHAPSODE_INBOX"):
        return Path(env).expanduser()
    win = windows_home()
    return (win / "AppData" / "Local" / "Temp" / "rhapsode") if win else Path("/tmp/rhapsode")


def work_dir(slug: str) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "rhapsode"
    return base / slug


def whisper_models_dir() -> Path | None:
    """Reuse Voxium's downloaded faster-whisper models when they are present."""
    if env := os.environ.get("RHAPSODE_WHISPER_DIR"):
        return Path(env).expanduser()
    win = windows_home()
    for cand in ([win / "voxium" / "models"] if win else []) + [Path.home() / "voxium" / "models"]:
        if any(cand.glob("models--Systran--faster-whisper-*")):
            return cand
    return None


def to_windows(path: Path) -> str:
    """Windows form of a WSL path (for MCP filePath arguments and players)."""
    if not is_wsl():
        return str(path)
    out = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def slugify(text: str, limit: int = 80) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return (slug[:limit].rstrip("-") or "untitled")


def speech_cache_dir() -> Path:
    """Disk cache root for Kokoro TTS synthesis results.

    Override with :envvar:`RHAPSODE_SPEECH_CACHE`.
    """
    if env := os.environ.get("RHAPSODE_SPEECH_CACHE"):
        return Path(env).expanduser()
    return work_dir("speech")
