"""Reading a project's ``.env``.

Nothing in a generated dataset depends on the environment; this is for the
one thing that reads it, which is the API key the semantic layer needs. The
two properties worth protecting are that the shell beats the file and that
a value never gets printed.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from graphfaker.cli import app
from graphfaker.env import ENV_FILE_VAR, describe, find, load, parse

runner = CliRunner()


def test_the_format_people_actually_write():
    parsed = parse(
        "\n".join([
            "# a comment",
            "",
            "OPENAI_API_KEY=sk-plain",
            "export ANTHROPIC_API_KEY=sk-exported",
            'QUOTED="sk-double"',
            "SINGLE='sk-single'",
            "SPACED  =  sk-spaced  ",
            "WITH_EQUALS=a=b=c",
            "EMPTY=",
            "not a pair",
            "9LEADING_DIGIT=x",
            "has-a-dash=x",
        ])
    )
    assert parsed == {
        "OPENAI_API_KEY": "sk-plain",
        "ANTHROPIC_API_KEY": "sk-exported",
        "QUOTED": "sk-double",
        "SINGLE": "sk-single",
        "SPACED": "sk-spaced",
        "WITH_EQUALS": "a=b=c",
        "EMPTY": "",
    }


def test_the_shell_beats_the_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ALREADY_SET=from-file\nNOT_SET=from-file\n", encoding="utf-8")
    monkeypatch.setenv("ALREADY_SET", "from-shell")
    monkeypatch.delenv("NOT_SET", raising=False)

    path, applied = load(env)
    assert path == env and applied == 1
    import os

    assert os.environ["ALREADY_SET"] == "from-shell", "a key exported on purpose is not overridden"
    assert os.environ["NOT_SET"] == "from-file"

    # Unless asked, which is what --env-file on a throwaway run is for.
    load(env, override=True)
    assert os.environ["ALREADY_SET"] == "from-file"


def test_the_search_stops_at_the_repository(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_FILE_VAR, raising=False)
    outside = tmp_path / ".env"
    outside.write_text("LEAKED=yes\n", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    deep = repo / "src" / "pkg"
    deep.mkdir(parents=True)

    # Nothing in the repo: the one a directory above the repo is not ours.
    assert find(deep) is None

    # A file at the repo root is found from any depth inside it.
    inside = repo / ".env"
    inside.write_text("OPENAI_API_KEY=x\n", encoding="utf-8")
    assert find(deep) == inside
    # The nearest one wins.
    nearer = repo / "src" / ".env"
    nearer.write_text("OPENAI_API_KEY=y\n", encoding="utf-8")
    assert find(deep) == nearer


def test_an_explicit_file_wins_over_the_search(tmp_path, monkeypatch):
    chosen = tmp_path / "other.env"
    chosen.write_text("PICKED=yes\n", encoding="utf-8")
    monkeypatch.setenv(ENV_FILE_VAR, str(chosen))
    assert find(tmp_path) == chosen
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path / "missing.env"))
    assert find(tmp_path) is None


def test_a_missing_or_unreadable_file_is_not_an_error(tmp_path):
    assert load(tmp_path / "nope.env") == (None, 0)
    assert load(tmp_path) == (None, 0), "a directory is not a .env"
    assert describe(None, 0) == "no .env found"


def test_nothing_ever_prints_a_value(tmp_path, monkeypatch):
    secret = "sk-do-not-print-me"
    env = tmp_path / ".env"
    env.write_text(f"OPENAI_API_KEY={secret}\n", encoding="utf-8")
    monkeypatch.setenv(ENV_FILE_VAR, str(env))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    path, applied = load()
    assert secret not in describe(path, applied)

    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert secret not in result.output
    # It says the key is there, and where it came from, and no more.
    assert "OPENAI_API_KEY" in result.output
    assert str(Path(env)) in result.output


def test_the_cli_loads_it_without_being_asked(tmp_path, monkeypatch):
    env = tmp_path / "picked.env"
    env.write_text("GRAPHFAKER_SMOKE=loaded\n", encoding="utf-8")
    monkeypatch.delenv("GRAPHFAKER_SMOKE", raising=False)
    monkeypatch.delenv(ENV_FILE_VAR, raising=False)

    result = runner.invoke(app, ["--env-file", str(env), "info"])
    assert result.exit_code == 0, result.output
    import os

    assert os.environ.get("GRAPHFAKER_SMOKE") == "loaded"
