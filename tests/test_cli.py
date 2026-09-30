"""CLI smoke tests — use subprocess to invoke CLI to avoid typer/CliRunner compat issues."""

from __future__ import annotations

import subprocess
from pathlib import Path

from rhapsode import slugify
import pytest

PYTHON = ".venv/bin/python"


def _cli(*args) -> subprocess.CompletedProcess[str]:
    """Run the CLI via subprocess."""
    return subprocess.run(
        [PYTHON, "-m", "rhapsode.cli"] + list(args),
        capture_output=True, text=True, timeout=30,
        cwd=str(Path(__file__).resolve().parents[1]),
    )


# ── help output ───────────────────────────────────────────────────────


def test_help():
    r = _cli("--help")
    assert r.returncode == 0
    out = (r.stdout + r.stderr).lower()
    assert "audiobook" in out or "narrat" in out


def test_narrate_help():
    r = _cli("narrate", "--help")
    assert r.returncode == 0
    assert "narrat" in (r.stdout + r.stderr).lower()


def test_run_help():
    r = _cli("run", "--help")
    assert r.returncode == 0
    assert "narrat" in (r.stdout + r.stderr).lower()


def test_bind_help():
    r = _cli("bind", "--help")
    assert r.returncode == 0
    assert "encode" in (r.stdout + r.stderr).lower() or "chapter" in (r.stdout + r.stderr).lower()


def test_proof_help():
    r = _cli("proof", "--help")
    assert r.returncode == 0
    assert "proof" in (r.stdout + r.stderr).lower()


# ── run --dry (no TTS needed) ──────────────────────────────────────────


def test_run_dry(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text("# My Doc\n\nHello world.\n")
    r = _cli("run", str(doc), "--dry")
    assert r.returncode == 0, r.stderr
    assert "My Doc" in r.stdout


def test_run_dry_sections(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text("# T\n\nFirst.\n\n## Sub\n\nSecond.\n")
    r = _cli("run", str(doc), "--dry")
    assert r.returncode == 0, r.stderr
    assert "Sections" in r.stdout


def test_run_dry_no_proofread(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text("# T\n\nH.\n")
    r = _cli("run", str(doc), "--dry", "--no-proofread")
    assert r.returncode == 0, r.stderr


def test_run_dry_output(tmp_path):
    """Dry run should show utterance lines with kind indicators."""
    doc = tmp_path / "doc.md"
    doc.write_text("# T\n\nHi there.\n")
    r = _cli("run", str(doc), "--dry")
    assert r.returncode == 0
    out = r.stdout.lower()
    assert "hi" in out


# ── run without --dry on missing file ────────────────────


def test_run_missing_file():
    r = _cli("run", "/nonexistent_file_xyz.md")
    # Should fail gracefully
    assert r.returncode != 0


# ── inbox ───────────────────────────────────────────────────────────


def test_inbox_stage(tmp_path, monkeypatch):
    doc = tmp_path / "doc.md"
    doc.write_text("hello")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("RHAPSODE_INBOX", str(inbox))
    env = dict(__import__("os").environ, RHAPSODE_INBOX=str(inbox))
    r = subprocess.run(
        [PYTHON, "-m", "rhapsode.cli", "inbox", str(doc)],
        capture_output=True, text=True, timeout=30, env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert r.returncode == 0, r.stderr
    assert (inbox / "doc.md").exists()


def test_inbox_missing_file(tmp_path):
    r = _cli("inbox", "/nonexistent_file_xyz.md")
    assert "not found" in r.stderr or "not found" in r.stdout or r.returncode != 0


def test_inbox_stdout(tmp_path, monkeypatch):
    doc = tmp_path / "file.txt"
    doc.write_text("x")
    inbox = tmp_path / "ib"
    inbox.mkdir()
    env = dict(__import__("os").environ, RHAPSODE_INBOX=str(inbox))
    r = subprocess.run(
        [PYTHON, "-m", "rhapsode.cli", "inbox", str(doc)],
        capture_output=True, text=True, timeout=30, env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert r.returncode == 0
    assert "staged" in r.stdout.lower()


# ── narrate command (missing arg) ───────────────────


def test_narrate_missing_arg():
    r = _cli("narrate")
    assert r.returncode != 0  # requires doc arg


# ── bind command (missing arg) ──────────────────────────

def test_bind_missing_arg():
    r = _cli("bind")
    assert r.returncode != 0


# ── proof command (missing arg) ─────────────────────────

def test_proof_missing_arg():
    r = _cli("proof")
    assert r.returncode != 0
