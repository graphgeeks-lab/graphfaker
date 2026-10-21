"""Helpers shared between test modules."""

from __future__ import annotations

import re

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_BOX = re.compile(r"[─-╿]")


def said(result) -> str:
    """What the CLI said, with rich's drawing taken off.

    Typer renders an error through rich, which colours it, wraps it to the
    terminal width and puts it in a box. Colour is the part that breaks a
    substring match, because an option is styled a dash at a time:
    ``--force`` arrives as ``\x1b[1;36m-\x1b[0m\x1b[1;36m-force\x1b[0m``, so
    the literal string is not in the output at all. The wrapping breaks the
    rest, because a sentence the assertion looks for arrives with a newline
    somewhere in the middle of it, and where depends on the terminal width.

    Both are why an assertion on an error message passes locally and fails
    in CI, which has happened twice now. Anything asserting on the text of
    a ``typer.BadParameter`` or a usage error goes through here.
    """
    text = _BOX.sub(" ", _ANSI.sub("", result.output))
    return " ".join(text.split())
