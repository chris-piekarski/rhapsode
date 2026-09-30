# Rhapsode

Turn web pages into narrated audiobooks — Kokoro-82M for natural-sounding narration, faster-whisper for proofreading, ffmpeg for encoding with chapter markers.

## Quick start

```bash
# Stage a document (HTML, text, markdown)
rhapsode inbox my-page.html

# Narrate — produces WAV then MP3 with chapter markers
# (default voice: af_heart, proofread: on, device: auto)
rhapsode run my-page.json --voice af_heart --speed 1.2

# Dry-run — show the script without generating audio
rhapsode run my-page.json --dry
```

## Architecture

```
inbox/<file>.html  ──extract.js──▶  document.json
document.json  ──Narrator──▶  utterances → WAV
WAV  ──Lexicon──▶  proof → ProofReport
WAV + sections  ──ffmpeg──▶  final.mp3 (with chapter markers)
```

### Modules

| module | job |
|--------|-----|
| `document.py` | Parse, split into `Utterance`s, build script |
| `extract.js`  | Run in Chrome via MCP / DevTools to pull page text |
| `narrate.py`  | Kokoro-82M → audio per utterance → timed WAV |
| `speech.py`   | Text cleaning + pronunciation `Lexicon` |
| `proof.py`    | faster-whisper transcribe-back + diff |
| `bind.py`     | ffmpeg M4A/MP3 with metadata chapters |
| `paths.py`    | Output/inbox slug, WSL support |
| `cuda.py`     | CUDA 12 library preloading |

### Dependencies

All are present in `.venv/`. The five direct ones:

```
kokoro        # Kokoro-82M TTS (via CTranslate2 + PyTorch)
faster-whisper      # transcription for proofreading
soundfile   # audio I/O
numpy       # audio arrays
num2words   # number → word spelling
pydub       # ffmpeg wrapper for encoding
```

## Document format

Documents are JSON arrays of sections:

```json
{
  "title": "My Page",
  "sections": [
    {
      "title": "Introduction",
      "text": "Full text of section one…"
    }
  ]
}
```

`extract.js` produces this format from browser content.

### Voices

See [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M).
- `af_heart` — warm American English (default)
- `b-af_heart`, `b-af_xiaoxiao` — British
- `ef_dora` — Spanish
- `ff_siwis` — French
- `hf_english`, `hf_indian` — Hindi

## Notes

- **WSL2**: `cuda.py.preload_cuda()` handles CUDA 12 lib paths
- **Pause between utterances**: config per-utterance (`utt.pause` field)
- **Lexicon**: loaded from `src/rhapsode/lexicon.txt` on first use
