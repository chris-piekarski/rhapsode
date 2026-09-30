"""Make CUDA 12 libraries visible to CTranslate2 (faster-whisper).

System PyTorch here is a CUDA 13 build, while the CTranslate2 wheels are built
against CUDA 12. The venv carries nvidia-cublas-cu12 and nvidia-cudnn-cu12;
loading them with RTLD_GLOBAL before CTranslate2 initialises lets its dlopen()
calls resolve by soname without touching LD_LIBRARY_PATH.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

_PATTERNS = (
    "nvidia/cublas/lib/libcublasLt.so.12",
    "nvidia/cublas/lib/libcublas.so.12",
    "nvidia/cudnn/lib/libcudnn*.so.9",
)
_done = False


def preload_cuda12() -> bool:
    """Load the CUDA 12 runtime libraries if the venv has them. Idempotent."""
    global _done
    if _done:
        return True
    for entry in sys.path:
        root = Path(entry)
        if not (root / "nvidia" / "cublas").is_dir():
            continue
        libs = [f for pat in _PATTERNS for f in sorted(root.glob(pat))]
        # libcudnn.so.9 dispatches to the sub-libraries, so load it last.
        libs.sort(key=lambda f: f.name.startswith("libcudnn.so"))
        try:
            for lib in libs:
                ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
        except OSError:
            return False
        _done = bool(libs)
        return _done
    return False
