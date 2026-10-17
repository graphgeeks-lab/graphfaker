"""Reading a project's ``.env``, because that is where the key already is.

Nothing in a generated dataset depends on the environment. This exists for
the one thing that reads it: the semantic layer needs an API key, and the
key is almost always sitting in a ``.env`` next to the code rather than
exported in the shell. Making people export it again to run one command is
friction with nothing behind it.

Written out rather than taken from ``python-dotenv`` for the usual reason
this project writes things out: it is thirty lines for the format people
actually write, the package would be a dependency of the base install for
one feature of one extra, and the parts of the format that are genuinely
hard (interpolation, multiline values) are parts nobody puts an API key in.
What is supported is listed in :func:`parse` and anything else is ignored
rather than guessed at.

Two rules that matter more than the parsing:

* **The shell wins.** A variable already set is never replaced. Someone who
  exported a key on purpose, or a CI job with secrets in the environment,
  does not get overridden by a file they forgot was there.
* **Values never get printed.** The path is reportable, the contents are
  not. :func:`describe` exists so a command can say where it looked without
  saying what it found.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The file name, and the marker that bounds the search.
ENV_FILE = ".env"
REPO_MARKER = ".git"

#: Point at a different file, for a layout this does not guess.
ENV_FILE_VAR = "GRAPHFAKER_ENV_FILE"


def find(start: Path | None = None) -> Path | None:
    """The project's ``.env``: the first one from here up to the repo root.

    Bounded by the repository rather than by the filesystem, so running in
    a subdirectory finds the project's file and running in a temp directory
    does not quietly pick up whatever is in your home. Without a repository
    it looks in one place, which is the directory you are standing in.
    """
    override = os.environ.get(ENV_FILE_VAR)
    if override:
        path = Path(override)
        return path if path.is_file() else None

    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / ENV_FILE
        if candidate.is_file():
            return candidate
        if (directory / REPO_MARKER).exists():
            return None
    return None


def parse(text: str) -> dict[str, str]:
    """``KEY=value`` pairs from a ``.env``.

    Blank lines and ``#`` comments are skipped, a leading ``export`` is
    allowed, surrounding single or double quotes are stripped, and a line
    without ``=`` is ignored. A value is taken literally: no interpolation,
    no escape sequences, nothing clever. A key that is not a plausible
    environment variable name is skipped rather than exported.
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        if not name.replace("_", "").isalnum() or name[0].isdigit():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        found[name] = value
    return found


def load(path: Path | None = None, *, override: bool = False) -> tuple[Path | None, int]:
    """Put a ``.env`` into the environment. Returns the file and how many it set.

    Variables already in the environment are left alone unless ``override``
    is asked for, so the shell beats the file. The count is of variables
    actually set, which is what a report should say: "loaded 2 of 4" tells
    you the other two were already there.
    """
    path = path or find()
    if path is None or not path.is_file():
        return None, 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, 0
    applied = 0
    for name, value in parse(text).items():
        if override or name not in os.environ:
            os.environ[name] = value
            applied += 1
    return path, applied


def describe(path: Path | None, applied: int) -> str:
    """One line about what was loaded, naming no values."""
    if path is None:
        return "no .env found"
    return f"{path}: {applied} variable(s) set"
