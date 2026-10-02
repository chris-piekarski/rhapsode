"""Package version.

``pyproject.toml`` is the only copy of the number. A checkout reads that
file. An installed wheel reads the metadata that was copied from it at
build time.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

__all__ = ["__version__"]


def _from_pyproject() -> str | None:
    path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        bare = line.split("#", 1)[0].strip()
        if bare.startswith("version") and "=" in bare:
            return bare.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def package_version() -> str:
    found = _from_pyproject()
    if found:
        return found
    try:
        return version("rhapsode")
    except PackageNotFoundError as exc:
        raise RuntimeError("rhapsode version is not installed") from exc


__version__ = package_version()
