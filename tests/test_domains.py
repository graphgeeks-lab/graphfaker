"""The domain registry and the generic CLI commands."""

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from graphfaker.cli import _parse_options, app
from graphfaker.domains import Domain, available, get, register
from graphfaker.domains.social import SocialOptions
from graphfaker.engine import GraphRun

runner = CliRunner()


def test_builtin_domains_are_registered():
    names = set(available())
    assert {"social", "fraud"} <= names
    assert get("social").options is SocialOptions
    assert get("social").schema is not None


def test_unknown_domain_names_the_alternatives():
    with pytest.raises(KeyError, match="social"):
        get("laundromat")


def test_run_validates_options_and_generates():
    run = get("social").run(seed=1, total_nodes=40, total_edges=80)
    assert isinstance(run, GraphRun)
    assert run.tables.node_count == 40
    with pytest.raises(ValueError):
        get("social").run(total_nodes="lots")


def test_describe_options_renders_literals():
    rows = {name: kind for name, kind, _ in get("fraud").describe_options()}
    assert rows["hardness"] == "low | medium | high"
    assert rows["scale"] == "float"


def test_register_rejects_duplicate_names():
    class Opts(BaseModel):
        n: int = 1

    domain = Domain(name="social", summary="dup", generate=lambda **kw: None, options=Opts)
    with pytest.raises(ValueError, match="already registered"):
        register(domain)


def test_parse_options():
    assert _parse_options(["--total-nodes", "5", "--topology=uniform", "--flag"]) == {
        "total_nodes": "5",
        "topology": "uniform",
        "flag": "true",
    }


def test_parse_options_decodes_json_values():
    assert _parse_options(["--patterns", '{"cycle": 3}']) == {"patterns": {"cycle": 3}}
    with pytest.raises(Exception, match="not valid JSON"):
        _parse_options(["--patterns", "{cycle: 3}"])


def test_cli_domains_lists_options():
    result = runner.invoke(app, ["domains"])
    assert result.exit_code == 0, result.output
    assert "social:" in result.output and "--total-nodes" in result.output
    assert "fraud:" in result.output and "--hardness <low | medium | high>" in result.output


def test_cli_generate_any_domain(tmp_path):
    result = runner.invoke(
        app, ["generate", "social", "--total-nodes", "30", "--total-edges", "60", "--seed", "3", "--out", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "nodes" / "Person.parquet").exists()
    assert (tmp_path / "manifest.json").exists()


def test_cli_generate_reports_bad_options(tmp_path):
    result = runner.invoke(app, ["generate", "social", "--colour", "blue", "--out", str(tmp_path)])
    assert result.exit_code != 0
    assert "--colour" in result.output
    result = runner.invoke(app, ["generate", "nope", "--out", str(tmp_path)])
    assert result.exit_code != 0 and "social" in result.output
