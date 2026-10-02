"""Nameplate shared by the README, ``rhapsode --help``, and ``make help``."""

from __future__ import annotations

# 60 columns. The mark is the booth icon: three text rules and a playhead.
BANNER = """\
                   ╭────────────────────╮                   
                   │                    │                   
                   │           ┃        │                   
                   │    ━━━━━━━┃━━━     │                   
                   │    ━━━━━━━┃        │                   
                   │    ━━━━━  ┃        │                   
                   │           ┃        │                   
                   │                    │                   
                   ╰────────────────────╯                   
                                                            
                          Rhapsode                          
                    the page, read aloud                    """
SUMMARY = (
    "Rhapsode is a local booth. It reads an open web page or a saved "
    "document aloud with Kokoro, keeps your place, and can proofread "
    "the recording and bind it into an audiobook with chapters."
)


def help_banner() -> str:
    """Banner plus the one-paragraph description, for a terminal greeting."""
    return f"{BANNER}\n\n{SUMMARY}\n"
