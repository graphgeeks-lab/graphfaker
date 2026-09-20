"""The domain registry and the generic CLI commands."""

import re

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


def _plain(output: str) -> str:
    """Typer renders errors with rich: strip ANSI codes and box drawing so the
    assertions see the words, whatever the terminal on the CI runner."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", output)
    text = re.sub(r"[\u2500-\u257f]", " ", text)
    return re.sub(r"\s+", " ", text)


def test_cli_generate_reports_bad_options(tmp_path):
    result = runner.invoke(app, ["generate", "social", "--colour", "blue", "--out", str(tmp_path)])
    assert result.exit_code != 0
    assert "--colour" in _plain(result.output)
    result = runner.invoke(app, ["generate", "nope", "--out", str(tmp_path)])
    assert result.exit_code != 0 and "social" in _plain(result.output)


def test_cli_schema_round_trip(tmp_path):
    """``schema`` writes a domain's schema; ``generate --schema`` runs it and
    produces the same dataset as running the domain with those options."""
    from graphfaker.domains import social
    from graphfaker.engine import generate
    from graphfaker.schema import GraphSchema

    schema_file = tmp_path / "social.yaml"
    result = runner.invoke(app, ["schema", "social", "--total-nodes", "60", "--total-edges", "120", "--out", str(schema_file)])
    assert result.exit_code == 0, result.output
    text = schema_file.read_text(encoding="utf-8")
    assert text.startswith("# Schema of the 'social' domain") and "name: social" in text

    result = runner.invoke(app, ["generate", "--schema", str(schema_file), "--seed", "9", "--out", str(tmp_path / "g")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "g" / "nodes" / "Person.parquet").exists()
    written = GraphSchema.from_yaml(tmp_path / "g" / "schema.yaml")
    assert written.digest() == GraphSchema.from_yaml(schema_file).digest()
    from graphfaker.backends import GraphTables
    from graphfaker.engine import GraphRun, fingerprint

    tables = GraphTables.read_parquet(tmp_path / "g")
    expected = generate(social.schema(60, 120), seed=9)
    # The fingerprint does not depend on table order, so a run read back from
    # Parquet compares equal to the run that wrote it.
    assert fingerprint(GraphRun(schema=expected.schema, tables=tables, truth=expected.truth, manifest=expected.manifest)) == fingerprint(expected)


def test_cli_schema_prints_to_stdout_and_explains_fraud():
    result = runner.invoke(app, ["schema", "social"])
    assert result.exit_code == 0 and "kind: faker" in result.output
    result = runner.invoke(app, ["schema", "fraud"])
    assert result.exit_code == 0, result.output
    assert "entities only" in result.output and "name: fraud" in result.output


def test_cli_generate_schema_errors(tmp_path):
    result = runner.invoke(app, ["generate"])
    assert result.exit_code != 0 and "--schema" in _plain(result.output)
    result = runner.invoke(app, ["generate", "social", "--schema", "x.yaml"])
    assert result.exit_code != 0 and "not both" in _plain(result.output)
    result = runner.invoke(app, ["generate", "--schema", str(tmp_path / "missing.yaml")])
    assert result.exit_code != 0 and "not found" in _plain(result.output)
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nnodes: []\n", encoding="utf-8")
    result = runner.invoke(app, ["generate", "--schema", str(bad)])
    assert result.exit_code != 0 and "not a valid schema" in _plain(result.output)
    bad.write_text("name: [unclosed\n", encoding="utf-8")
    result = runner.invoke(app, ["generate", "--schema", str(bad)])
    assert result.exit_code != 0 and "not valid YAML" in _plain(result.output)
