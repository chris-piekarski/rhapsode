"""rhapsode — the page, read aloud."""

from __future__ import annotations

from pathlib import Path

import typer
from typer.core import TyperGroup

from .banner import BANNER, SUMMARY


class _BannerGroup(TyperGroup):
    """Print the nameplate before Rich wraps the rest of --help."""

    def format_help(self, ctx, formatter) -> None:  # type: ignore[no-untyped-def]
        typer.echo(BANNER)
        typer.echo("")
        super().format_help(ctx, formatter)


app = typer.Typer(
    name="rhapsode",
    help=SUMMARY,
    cls=_BannerGroup,
    add_completion=False,
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if not value:
        return
    from .version import __version__

    typer.echo(__version__)
    raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """The booth reads an open page aloud and can bind the recording into an audiobook."""


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


# ── read (MCP page extraction) ────────────────────────────────────────

@app.command(help="Read a Chrome tab, or the URL you name, and save the page.")
def read(
    url: str = typer.Argument(..., help="Web page URL"),
    narrate: bool = typer.Option(
        False, "--narrate", help="Immediately narrate the extracted page"
    ),
    voice: str = typer.Option("af_heart", help="Kokoro voice id (when --narrate)"),
    speed: float = typer.Option(1.0, help="Speech rate (when --narrate)"),
    output: Path = typer.Option(None, help="WAV output path (when --narrate)"),
    device: str | None = typer.Option(None, help="torch device (when --narrate)"),
    play: bool = typer.Option(False, "--play", help="Stream PCM while narrating"),
    wav_file: bool = typer.Option(True, "--wav/--no-wav", help="Write a WAV file (when --narrate)"),
) -> None:
    _preload()
    import asyncio

    from .extractor import extract_page

    saved: Path | None = None

    async def _run() -> Path:
        p = await extract_page(url)
        typer.echo(f"saved → {p}", err=True)
        return p

    saved = asyncio.run(_run())
    typer.echo(str(saved))

    if narrate:
        narrate_cli(saved, voice, speed, output, device, play, wav_file)


def narrate_cli(
    doc_path: Path,
    voice: str,
    speed: float,
    output: Path | None,
    device: str | None,
    play: bool,
    wav_file: bool,
) -> None:
    """Shared narration logic used by both *narrate* and *read --narrate*."""
    from .document import load, build_script
    from .narrate import Narrator
    from .speech import Lexicon

    doc_obj = load(doc_path)
    script = build_script(doc_obj)

    if not wav_file and not play:
        raise typer.BadParameter("pass --play, leave the WAV on, or use both")
    out = None if not wav_file else _output_path(doc_obj.title, ".wav", output)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)

    narrator = Narrator(voice=voice, speed=speed, device=device)
    dest = "speaker" if out is None else (f"{out} + speaker" if play else str(out))
    typer.echo(f"narrating {len(script)} utterances on {narrator.device} → {dest}", err=True)
    timings = narrator.narrate(script, out, Lexicon(), progress=True, play=play, doc=doc_obj)
    typer.echo("done.", err=True)
    typer.echo(f"  {timings[-1].end:.1f} s", err=True)


# ── narrate ──────────────────────────────────────────────────────────────

@app.command(help="Narrate a document to a WAV you can keep.")
def narrate(
    doc: Path = typer.Argument(..., help="Document file (JSON, markdown, plain text)"),
    voice: str = typer.Option("af_heart", help="Kokoro voice id"),
    speed: float = typer.Option(1.0, help="Speech rate multiplier"),
    output: Path = typer.Option(None, help="WAV output path"),
    device: str | None = typer.Option(None, help="torch device (cuda|cpu)"),
    play: bool = typer.Option(False, "--play", help="Stream each PCM piece to the speaker"),
    wav_file: bool = typer.Option(True, "--wav/--no-wav", help="Write a WAV file"),
) -> None:
    _preload()
    narrate_cli(doc, voice, speed, output, device, play, wav_file)


# ── proof ────────────────────────────────────────────────────────────────

@app.command(help="Proofread a WAV against the page and flag lines that drifted.")
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

@app.command(help="Encode a WAV as an audiobook with chapter markers.")
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

@app.command(help="Narrate, proofread, and encode a document in one pass.")
def run(
    doc: Path = typer.Argument(..., help="Document file"),
    voice: str = typer.Option("af_heart", help="Kokoro voice id"),
    speed: float = typer.Option(1.0, help="Speech rate"),
    output_dir: Path = typer.Option(None, help="Output directory"),
    device: str | None = typer.Option(None, help="torch device"),
    proof: bool = typer.Option(True, "--proof/--no-proof", help="Transcribe-back with whisper"),
    dry: bool = typer.Option(False, help="Show script only"),
    play: bool = typer.Option(False, "--play", help="Stream each PCM piece to the speaker while the WAV is written"),
) -> None:
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
    dest = f"{wav_out} + speaker" if play else str(wav_out)
    typer.echo(f"narrating {len(script)} utterances → {dest}", err=True)
    timings = Narrator(voice=voice, speed=speed, device=device).narrate(
        script, wav_out, Lexicon(), progress=True, play=play, doc=doc_obj
    )
    typer.echo(f"  {timings[-1].end:.1f} s of audio", err=True)

    # 2) proofread
    if proof:
        typer.echo("\nproofreading …", err=True)
        from .proof import proofread as _proofread
        report = _proofread(wav_out, script, timings, threshold=0.12, progress=True)
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


# ── live (reactive operator desk) ────────────────────────────────────────

@app.command(help="Open the booth and speak a document there.")
def live(
    doc: Path = typer.Argument(..., help="Document or extractor JSON", exists=True, dir_okay=False),
    wav: Path = typer.Option(Path("/tmp/rhapsode-listen/live.wav"), help="Where the recording is written"),
    mcp: str | None = typer.Option(None, "--mcp", help="Chrome DevTools websocket the page was read through"),
    page_url: str | None = typer.Option(None, "--page", help="Page URL, if it is not already in the document"),
    port: int = typer.Option(8765, help="Local port for the operator page"),
) -> None:
    from .desk import serve_live

    wav.parent.mkdir(parents=True, exist_ok=True)
    serve_live(wav, doc, mcp, page_url, port=port)


@app.command(help="Speak MCP so an agent can read and drive the live booth.")
def agent(
    desk: str = typer.Option("http://127.0.0.1:8765", help="Operator desk the agent talks to"),
) -> None:
    from .agent import serve

    serve(desk)


# ── play (operator desk) ─────────────────────────────────────────────────

@app.command(help="Open the booth on a recording that already exists.")
def play(
    wav: Path = typer.Argument(..., help="WAV file to operate", exists=True, dir_okay=False),
    doc: Path | None = typer.Option(None, "--doc", help="Document or extractor JSON the recording was read from", exists=True, dir_okay=False),
    mcp: str | None = typer.Option(None, "--mcp", help="Chrome DevTools websocket the page was read through"),
    page_url: str | None = typer.Option(None, "--page", help="Page URL, if it is not already in the document"),
    spoken: str | None = typer.Option(None, "--spoken", help="Sentence in the recording, so the list can follow it"),
    port: int = typer.Option(8765, help="Local port for the operator page"),
) -> None:
    from .desk import serve

    serve(wav, port=port, doc=doc, mcp_endpoint=mcp, page_url=page_url, spoken=spoken)


# ── inbox ────────────────────────────────────────────────────────────────

@app.command(help="Copy documents into the inbox for a later batch.")
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
