import json
import sys
from unittest.mock import patch

import networkx as nx
from typer.testing import CliRunner

from graphfaker.cli import app

runner = CliRunner()


def _tiny_graph():
    """A minimal real graph.

    The fetchers must be mocked with an actual graph rather than a string: the
    CLI reports node and edge counts and then exports, so a stand-in has to
    support the NetworkX interface.
    """
    G = nx.DiGraph()
    G.add_node("a", type="Test", name="A")
    G.add_node("b", type="Test", name="B")
    G.add_edge("a", "b", relationship="LINKS_TO")
    return G


def _tiny_osm_graph():
    """A graph shaped enough like an OSMnx result to survive osmnx export."""
    G = nx.MultiDiGraph()
    G.graph["crs"] = "epsg:4326"
    G.graph["simplified"] = True
    G.add_node(1, x=-0.13, y=51.51, street_count=1)
    G.add_node(2, x=-0.14, y=51.52, street_count=1)
    G.add_edge(1, 2, osmid=1001, length=120.5, oneway=False)
    return G


def test_faker_mode_generates_graph(tmp_path):
    result = runner.invoke(
        app,
        [
            "gen",
            "--fetcher",
            "faker",
            "--total-nodes",
            "10",
            "--total-edges",
            "20",
            "--export",
            str(tmp_path / "out.graphml"),
        ],
    )

    assert result.exit_code == 0
    assert "Graph" in result.output or "nodes" in result.output
    assert (tmp_path / "out.graphml").exists()


def test_faker_mode_is_reproducible_with_seed(tmp_path):
    def run(target):
        return runner.invoke(
            app,
            [
                "gen",
                "--fetcher",
                "faker",
                "--total-nodes",
                "20",
                "--total-edges",
                "40",
                "--seed",
                "42",
                "--export",
                str(tmp_path / target),
            ],
        )

    assert run("first.graphml").exit_code == 0
    assert run("second.graphml").exit_code == 0
    assert (tmp_path / "first.graphml").read_bytes() == (
        tmp_path / "second.graphml"
    ).read_bytes()


@patch("graphfaker.fetchers.osm.OSMGraphFetcher.fetch_network")
def test_osm_mode_with_place(mock_fetch, tmp_path):
    mock_fetch.return_value = _tiny_osm_graph()

    result = runner.invoke(
        app,
        [
            "gen",
            "--fetcher",
            "osm",
            "--place",
            "Soho Square, London, UK",
            "--export",
            str(tmp_path / "osm.graphml"),
        ],
    )
    assert result.exit_code == 0
    mock_fetch.assert_called_once()
    assert (tmp_path / "osm.graphml").exists()


def test_osm_mode_without_the_extra_names_it(monkeypatch, tmp_path):
    """osmnx is an optional extra; without it the CLI says which one, in one
    line, rather than a traceback."""
    monkeypatch.setitem(sys.modules, "osmnx", None)  # makes `import osmnx` raise ImportError
    result = runner.invoke(app, ["gen", "--fetcher", "osm", "--place", "Soho Square, London, UK", "--export", str(tmp_path / "osm.graphml")])
    assert result.exit_code != 0
    assert 'pip install "graphfaker[osm]"' in result.output
    assert "Traceback" not in result.output


@patch("graphfaker.fetchers.flights.FlightGraphFetcher.fetch_airlines")
@patch("graphfaker.fetchers.flights.FlightGraphFetcher.fetch_airports")
@patch("graphfaker.fetchers.flights.FlightGraphFetcher.fetch_flights")
@patch("graphfaker.fetchers.flights.FlightGraphFetcher.build_graph")
def test_flight_mode_valid_inputs(
    mock_build_graph,
    mock_fetch_flights,
    mock_fetch_airports,
    mock_fetch_airlines,
    tmp_path,
):
    mock_fetch_airlines.return_value = ["airline1"]
    mock_fetch_airports.return_value = ["airport1"]
    mock_fetch_flights.return_value = ["flight1"]
    mock_build_graph.return_value = _tiny_graph()

    result = runner.invoke(
        app,
        [
            "gen",
            "--fetcher",
            "flights",
            "--year",
            "2024",
            "--month",
            "1",
            "--export",
            str(tmp_path / "flights.graphml"),
        ],
    )

    assert result.exit_code == 0
    mock_fetch_airlines.assert_called_once()
    mock_fetch_airports.assert_called_once()
    mock_fetch_flights.assert_called_once()
    mock_build_graph.assert_called_once()
    assert (tmp_path / "flights.graphml").exists()


