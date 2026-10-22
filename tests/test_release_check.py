"""The release gate that stops a second upload of a published version.

Worth testing because it is the one thing standing between a re-run of a
release and a confusing 400 from the index, and because the version it
reads has to agree with the version the workflow's own tag check reads.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_unpublished as gate


def test_the_version_it_reads_is_the_packaged_one():
    """Three places state the version and they have to agree.

    The workflow greps pyproject with sed, this greps it with a regex, and
    the package reports its own. A release where they disagree is one that
    uploads something other than what the tag says.
    """
    from graphfaker import __version__

    root = Path(__file__).resolve().parents[1]
    assert gate.declared_version(root / "pyproject.toml") == __version__


def test_a_published_version_stops_the_release(monkeypatch, capsys):
    monkeypatch.setattr(gate, "published", lambda: {"1.2.0", "1.3.0", "1.4.0"})
    assert gate.main(["check", "1.4.0"]) == 1
    message = capsys.readouterr().err
    assert "already on PyPI" in message
    # The fix is never "run it again", so the message says what it is.
    assert "bump the version and tag again" in message


def test_an_unpublished_version_lets_it_through(monkeypatch, capsys):
    monkeypatch.setattr(gate, "published", lambda: {"1.3.0", "1.4.0"})
    assert gate.main(["check", "1.5.0"]) == 0
    assert "not on PyPI yet" in capsys.readouterr().out


def test_a_project_that_has_never_been_published_is_not_a_failure(monkeypatch):
    """A 404 from the index means a first release, not a broken one."""
    import urllib.error

    def missing(url, timeout=0):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(gate.urllib.request, "urlopen", missing)
    assert gate.published() == set()

    def broken(url, timeout=0):
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)

    # Anything else is a real failure and must not read as "nothing published".
    monkeypatch.setattr(gate.urllib.request, "urlopen", broken)
    with pytest.raises(urllib.error.HTTPError):
        gate.published()


def test_a_pyproject_with_no_version_says_so(tmp_path):
    bare = tmp_path / "pyproject.toml"
    bare.write_text("[project]\nname = 'x'\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="no version found"):
        gate.declared_version(bare)
