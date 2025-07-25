"""
Command-line interface for GraphFaker.
"""

from venv import logger
import typer
from graphfaker.core import GraphFaker
from graphfaker.enums import FetcherType
from graphfaker.fetchers.osm import OSMGraphFetcher
from graphfaker.fetchers.flights import FlightGraphFetcher
from graphfaker.utils import parse_date_range
import os

app = typer.Typer()


@app.command(short_help="Generate a graph using GraphFaker.")
def gen(
    fetcher: FetcherType = typer.Option(FetcherType.FAKER, help="Fetcher type to use."),
    # for FetcherType.FAKER source
    total_nodes: int = typer.Option(100, help="Total nodes for random mode."),
    total_edges: int = typer.Option(1000, help="Total edges for random mode."),
    # for FetcherType.OSM source
    place: str = typer.Option(
        None, help="OSM place name (e.g., 'Soho Square, London, UK')."
    ),
    address: str = typer.Option(
        None, help="OSM address (e.g., '1600 Amphitheatre Parkway, Mountain View, CA.')"
    ),
    bbox: str = typer.Option(None, help="OSM bounding box as 'north,south,east,west.'"),
    network_type: str = typer.Option(
        "drive", help="OSM network type: drive | walk | bike | all."
    ),
    simplify: bool = typer.Option(True, help="Simplify OSM graph topology."),
    retain_all: bool = typer.Option(False, help="Retain all components in OSM graph."),
    dist: int = typer.Option(
        1000, help="Search radius (meters) when fetching around address."
    ),
    # for FetcherType.FLIGHT source
    country: str = typer.Option(
        "United States",
        help="Filter airports by country for flight data. e.g 'United States'.",
    ),
    year: int = typer.Option(
        2024, help="Year (YYYY) for single-month flight fetch. e.g. 2024."
    ),
    month: int = typer.Option(
        1, help="Month (1-12) for single-month flight fetch. e.g. 1 for January."
    ),
    date_range: str = typer.Option(
        None,
        help="Year, Month and day range (YYYY-MM-DD,YYYY-MM-DD) for flight data. e.g. '2024-01-01,2024-01-15'.",
    ),

    # common
    export: str = typer.Option("graph.graphml", help="File path to export GraphML"),
):
    """Generate a graph using GraphFaker."""
    gf = GraphFaker()

    if fetcher == FetcherType.FAKER:

        g = gf.generate_graph(total_nodes=total_nodes, total_edges=total_edges)
        logger.info(
            f"Generated random graph with {g.number_of_nodes()} nodes and {g.number_of_edges()} edges."
        )

    elif fetcher == FetcherType.OSM:
        # parse bbox string if provided
        bbox_tuple = None
        if bbox:
            north, south, east, west = map(float, bbox.split(","))
            bbox_tuple = (north, south, east, west)
        g = OSMGraphFetcher.fetch_network(
            place=place,
            address=address,
            bbox=bbox_tuple,
            network_type=network_type,
            simplify=simplify,
            retain_all=retain_all,
            dist=dist,
        )
        logger.info(
            f"Fetched OSM graph with {g.number_of_nodes()} nodes and {g.number_of_edges()} edges."
        )
    else:
        # Flight fetcher
        parsed_date_range = parse_date_range(date_range) if date_range else None

        # validate year and month
        if not (1 <= month <= 12):
            raise ValueError("Month must be between 1 and 12.")
        if not (1900 <= year <= 2100):
            raise ValueError("Year must be between 1900 and 2100.")

        airlines_df = FlightGraphFetcher.fetch_airlines()

        airports_df = FlightGraphFetcher.fetch_airports(country=country)

        flights_df = FlightGraphFetcher.fetch_flights(
            year=year, month=month, date_range=parsed_date_range
        )
        logger.info(
            f"Fetched {len(airlines_df)} airlines, "
            f"{len(airports_df)} airports, "
            f"{len(flights_df)} flights."
        )

        g = FlightGraphFetcher.build_graph(airlines_df, airports_df, flights_df)
        
        logger.info(
            f"Generated flight graph with {g.number_of_nodes()} nodes and {g.number_of_edges()} edges."
        )
    
    abs_export_path = os.path.abspath(export)
    os.makedirs(os.path.dirname(abs_export_path) or ".", exist_ok=True)
    
    gf.export_graph(g, source=fetcher, path=abs_export_path)
    logger.info(f"exported graph to {abs_export_path}, with {g.number_of_nodes()} nodes and {g.number_of_edges()} edges.")


if __name__ == "__main__":
    app()