def test_invalid_month():
    result = runner.invoke(
        app, ["gen", "--fetcher", "flights", "--year", "2024", "--month", "13"]
    )

    assert result.exit_code != 0


def test_invalid_year():
    result = runner.invoke(
        app, ["gen", "--fetcher", "flights", "--year", "2200", "--month", "1"]
    )
    assert result.exit_code != 0


def test_invalid_daterange():
    result = runner.invoke(
        app, ["gen", "--fetcher", "flights", "--date-range", "2024-01-,2024-01-10"]
    )
    assert result.exit_code != 0


@patch("graphfaker.fetchers.flights.FlightGraphFetcher.fetch_flights")
def test_flight_mode_with_date_range(mock_fetch_flights, tmp_path):
    mock_fetch_flights.return_value = ["flightX"]
    with patch(
        "graphfaker.fetchers.flights.FlightGraphFetcher.fetch_airlines", return_value=[]
    ), patch(
        "graphfaker.fetchers.flights.FlightGraphFetcher.fetch_airports", return_value=[]
    ), patch(
        "graphfaker.fetchers.flights.FlightGraphFetcher.build_graph",
        return_value=_tiny_graph(),
    ):
        result = runner.invoke(
            app,
            [
                "gen",
                "--fetcher",
                "flights",
                "--year",
                "2024",
                "--month",
                "1",
                "--date-range",
                "2024-01-01,2024-01-10",
                "--export",
                str(tmp_path / "range.graphml"),
            ],
        )
        assert result.exit_code == 0
        mock_fetch_flights.assert_called_once()
        assert (tmp_path / "range.graphml").exists()


def test_version_and_info():
    from graphfaker import __version__

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and result.output.strip() == f"graphfaker {__version__}"
    assert runner.invoke(app, ["-V"]).output == result.output

    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert result.output.startswith(f"graphfaker {__version__} (engine ")
    for extra in ("neo4j", "duckdb", "ladybug", "pyg", "osm"):
        assert f"] {extra}" in result.output  # present or not, every extra is listed


def test_validate_reports_every_file_and_fails_on_any(tmp_path):
    good = tmp_path / "social.yaml"
    assert runner.invoke(app, ["schema", "social", "--total-nodes", "40", "--out", str(good)]).exit_code == 0
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nnodes: []\n", encoding="utf-8")

    result = runner.invoke(app, ["validate", str(good)])
    assert result.exit_code == 0 and "ok, schema 'social'" in result.output and "40 nodes" in result.output

    result = runner.invoke(app, ["validate", str(good), str(bad), str(tmp_path / "missing.yaml")])
    assert result.exit_code == 1
    assert "ok, schema 'social'" in result.output
    assert "not a valid schema" in result.output and "not found" in result.output


