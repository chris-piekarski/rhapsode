# Rhapsode

Turn web pages into audiobooks — Kokoro-82M for natural-sounding narration, faster-whisper for proofreading, ffmpeg for encoding with chapter markers.

## Quick start

```bash
# Show the script without generating audio
rhapsode run my-page.json --dry

# Narrate a single document
rhapsode narrate my-page.json --voice af_bella --speed 1.2

# Full pipeline: narrate → proofread → encode with chapters
rhapsode run my-page.json

# Skip proofreading
rhapsode run my-page.json --no-proofread
```

```bash
# Stage files into inbox/ for batch processing
rhapsode inbox page1.json page2.json

# Proofread a WAV produced by narrate
rhapsode proof output.wav timings.json my-page.json

# Encode WAV → M4B with chapter markers
rhapsode bind output.wav my-page.json
```

## Architecture

```
document.json  ──build_script()──▶  utterances []
utterances         ──Narrator──▶    WAV (lexicon applied per-utterance)
WAV + script       ──proofread()──▶  ProofReport (WER + findings)
WAV + metadata     ──encode()──▶     M4B/MP3 (with chapter markers)
```

### Modules

| module | job |
|--------|-----|
| `document.py` | Parse JSON/Markdown/text into `Document`, split into `Utterance`s |
| `narrate.py` | Kokoro-82M TTS — render each utterance, write timed WAV |
| `speech.py` | Text cleaning + pronunciation `Lexicon` (applied per-utterance before TTS) |
| `proof.py` | faster-whisper transcribe-back + word-level alignment |
| `bind.py` | ffmpeg M4B/MP3 encoding with FFmpeg metadata chapters |
| `paths.py` | Output/inbox/whisper-dir paths, WSL path interop, slugify |
| `cuda.py` | CUDA 12 library preloading |

### Pipeline flow

1. **`load(path)`** — Parse a document (JSON from extractor, Markdown, or plain text)
2. **`build_script(doc)`** — Split into `Utterance`s (text, kind, section, pause)
3. **`Narrator().narrate(script, wav_path, Lexicon())`** — Speak each utterance, apply lexicon, write WAV with timings
4. **`proofread(wav, script, timings)`** — Transcribe-back with whisper, compute WER and findings
5. **`encode(wav, out, ffmetadata(...chapters...))`** — Encode with chapter markers

### Dependencies

```
kokoro          # Kokoro-82M TTS (via CTranslate2 + PyTorch)
faster-whisper  # transcription for proofreading
soundfile       # audio I/O
numpy           # audio arrays
num2words       # number → word spelling
pydub           # ffmpeg wrapper for encoding
av              # codec backend
ctranslate2     # TTS inference engine
```

## Document format

Documents are JSON with a top-level title and sections. Each section has a heading, level, and blocks:

```json
{
  "title": "My Page",
  "url": "https://example.com/page",
  "site": "example",
  "lang": "en",
  "sections": [
    {
      "heading": "Introduction",
      "level": 1,
      "blocks": [
        { "kind": "p", "text": "Full text of section one…" },
        { "kind": "heading", "text": "Subheading" }
      ]
    }
  ]
}
```

Block kinds: `heading`, `p`, `li`, `quote`, `code`, `table`.

`extract.js` (run via Chrome DevTools MCP / DevTools) produces this format from browser content.

Plain text and Markdown files are auto-converted on load:

```bash
rhapsode run article.md      # Markdown headings become section boundaries
rhapsode run notes.txt       # plain text → single paragraph
```

### Voices

Voice IDs are from the Kokoro-82M model — see [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M). The first letter selects the language pipeline:

| prefix | language |
|--------|----------|
| `a` | American English |
| `b` | British English |
| `e` | Spanish |
| `f` | French |
| `h` | Hindi |
| `i` | Italian |
| `j` | Japanese |
| `p` | Portuguese |
| `z` | Mandarin |

Common voices: `af_heart` (American, default), `bf_emma` (British), `ef_dora` (Spanish), `ff_siwis` (French), `if_alla` (Italian), `hf_english` (Hindi).

### Lexicon

Pronunciation overrides live in `lexicon.txt` within the package (built-in) and can be extended via `~/.config/rhapsode/lexicon.txt`. Format:

```
# Rhapsode pronunciation lexicon
google = GOOGəl
x-ray  = /[ks ray]/    # phoneme pin via misaki syntax
```

Lexicon is applied **per-utterance before TTS rendering**, so the output audio already reflects the substitutions.

## Notes

- **WSL2**: `cuda.py.preload_cuda()` handles CUDA 12 lib paths. `output_dir()` and `inbox_dir()` default to Windows-side paths (`~/Music/Rhapsode`, `~/AppData/Local/Temp/rhapsode`) for cross-OS access. Set `RHAPSODE_OUT` / `RHAPSODE_INBOX` to override.
- **Pause between utterances**: auto-assigned per kind (`heading`: 0.7s, `p`: 0.5s, `li`: 0.3s, `quote`: 0.6s, `code`: 0.6s, `table`: 0.35s). Extra 0.8s gap between sections.
- **Whisper models**: Reuses models from `~/voxium/models` when present. Set `RHAPSODE_WHISPER_DIR` to override.
- **Output directory**: Default `~/.cache/rhapsode/<slug>` for work files, `Music/Rhapsode` for final audio. Set `RHAPSODE_OUT` to customize.
