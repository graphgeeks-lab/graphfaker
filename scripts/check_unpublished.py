"""Fail if the version in pyproject.toml is already on PyPI.

A release workflow that reaches the upload step with a version that exists
fails there instead, after building and smoke-testing, with a 400 from the
index that reads like an auth problem. This is the same answer in two
seconds and in a sentence.

It also catches the mistake underneath it, which is re-running a release
rather than cutting a new one: a published version cannot be replaced, only
yanked, so the fix is always to bump and tag again.

    python scripts/check_unpublished.py            # the version in pyproject
    python scripts/check_unpublished.py 1.4.0      # or one you name
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = "graphfaker"
INDEX = f"https://pypi.org/pypi/{PROJECT}/json"
TIMEOUT = 30


def declared_version(pyproject: Path) -> str:
    """The version in ``[project]``, read without a TOML parser.

    ``tomllib`` would be nicer and is only in 3.11; this runs on whatever
    Python the runner happens to have, before any dependency is installed.
    """
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        found = re.fullmatch(r'version\s*=\s*"([^"]+)"', line.strip())
        if found:
            return found.group(1)
    raise SystemExit(f"no version found in {pyproject}")


def published(url: str = INDEX) -> set[str]:
    """Every version on the index, or nothing if the project is new."""
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            return set(json.load(response)["releases"])
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return set()  # never published, so nothing to clash with
        raise


def main(argv: list[str]) -> int:
    version = argv[1] if len(argv) > 1 else declared_version(Path("pyproject.toml"))
    releases = published()
    if version in releases:
        recent = ", ".join(sorted(releases)[-5:])
        print(
            f"{version} is already on PyPI. A published version cannot be replaced, only yanked, "
            f"so bump the version and tag again rather than re-running this release. "
            f"Most recent there: {recent}",
            file=sys.stderr,
        )
        return 1
    newest = max(releases, default=None)
    print(f"{version} is not on PyPI yet (newest there: {newest or 'nothing published'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
