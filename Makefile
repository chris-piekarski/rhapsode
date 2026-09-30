.PHONY: help test test-fast lint fmt text-to-speech

PYTHON  := .venv/bin/python

help:
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

# ── text-to-speech ──────────────────────────────────────────────────────

text-to-speech:  # make text-to-speech text="hello there" voice="bf_emma" device="cpu"
	@$(PYTHON) scripts/tts.py "$(text)" $(voice) $(device)
