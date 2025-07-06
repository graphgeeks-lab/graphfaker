from typer.testing import CliRunner
from unittest.mock import patch
from graphfaker.cli import app

runner = CliRunner()


def test_faker_mode_generates_graph():
    result = runner.invoke(
        app,
        ["--fetcher", "faker", "--total-nodes", "10", "--total-edges", "20"],
    )

    assert result.exit_code == 0
    assert "Graph" in result.output or "nodes" in result.output


@patch("graphfaker.fetchers.osm.OSMGraphFetcher.fetch_network")
def test_osm_mode_with_place(mock_fetch):
    mock_fetch.return_value = "Mocked OSM Graph"

    result = runner.invoke(
        app,
        ["--fetcher", "osm", "--place", "Soho Square, London, UK"],
    )
    assert result.exit_code == 0
    assert "Mocked OSM Graph" in result.output
    mock_fetch.assert_called_once()


@patch("graphfaker.fetchers.flights.FlightGraphFetcher.fetch_airlines")
@patch("graphfaker.fetchers.flights.FlightGraphFetcher.fetch_airports")
@patch("graphfaker.fetchers.flights.FlightGraphFetcher.fetch_flights")
@patch("graphfaker.fetchers.flights.FlightGraphFetcher.build_graph")
def test_flight_mode_valid_inputs(
    mock_build_graph, mock_fetch_flights, mock_fetch_airports, mock_fetch_airlines
):
    mock_fetch_airlines.return_value = ["airline1"]
    mock_fetch_airports.return_value = ["airport1"]
    mock_fetch_flights.return_value = ["flight1"]
    mock_build_graph.return_value = "Mocked Flight Graph"

    result = runner.invoke(
        app, ["--fetcher", "flights", "--year", "2024", "--month", "1"]
    )

    assert result.exit_code == 0
    assert "Mocked Flight Graph" in result.output
    mock_fetch_airlines.assert_called_once()
    mock_fetch_airports.assert_called_once()
    mock_fetch_flights.assert_called_once()
    mock_build_graph.assert_called_once()


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
def test_flight_mode_with_date_range(mock_fetch_flights):
    mock_fetch_flights.return_value = ["flightX"]
    with patch(
        "graphfaker.fetchers.flights.FlightGraphFetcher.fetch_airlines", return_value=[]
    ), patch(
        "graphfaker.fetchers.flights.FlightGraphFetcher.fetch_airports", return_value=[]
    ), patch(
        "graphfaker.fetchers.flights.FlightGraphFetcher.build_graph",
        return_value="Mocked Graph",
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
            ],
        )
        assert result.exit_code == 0
        assert "Mocked Graph" in result.output
