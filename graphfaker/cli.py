"""
Command-line interface for GraphFaker.
"""

import json
import os

import typer
from pydantic import ValidationError

from graphfaker.core import GraphFaker
from graphfaker.enums import ExportFormat, FetcherType
from graphfaker.export import export_csv, export_cypher, export_neo4j_csv
from graphfaker.fetchers.flights import FlightGraphFetcher
from graphfaker.fetchers.osm import OSMGraphFetcher
from graphfaker.logger import logger
from graphfaker.utils import parse_date_range

app = typer.Typer(no_args_is_help=True, help="GraphFaker: synthetic and real-world graph datasets.")


@app.command(short_help="Generate a social graph, or load an OSM / flight network.")
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
    seed: int = typer.Option(
        None, help="Seed for reproducible synthetic generation (faker fetcher)."
    ),
    export: str = typer.Option(
        "graph.graphml",
        help="Output path. For csv/neo4j-csv this is used as a directory or stem.",
    ),
    export_format: ExportFormat = typer.Option(
        ExportFormat.GRAPHML,
        "--format",
        help="Output format: graphml | csv | neo4j-csv | cypher | opencypher | gql.",
    ),
):
    """Generate a graph using GraphFaker."""
    gf = GraphFaker(seed=seed)

    if fetcher == FetcherType.FAKER:

        g = gf.generate_graph(
            source=FetcherType.FAKER.value,
            total_nodes=total_nodes,
            total_edges=total_edges,
        )
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

    if export_format == ExportFormat.GRAPHML:
        gf.export_graph(g, source=fetcher, path=abs_export_path)
    elif export_format == ExportFormat.CSV:
        stem, _ = os.path.splitext(abs_export_path)
        export_csv(g, f"{stem}_nodes.csv", f"{stem}_edges.csv")
    elif export_format == ExportFormat.NEO4J_CSV:
        stem, _ = os.path.splitext(abs_export_path)
        export_neo4j_csv(g, stem)
    else:
        dialect = "neo4j" if export_format == ExportFormat.CYPHER else export_format.value
        export_cypher(g, abs_export_path, dialect=dialect)

    logger.info(
        f"exported graph as {export_format.value} to {abs_export_path}, "
        f"with {g.number_of_nodes()} nodes and {g.number_of_edges()} edges."
    )


def _write_sink(run, out: str, sink: str) -> None:
    """Write the run as Parquet, then whatever extra layout ``sink`` asks for."""
    root = run.write(out)
    logger.info("wrote %s (%s)", root, ", ".join(f"{k}={v}" for k, v in run.manifest.node_counts.items()))
    if sink == "parquet":
        return
    if sink == "neo4j-admin":
        from graphfaker.sinks import write_neo4j_admin

        write_neo4j_admin(run.tables, os.path.join(out, "neo4j"))
    elif sink == "ladybug":
        from graphfaker.sinks import write_ladybug

        try:
            write_ladybug(run.tables, out, db_path=os.path.join(out, "graph.lbdb"))
        except ImportError as exc:
            typer.echo(f"wrote {out}/load.cypher; database not created: {exc}", err=True)
    elif sink == "gen-fraud-graph":
        from graphfaker.sinks import write_gen_fraud_graph

        write_gen_fraud_graph(run, os.path.join(out, "gen_fraud_graph"))
    else:
        raise typer.BadParameter(f"unknown sink {sink!r}")


def _parse_options(args: list[str]) -> dict[str, object]:
    """``--total-nodes 500 --topology uniform`` -> ``{"total_nodes": "500", ...}``.

    Values are strings, which the domain's options model converts; a value
    that looks like JSON (``--patterns '{"cycle": 5}'``) is decoded first.
    """
    options: dict[str, object] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if not token.startswith("--"):
            raise typer.BadParameter(f"unexpected argument {token!r}; domain options look like --name value")
        key = token[2:].replace("-", "_")
        if "=" in key:
            key, value = key.split("=", 1)
        elif i + 1 < len(args) and not args[i + 1].startswith("--"):
            value = args[i + 1]
            i += 1
        else:
            value = "true"
        if value[:1] in "{[":
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise typer.BadParameter(f"--{token[2:]}: not valid JSON ({exc.msg})") from exc
        options[key] = value
        i += 1
    return options


