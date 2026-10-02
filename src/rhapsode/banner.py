"""Nameplate shared by the README, ``rhapsode --help``, and ``make help``."""

from __future__ import annotations

# 60 columns. The stroke under the name is the booth's playhead.
BANNER = """\
+----------------------------------------------------------+
|                                                          |
|  #####  #   #   ###   #####   ####   ###   ####   #####  |
|  #   #  #   #  #   #  #   #  #      #   #  #   #  #      |
|  #####  #####  #####  #####   ###   #   #  #   #  ####   |
|  #  #   #   #  #   #  #          #  #   #  #   #  #      |
|  #   #  #   #  #   #  #      ####    ###   ####   #####  |
|                                                          |
|  the page, read aloud                                    |
|                                                          |
|          ----------|---------------                      |
|      --------------+---------------------------          |
|              ------|---------------------                |
|                                                          |
+----------------------------------------------------------+"""

SUMMARY = (
    "Rhapsode is a local booth. It reads an open web page or a saved "
    "document aloud with Kokoro, keeps your place, and can proofread "
    "the recording and bind it into an audiobook with chapters."
)


def help_banner() -> str:
    """Banner plus the one-paragraph description, for a terminal greeting."""
    return f"{BANNER}\n\n{SUMMARY}\n"
