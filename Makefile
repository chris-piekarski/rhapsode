.PHONY: help test test-fast lint fmt wheel text-to-speech

PYTHON  := .venv/bin/python

help:  # show this list
	@$(PYTHON) -c "from rhapsode.banner import help_banner; print(help_banner(), end='')"
	@echo
	@grep -E '^[a-z].*# ' $(MAKEFILE_LIST) | sed 's/Makefile://' | \
		awk -F':.*# ' '{printf "  %-20s %s\n", $$1, $$2}'

# ── test ───────────────────────────────────────────────────────────────────

test:  # run full test suite with coverage
	$(PYTHON) -m pytest tests/ --cov=rhapsode --cov-report=term-missing -q

test-fast:  # run tests without coverage (faster)
	$(PYTHON) -m pytest tests/ -q

# ── lint ───────────────────────────────────────────────────────────────────

lint:  # run ruff linter + mypy type check
	$(PYTHON) -m ruff check src/rhapsode
	$(PYTHON) -m mypy src/rhapsode

fmt:  # auto-format with black and isort
	$(PYTHON) -m black src/ tests/
	$(PYTHON) -m isort src/ tests/

wheel:  # build a wheel and sdist in dist/ (does not upload)
	$(PYTHON) -m build

# ── text-to-speech ──────────────────────────────────────────────────────

# play=1 streams PCM as each piece is synthesized. wav=1 also writes text-to-speech.wav.
# Defaults do both. play=0 is the file only; wav=0 is the speaker only.
play ?= 1
wav  ?= 1

text-to-speech:  # local GPU speech to wav and speaker: make text-to-speech text="phrase"
	@test -n "$(strip $(text))" || { echo 'usage: make text-to-speech text="phrase"' >&2; exit 1; }
	@test "$(play)" = "1" -o "$(wav)" = "1" || { echo 'set play=1, wav=1, or both' >&2; exit 1; }
	@CUDA_VISIBLE_DEVICES=$${CUDA_VISIBLE_DEVICES:-0} $(PYTHON) scripts/tts.py "$(text)" --voice "$(if $(voice),$(voice),af_heart)" --device "$(if $(device),$(device),cuda)" $(if $(filter 1,$(play)),--play,) $(if $(filter 1,$(wav)),,--no-wav)