@app.command(short_help="List the domains that can be generated.")
def domains():
    from graphfaker.domains import available

    for name, domain in sorted(available().items()):
        typer.echo(f"{name}: {domain.summary}")
        for option, kind, default in domain.describe_options():
            typer.echo(f"    --{option.replace('_', '-')} <{kind}>  default {default!r}")


@app.command(
    short_help="Generate any domain by name; domain options are passed as --name value.",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def generate(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="A domain name from `graphfaker domains`."),
    out: str = typer.Option("graphfaker_out", help="Output directory."),
    seed: int = typer.Option(None, help="Seed for a reproducible dataset."),
    workers: int = typer.Option(1, help="Processes for node sampling. Does not change the result."),
    sink: str = typer.Option("parquet", help="parquet | neo4j-admin | ladybug | gen-fraud-graph."),
):
    """Example: graphfaker generate fraud --scale 0.01 --hardness high --seed 1 --out ./bank"""
    from graphfaker.domains import get

    try:
        pack = get(domain)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    try:
        run = pack.run(seed=seed, workers=workers, **_parse_options(list(ctx.args)))
    except ValidationError as exc:
        problems = "; ".join(f"--{'.'.join(str(p) for p in e['loc']).replace('_', '-')}: {e['msg']}" for e in exc.errors())
        raise typer.BadParameter(f"{problems}. Run `graphfaker domains` to see the options.") from exc
    _write_sink(run, out, sink)


@app.command(short_help="Generate a fraud / AML transaction graph with labelled typologies.")
def fraud(
    scale: float = typer.Option(0.001, help="1.0 = ~10M accounts / ~90M transactions (gen-fraud-graph convention)."),
    hardness: str = typer.Option("medium", help="low | medium | high: how hard the fraud is to find."),
    seed: int = typer.Option(None, help="Seed for a reproducible dataset."),
    out: str = typer.Option("fraud_data", help="Output directory."),
    sink: str = typer.Option(
        "parquet",
        help="parquet | neo4j-admin | ladybug | gen-fraud-graph. Parquet (nodes/, edges/, truth/, manifest) is always written.",
    ),
    period_days: int = typer.Option(90, help="Length of the transaction period in days."),
    workers: int = typer.Option(1, help="Processes for entity sampling. Does not change the result."),
    report: bool = typer.Option(True, help="Print the hardness and realism reports."),
):
    """Shortcut for `graphfaker generate fraud ...` that also prints the reports."""
    from graphfaker.domains import get
    from graphfaker.domains.fraud.hardness import hardness_report, realism_report

    run = get("fraud").run(seed=seed, workers=workers, scale=scale, hardness=hardness, period_days=period_days)
    _write_sink(run, out, sink)
    if report:
        typer.echo(hardness_report(run).summary())
        typer.echo("")
        typer.echo("realism: " + ", ".join(f"{k}={v:.3f}" for k, v in realism_report(run).items()))


@app.command(short_help="Score flagged accounts / transactions against a fraud run's truth.")
def evaluate(
    data: str = typer.Argument(..., help="Directory written by `graphfaker fraud`."),
    accounts: str = typer.Option(None, help="File with one flagged account id per line."),
    transactions: str = typer.Option(None, help="File with one flagged transaction id per line."),
    ring_threshold: float = typer.Option(1.0, help="Fraction of a pattern's accounts that must be flagged to count it as found."),
):
    from graphfaker.domains.fraud.evaluate import evaluate as _evaluate

    def read(path):
        if not path:
            return []
        with open(path, encoding="utf-8") as fh:
            return [line.strip() for line in fh if line.strip()]

    result = _evaluate(data, read(accounts), read(transactions), ring_threshold=ring_threshold)
    typer.echo(result.summary())


if __name__ == "__main__":
    app()
