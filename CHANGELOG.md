# Changelog

This project follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)
and [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The version number lives in `pyproject.toml`. A published revision gets a Git
tag with a `v` prefix that matches it, such as `v0.0.1`.

While the major version is 0, the public API is not stable. Patch releases
are fixes. Minor releases may add or change behavior. `1.0.0` is the first
stable API.

## [Unreleased]

## [0.0.1] - 2026-10-01

First public revision.

Built with Grok 4.7 as the arbitrator for π, the local coding agent. π ran Ollama `qwen3.6:27b` with a 65,536-token context window. Grok drove that generation on this machine's Blackwell GPU, an NVIDIA GeForce RTX 5090 with 32 GB of GPU RAM (32,607 MiB). The run consumed 64 GB of system RAM and 51 million edge tokens.