def test_inspect_and_json_outputs(tmp_path):
    out = tmp_path / "bank"
    result = runner.invoke(app, ["fraud", "--scale", "0.001", "--seed", "3", "--out", str(out), "--quiet", "--json"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["manifest"]["seed"] == 3 and doc["manifest"]["node_counts"]["Account"] == 10000
    assert 0 <= doc["hardness"]["max_auc"] <= 1 and "recurring_share" in doc["realism"]

    result = runner.invoke(app, ["inspect", str(out)])
    assert result.exit_code == 0
    assert "schema 'fraud'" in result.output and "seed 3" in result.output and "truth  accounts, patterns" in result.output
    result = runner.invoke(app, ["inspect", str(out), "--json"])
    assert json.loads(result.output)["truth"] == ["accounts", "patterns", "region", "transactions"]

    result = runner.invoke(app, ["inspect", str(tmp_path)])
    assert result.exit_code != 0 and "manifest.json" in result.output

    result = runner.invoke(app, ["evaluate", str(out), "--json"])
    assert result.exit_code == 0 and json.loads(result.output)["account"]["fn"] > 0


def test_the_fraud_report_says_what_the_dataset_contains(tmp_path):
    out = tmp_path / "bank"
    result = runner.invoke(app, ["fraud", "--scale", "0.001", "--seed", "3", "--out", str(out), "--quiet"])
    assert result.exit_code == 0, result.output
    assert "hardness report" in result.output and "realism:" in result.output
    assert "coverage report:" in result.output
    # The three things the coverage report is for: shapes, their decoys, and
    # where in the ordinary process the patterns landed.
    assert "fan_in" in result.output and "decoy_fan_in" in result.output
    assert "roles filled" in result.output

    result = runner.invoke(app, ["fraud", "--scale", "0.001", "--seed", "3", "--out", str(out), "--quiet", "--json"])
    coverage = json.loads(result.output)["coverage"]
    assert coverage["shapes_in_catalogue"] == 14
    assert {row["shape"] for row in coverage["twins"]} <= {row["shape"] for row in coverage["shapes"]}


def test_coverage_is_available_to_every_domain_that_injects_anything(tmp_path):
    """The report is domain-agnostic, so a pack gets it without a command.

    ``graphfaker fraud`` is a shortcut with its own reports; everything else
    goes through ``generate``, and the flag is there rather than in three
    places.
    """
    out = tmp_path / "chain"
    result = runner.invoke(
        app,
        ["generate", "supply_chain", "--scale", "0.004", "--seed", "3", "--out", str(out), "--quiet", "--coverage"],
    )
    assert result.exit_code == 0, result.output
    assert "coverage report:" in result.output
    assert "phantom_supplier" in result.output and "disruption_cascade" in result.output

    # A domain with no patterns says so instead of failing.
    plain = tmp_path / "social"
    result = runner.invoke(
        app,
        ["generate", "social", "--total-nodes", "200", "--total-edges", "400",
         "--seed", "1", "--out", str(plain), "--quiet", "--coverage"],
    )
    assert result.exit_code == 0, result.output
    assert "no injected patterns" in result.output


def test_validate_verbose_reads_the_schema_back(tmp_path):
    """What somebody holding a drafted schema needs: the ratios.

    The header line already says how big the thing is. The breakdown says
    what it is, and the number that matters most is an edge family's budget
    divided by its source rows: a schema states a share of a total and
    nothing in the file says whether that works out to one per row.
    """
    path = tmp_path / "social.yaml"
    result = runner.invoke(app, ["schema", "social", "--total-nodes", "400", "--out", str(path)])
    assert result.exit_code == 0, result.output

    quiet = runner.invoke(app, ["validate", str(path)])
    assert quiet.exit_code == 0, quiet.output
    assert "node types" in quiet.output
    assert "per Person" not in quiet.output, "the breakdown is opt-in"

    loud = runner.invoke(app, ["validate", str(path), "--verbose"])
    assert loud.exit_code == 0, loud.output
    assert "latent  community" in loud.output
    assert "per Person" in loud.output
    assert "faker(name)" in loud.output, "samplers and their parameters, not just attribute names"
    assert "* functional" in loud.output
    # -v is the same flag.
    assert runner.invoke(app, ["validate", str(path), "-v"]).output == loud.output


def test_validate_verbose_still_fails_on_a_bad_schema(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: oops\nnodes: []\n", encoding="utf-8")
    result = runner.invoke(app, ["validate", str(bad), "--verbose"])
    assert result.exit_code == 1


def test_generate_refuses_to_merge_two_datasets_in_one_directory(tmp_path):
    social = tmp_path / "social.yaml"
    coordination = tmp_path / "coordination.yaml"
    assert runner.invoke(app, ["schema", "social", "--total-nodes", "200", "--out", str(social)]).exit_code == 0
    assert runner.invoke(app, ["schema", "coordination", "--out", str(coordination)]).exit_code == 0

    out = tmp_path / "out"
    base = ["generate", "--seed", "1", "--out", str(out), "--quiet", "--schema"]
    assert runner.invoke(app, [*base, str(social)]).exit_code == 0
    first = sorted(p.stem for p in (out / "nodes").glob("*.parquet"))
    assert "Person" in first

    # The same schema again is the ordinary loop and is not a conflict.
    assert runner.invoke(app, [*base, str(social)]).exit_code == 0

    refused = runner.invoke(app, [*base, str(coordination)])
    assert refused.exit_code == 1
    assert "union of both" in refused.output and "--force" in refused.output
    assert sorted(p.stem for p in (out / "nodes").glob("*.parquet")) == first, "nothing was written"

    forced = runner.invoke(app, [*base, str(coordination), "--force"])
    assert forced.exit_code == 0, forced.output
    assert sorted(p.stem for p in (out / "nodes").glob("*.parquet")) == ["Account", "Device", "Topic"]


def test_schema_draft_will_not_write_over_an_existing_file(tmp_path, monkeypatch):
    """Checked before the model is asked, so a mistaken rerun costs nothing.

    A drafted schema is meant to be read and edited, and a second draft of
    the same sentence is a different schema, so replacing one silently
    throws away the editing that is the whole point of the file.
    """
    from graphfaker.llm import RecordedProvider

    out = tmp_path / "hospital.yaml"
    out.write_text("name: edited_by_hand\nnodes: []\n", encoding="utf-8")
    # No replies at all: if the model is reached, the provider raises.
    monkeypatch.setattr("graphfaker.llm.get_provider", lambda *a, **k: RecordedProvider(replies=[]))

    refused = runner.invoke(app, ["schema", "draft", "a hospital", "--out", str(out)])
    assert refused.exit_code != 0
    assert "already exists" in refused.output and "--force" in refused.output
    assert "asking" not in refused.output, "the model was asked before the file was checked"
    assert out.read_text(encoding="utf-8").startswith("name: edited_by_hand")

    good = "name: clinic\nnodes:\n- {name: A, count: 2}\ntopology: {kind: uniform}\n"
    monkeypatch.setattr("graphfaker.llm.get_provider", lambda *a, **k: RecordedProvider(replies=[good]))
    forced = runner.invoke(app, ["schema", "draft", "a hospital", "--out", str(out), "--force"])
    assert forced.exit_code == 0, forced.output
    assert "name: clinic" in out.read_text(encoding="utf-8")


def test_a_redraft_says_when_a_dataset_came_from_the_previous_one(tmp_path, monkeypatch):
    """`hospital.yaml` next to `./hospital` is the pairing people make."""
    from graphfaker.llm import RecordedProvider

    schema = tmp_path / "hospital.yaml"
    good = "name: hospital\nnodes:\n- {name: A, count: 2}\ntopology: {kind: uniform}\n"
    monkeypatch.setattr("graphfaker.llm.get_provider", lambda *a, **k: RecordedProvider(replies=[good]))
    assert runner.invoke(app, ["schema", "draft", "a hospital", "--out", str(schema)]).exit_code == 0

    data = tmp_path / "hospital"
    assert runner.invoke(
        app, ["generate", "--schema", str(schema), "--seed", "1", "--out", str(data), "--quiet"]
    ).exit_code == 0

    monkeypatch.setattr("graphfaker.llm.get_provider", lambda *a, **k: RecordedProvider(replies=[good]))
    again = runner.invoke(app, ["schema", "draft", "a hospital", "--out", str(schema), "--force"])
    assert again.exit_code == 0, again.output
    assert "holds a dataset generated from the previous draft" in again.output
    # Said, not done: the dataset is untouched.
    assert (data / "manifest.json").is_file()
