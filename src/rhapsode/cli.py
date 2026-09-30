"""rhapsode — page-to-audiobook CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

app = typer.Typer(
    name="rhapsode",
    help="Turn web pages / documents into narrated audiobooks.",
    add_completion=False,
)


# ── helpers ───────────────────────────────────────────────────────────────

def _preload() -> None:
    from .cuda import preload_cuda12
    preload_cuda12()


def _output_path(name: str, suffix: str, explicit: Path | None) -> Path:
    """Resolve output path, defaulting to output_dir/slug."""
    if explicit:
        return explicit
    from .paths import output_dir, slugify
    return output_dir() / (slugify(name) + suffix)


# ── narrate ──────────────────────────────────────────────────────────────

@app.command(help="Narrate a document → WAV.")
def narrate(
    doc: Path = typer.Argument(..., help="Document file (JSON, markdown, plain text)"),
    voice: str = typer.Option("af_heart", help="Kokoro voice id"),
    speed: float = typer.Option(1.0, help="Speech rate multiplier"),
    output: Path = typer.Option(None, help="WAV output path"),
    device: str | None = typer.Option(None, help="torch device (cuda|cpu)"),
) -> None:
    _preload()
    from .document import load, build_script
    from .narrate import Narrator
    from .speech import Lexicon

    doc_obj = load(doc)
    script = build_script(doc_obj)

    out = _output_path(doc_obj.title, ".wav", output)
    out.parent.mkdir(parents=True, exist_ok=True)

    typer.echo(f"narrating {len(script)} utterances → {out}", err=True)
    timings = Narrator(voice=voice, speed=speed, device=device).narrate(
        script, out, Lexicon(), progress=True
    )
    typer.echo("done.", err=True)
    typer.echo(f"  {timings[-1].end:.1f} s", err=True)


# ── proof ────────────────────────────────────────────────────────────────

@app.command(help="Proofread a WAV against its source utterances.")
def proof(
    wav: Path = typer.Argument(..., help="WAV produced by narrate"),
    timings: Path = typer.Argument(..., help="timings JSON from narrate step"),
    doc: Path = typer.Argument(..., help="Source document"),
    model: str = typer.Option("large-v3", help="Whisper model"),
    threshold: float = typer.Option(0.12, help="WER flag threshold"),
) -> None:
    _preload()
    from .document import load, build_script
    from .narrate import Timing
    from .proof import proofread

    import json

    doc_obj = load(doc)
    script = build_script(doc_obj)
    raw = json.loads(timings.read_text())
    ts = [Timing(t["start"], t["end"]) for t in raw]

    typer.echo("proofreading …", err=True)
    report = proofread(wav, script, ts, model_name=model,
                       threshold=threshold, progress=True)
    typer.echo(f"WER: {report.errors}/{report.words} = {report.wer:.3f}", err=True)
    if report.findings:
        typer.echo(f"\nFlagged ({len(report.findings)}):", err=True)
        for f in report.findings[:10]:
            typer.echo(f"  #{f.index} wer={f.wer}  said: {f.said[:80]}", err=True)
            typer.echo(f"                       heard: {f.heard[:80]}", err=True)
    else:
        typer.echo("proof ✓", err=True)


# ── bind ─────────────────────────────────────────────────────────────────

@app.command(help="Encode WAV → MP3/M4B with chapter markers.")
def bind(
    wav: Path = typer.Argument(..., help="WAV file"),
    doc: Path = typer.Argument(..., help="Document for metadata"),
    output: Path = typer.Option(None, help="Output path (.mp3|.m4b|.ogg)"),
) -> None:
    from .document import load, build_script
    from .paths import slugify, output_dir as _out_base
    from .bind import Chapter, ffmetadata, encode

    doc_obj = load(doc)
    script = build_script(doc_obj)

    out = _out_base() / (slugify(doc_obj.title) + ".m4b") if output is None else output
    out.parent.mkdir(parents=True, exist_ok=True)

    # build chapters from section boundaries
    chapters: list[Chapter] = []
    pos: float = 0
    seen: set[str] = set()

    for utt in script:
        sec = doc_obj.sections[utt.section]
        label = sec.heading or f"Section {utt.section}"
        if label not in seen:
            if chapters:
                chapters[-1].end = pos
            chapters.append(Chapter(pos, 0, label))
            seen.add(label)
        # rough duration: ~15 wps, 50 chars/word approx
        chars_per_second = 15 * 5
        pos += len(utt.text) / max(chars_per_second, 1) + utt.pause

    if chapters:
        chapters[-1].end = pos

    meta = ffmetadata(
        title=doc_obj.title,
        artist="Rhapsode",
        comment=f"from {doc.name}",
        chapters=chapters,
    )
    typer.echo(f"encoding → {out}", err=True)
    encode(wav, out, meta)
    typer.echo("done.", err=True)


# ── run  (full pipeline) ────────────────────────────────────────────────

@app.command(help="Narrate → proof → encode in one shot.")
def run(
    doc: Path = typer.Argument(..., help="Document file"),
    voice: str = typer.Option("af_heart", help="Kokoro voice id"),
    speed: float = typer.Option(1.0, help="Speech rate"),
    output_dir: Path = typer.Option(None, help="Output directory"),
    device: str | None = typer.Option(None, help="torch device"),
    proofread: bool = typer.Option(True, help="Transcribe-back with whisper"),
    dry: bool = typer.Option(False, help="Show script only"),
) -> None:
    _preload()
    from .document import load, build_script
    from .speech import Lexicon
    from .narrate import Narrator
    from .paths import slugify, output_dir as _out_base

    doc_obj = load(doc)
    script = build_script(doc_obj)

    base = output_dir if output_dir else _out_base()
    slug = slugify(doc_obj.title)
    wav_out = base / (slug + ".wav")
    mp3_out = base / (slug + ".mp3")
    base.mkdir(parents=True, exist_ok=True)

    if dry:
        typer.echo(f"  Title      : {doc_obj.title}")
        typer.echo(f"  Sections   : {len(doc_obj.sections)}")
        typer.echo(f"  Utterances : {len(script)}")
        for i, u in enumerate(script):
            txt = u.text[:80].replace("\n", "\\n")
            typer.echo(f"    {i+1:>4} [{u.kind:<7}] {txt}{'…' if len(u.text)>80 else ''}")
        return

    # 1) narrate
    typer.echo(f"narrating {len(script)} utterances → {wav_out}", err=True)
    timings = Narrator(voice=voice, speed=speed, device=device).narrate(
        script, wav_out, Lexicon(), progress=True
    )
    typer.echo(f"  {timings[-1].end:.1f} s of audio", err=True)

    # 2) proofread
    if proofread:
        typer.echo("\nproofreading …", err=True)
        from .proof import proofread
        report = proofread(wav_out, script, timings, threshold=0.12, progress=True)
        typer.echo(f"  WER {report.errors}/{report.words} = {report.wer:.3f}", err=True)
        if report.findings:
            typer.echo(f"  flagged: {len(report.findings)} utterance(s)", err=True)
        else:
            typer.echo("  proof ✓", err=True)

    # 3) bind with chapters
    from .bind import Chapter, encode as _enc, ffmetadata

    chapters: list[Chapter] = []
    seen: set[str] = set()

    for i, utt in enumerate(script):
        sec = doc_obj.sections[utt.section]
        label = sec.heading or f"Section {utt.section}"
        start = timings[i].start if i < len(timings) else 0
        if label not in seen:
            if chapters:
                chapters[-1].end = start
            chapters.append(Chapter(start, 0, label))
            seen.add(label)

    if chapters and timings:
        chapters[-1].end = timings[-1].end

    meta = ffmetadata(
        title=doc_obj.title,
        artist="Rhapsode",
        comment=f"narrated from {doc.name}",
        chapters=chapters,
    )
    typer.echo(f"\nencoding → {mp3_out}", err=True)
    _enc(wav_out, mp3_out, meta)
    typer.echo("done.", err=True)


# ── inbox ────────────────────────────────────────────────────────────────

@app.command(help="Stage files into inbox/ for batch processing.")
def inbox(
    files: list[Path] = typer.Argument(..., help="Files to stage"),
) -> None:
    from .paths import inbox_dir
    inbox_path = inbox_dir()
    inbox_path.mkdir(exist_ok=True)
    for f in files:
        if not f.exists():
            typer.echo(f"not found: {f}", err=True)
            continue
        import shutil
        dest = inbox_path / f.name
        shutil.copy2(f, dest)
        typer.echo(f"staged → {dest}")


if __name__ == "__main__":
    app()
