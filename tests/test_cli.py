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
        app, ["--fetcher", "flights", "--year", "2024", "--month", "13"]
    )

    assert result.exit_code != 0


def test_invalid_year():
    result = runner.invoke(
        app, ["--fetcher", "flights", "--year", "2200", "--month", "1"]
    )
    assert result.exit_code != 0


def test_invalid_daterange():
    result = runner.invoke(
        app, ["--fetcher", "flights", "--date-range", "2024-01-,2024-01-10"]
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
