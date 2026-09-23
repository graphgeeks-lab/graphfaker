"""
Command-line interface for GraphFaker.
"""

import json
import os
from pathlib import Path

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


def _version_callback(value: bool) -> None:
    if value:
        from graphfaker import __version__

        typer.echo(f"graphfaker {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False, "--version", "-V", help="Print the version and exit.", callback=_version_callback, is_eager=True
    ),
) -> None:
    """GraphFaker: synthetic and real-world graph datasets."""


#: Optional extras and the module that proves each one is installed.
_EXTRAS = {
    "neo4j": ("neo4j", "graphfaker load neo4j, verify neo4j"),
    "duckdb": ("duckdb", "graphfaker load duckdb, verify duckdb, --sink duckdb"),
    "ladybug": ("ladybug", "graphfaker load ladybug, verify ladybug, --sink ladybug"),
    "pyg": ("torch_geometric", "--sink pyg"),
    "osm": ("osmnx", "graphfaker gen --fetcher osm"),
}


@app.command(short_help="Versions of GraphFaker and its dependencies, and which extras are installed.")
def info():
    """What this installation can do: the versions that decide whether two
    machines produce the same bytes, and the optional extras that are present."""
    import platform
    from importlib.metadata import PackageNotFoundError, version

    from graphfaker import __version__
    from graphfaker.engine.run import ENGINE_VERSION

    typer.echo(f"graphfaker {__version__} (engine {ENGINE_VERSION})")
    typer.echo(f"python {platform.python_version()} on {platform.system().lower()} {platform.machine()}")
    for dist in ("polars", "numpy", "pyarrow", "faker", "networkx"):
        try:
            typer.echo(f"  {dist} {version(dist)}")
        except PackageNotFoundError:
            typer.echo(f"  {dist} missing")
    typer.echo("extras:")
    for extra, (module, enables) in _EXTRAS.items():
        try:
            installed = version(module)
        except PackageNotFoundError:
            typer.echo(f"  [ ] {extra}: pip install \"graphfaker[{extra}]\" for {enables}")
        else:
            typer.echo(f"  [x] {extra} ({module} {installed}): {enables}")


@app.command(short_help="Load a real-world network, or a quick social graph, and export one file.")
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
    """Load an OpenStreetMap or flight network, or build a quick social graph, as
    NetworkX, and export it as a single file.

    This is the NetworkX-facing entry point. For datasets with ground truth and a
    manifest, written as tables and loadable into a database, use `graphfaker
    generate <domain>` or `graphfaker fraud`.
    """
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
        try:
            g = OSMGraphFetcher.fetch_network(
                place=place,
                address=address,
                bbox=bbox_tuple,
                network_type=network_type,
                simplify=simplify,
                retain_all=retain_all,
                dist=dist,
            )
        except ImportError as exc:
            raise typer.BadParameter(str(exc)) from exc
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


def _quiet(quiet: bool) -> None:
    """``--quiet``: progress logging off, warnings and errors still shown."""
    if quiet:
        import logging

        logger.setLevel(logging.WARNING)


def _dump(data: dict) -> None:
    """``--json``: one document on stdout. Logging is already on stderr, so a
    pipeline can read stdout and still see progress."""
    typer.echo(json.dumps(data, indent=2, default=str))


def _write_sink(run, out: str, sink: str, blind: bool = False) -> None:
    """Write the run as Parquet, then whatever extra layout ``sink`` asks for.

    ``blind`` keeps the ground truth out of a database sink (it is still
    written to ``truth/`` on disk), so the database can be handed to a
    detector as an unbiased benchmark."""
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
            write_ladybug(run.tables, out, db_path=os.path.join(out, "graph.lbdb"), truth=None if blind else run.truth)
        except ImportError as exc:
            typer.echo(f"wrote {out}/load.cypher; database not created: {exc}", err=True)
    elif sink == "duckdb":
        from graphfaker.sinks.duckdb import write_duckdb

        try:
            write_duckdb(run.tables, out, db_path=os.path.join(out, "graph.duckdb"), truth=None if blind else run.truth, graph=run.schema.name)
        except ImportError as exc:
            typer.echo(f"wrote {out}/load.sql; database not created: {exc}", err=True)
    elif sink == "pyg":
        from graphfaker.sinks.pyg import write_pyg

        try:
            write_pyg(run.tables, os.path.join(out, "graph.pt"), None if blind else run.truth, seed=run.manifest.seed or 0)
        except ImportError as exc:
            typer.echo(f"graph.pt not written: {exc}", err=True)
    elif sink == "gen-fraud-graph":
        from graphfaker.sinks import write_gen_fraud_graph

        write_gen_fraud_graph(run, os.path.join(out, "gen_fraud_graph"))
    elif sink == "neo4j":
        from graphfaker.sinks.neo4j_live import Target, load_tables

        target = Target()
        typer.echo(f"loading into {target.uri} database {target.database!r} ...")
        typer.echo(load_tables(run.tables, target, None if blind else run.truth, wipe_first=True).summary())
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


def _load_schema(path: str):
    """A ``GraphSchema`` from a YAML file, with the file's problems reported
    as the CLI's problems."""
    import yaml

    from graphfaker.schema import GraphSchema

    if not os.path.isfile(path):
        raise typer.BadParameter(f"schema file not found: {path}")
    try:
        return GraphSchema.from_yaml(Path(path))
    except yaml.YAMLError as exc:
        raise typer.BadParameter(f"{path} is not valid YAML: {exc}") from exc
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'schema'}: {e['msg']}" for e in exc.errors())
        raise typer.BadParameter(f"{path} is not a valid schema: {problems}") from exc


@app.command(
    short_help="Generate a domain by name, or any graph from a schema file.",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def generate(
    ctx: typer.Context,
    domain: str = typer.Argument(None, help="A domain name from `graphfaker domains`. Omit it when generating from --schema."),
    schema: str = typer.Option(None, "--schema", help="A schema YAML file to generate from (see `graphfaker schema`)."),
    out: str = typer.Option("graphfaker_out", help="Output directory."),
    seed: int = typer.Option(None, help="Seed for a reproducible dataset."),
    shard_size: int = typer.Option(None, "--shard-size", help="Rows per shard for --schema runs. Part of reproducibility; recorded in the manifest. Default 10000."),
    workers: int = typer.Option(1, help="Processes for node sampling. Does not change the result."),
    sink: str = typer.Option("parquet", help="parquet | neo4j | neo4j-admin | ladybug | duckdb | pyg | gen-fraud-graph."),
    blind: bool = typer.Option(False, "--blind", help="Keep the ground truth out of the database sink (it is still written to truth/ on disk)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="No progress logging; warnings and errors only."),
    as_json: bool = typer.Option(False, "--json", help="Print the manifest as JSON on stdout when done."),
):
    """Examples:

      graphfaker generate fraud --scale 0.01 --hardness high --seed 1 --out ./bank

      graphfaker generate --schema my_graph.yaml --seed 1 --out ./my_graph
    """
    _quiet(quiet)
    extra = list(ctx.args)
    if schema is not None:
        if domain is not None:
            raise typer.BadParameter("give either a domain name or --schema, not both")
        if extra:
            raise typer.BadParameter(f"a schema file takes no domain options ({' '.join(extra)}); edit the file instead")
        from graphfaker.engine.run import DEFAULT_SHARD_SIZE
        from graphfaker.engine.run import generate as _generate

        loaded = _load_schema(schema)
        run = _generate(loaded, seed=seed, shard_size=shard_size or DEFAULT_SHARD_SIZE, workers=workers)
        _write_sink(run, out, sink, blind)
        if as_json:
            _dump(run.manifest.model_dump())
        return
    if domain is None:
        raise typer.BadParameter("give a domain name (see `graphfaker domains`) or --schema FILE")
    if shard_size is not None:
        raise typer.BadParameter("--shard-size applies to --schema runs; a domain sets its own")
    from graphfaker.domains import get

    try:
        pack = get(domain)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    try:
        run = pack.run(seed=seed, workers=workers, **_parse_options(extra))
    except ValidationError as exc:
        problems = "; ".join(f"--{'.'.join(str(p) for p in e['loc']).replace('_', '-')}: {e['msg']}" for e in exc.errors())
        raise typer.BadParameter(f"{problems}. Run `graphfaker domains` to see the options.") from exc
    _write_sink(run, out, sink, blind)
    if as_json:
        _dump(run.manifest.model_dump())


@app.command(short_help="Check schema YAML files without generating anything.")
def validate(
    files: list[str] = typer.Argument(..., help="Schema YAML files, as written by `graphfaker schema` or by hand."),
):
    """Parse and validate each file with the same checks `generate --schema`
    applies, and say what it describes. Exits non-zero if any file fails.

      graphfaker validate my_graph.yaml other.yaml
    """
    failed = False
    for path in files:
        try:
            loaded = _load_schema(path)
        except typer.BadParameter as exc:
            typer.echo(exc.message, err=True)
            failed = True
            continue
        typer.echo(
            f"{path}: ok, schema {loaded.name!r}, {len(loaded.nodes)} node types, {len(loaded.relationships())} relationship types, "
            f"{loaded.total_nodes:,} nodes and {loaded.total_edges:,} edges, digest {loaded.digest()}"
        )
    if failed:
        raise typer.Exit(code=1)


@app.command(short_help="What a dataset directory contains: manifest, counts, truth, versions.")
def inspect(
    data: str = typer.Argument(..., help="Directory written by `graphfaker generate` or `graphfaker fraud`."),
    as_json: bool = typer.Option(False, "--json", help="Print it as JSON instead of text."),
):
    """Read `manifest.json` and the file layout, without loading any table.

      graphfaker inspect ./bank
    """
    from graphfaker.engine.run import Manifest

    root = Path(data)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise typer.BadParameter(f"no manifest.json in {data}; is it a dataset directory?")
    manifest = Manifest.read(manifest_path)
    truth = sorted(p.stem for p in (root / "truth").glob("*.parquet")) if (root / "truth").is_dir() else []
    files = {
        "nodes": sorted(p.name for p in (root / "nodes").glob("*.parquet")),
        "edges": sorted(p.name for p in (root / "edges").glob("*.parquet")),
    }
    size = sum(p.stat().st_size for p in root.rglob("*.parquet"))
    extras = sorted(p.name for p in root.iterdir() if p.name not in {"nodes", "edges", "truth", "schema.yaml", "manifest.json"})
    if as_json:
        _dump({**manifest.model_dump(), "truth": truth, "files": files, "parquet_bytes": size, "extras": extras})
        return
    typer.echo(f"{root}: schema {manifest.schema_name!r} (digest {manifest.schema_digest}), seed {manifest.seed}, shard size {manifest.shard_size}")
    typer.echo(f"  written by graphfaker {manifest.graphfaker_version} (engine {manifest.engine_version}) at {manifest.created_at}")
    typer.echo(f"  nodes  {sum(manifest.node_counts.values()):>12,}  " + ", ".join(f"{k}={v:,}" for k, v in manifest.node_counts.items()))
    typer.echo(f"  edges  {sum(manifest.edge_counts.values()):>12,}  " + ", ".join(f"{k}={v:,}" for k, v in manifest.edge_counts.items()))
    typer.echo(f"  truth  {', '.join(truth) if truth else 'none'}")
    typer.echo(f"  parquet {size / 1e6:,.1f} MB" + (f"; also {', '.join(extras)}" if extras else ""))
    for name, config in manifest.extra.items():
        if isinstance(config, dict):
            typer.echo(f"  {name}: " + ", ".join(f"{k}={v}" for k, v in config.items()))
        else:
            typer.echo(f"  {name}: {config}")


@app.command(
    "schema",
    short_help="Print a domain's schema as YAML, to edit and generate from.",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def schema_command(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="A domain name from `graphfaker domains`."),
    out: str = typer.Option(None, "--out", help="Write to this file instead of standard output."),
):
    """Examples:

      graphfaker schema social --total-nodes 500 --out social.yaml

      graphfaker generate --schema social.yaml --seed 1 --out ./social
    """
    from graphfaker.domains import get

    try:
        pack = get(domain)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if pack.schema is None:
        raise typer.BadParameter(f"the {domain!r} domain is generated by code, not from a schema; it has nothing to print")
    options = _parse_options(list(ctx.args))
    try:
        if pack.options is not None:
            options = pack.options(**options).model_dump()
        loaded = pack.schema(**options)
    except ValidationError as exc:
        problems = "; ".join(f"--{'.'.join(str(p) for p in e['loc']).replace('_', '-')}: {e['msg']}" for e in exc.errors())
        raise typer.BadParameter(f"{problems}. Run `graphfaker domains` to see the options.") from exc
    header = f"# Schema of the {domain!r} domain, written by `graphfaker schema {domain}`.\n# Edit it, then: graphfaker generate --schema {os.path.basename(out) if out else 'this-file.yaml'} --seed 1 --out ./graph\n"
    if pack.schema_note:
        header += "".join(f"# {line}\n" for line in pack.schema_note.splitlines())
    text = header + loaded.to_yaml()
    if out is None:
        typer.echo(text, nl=False)
    else:
        Path(out).write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out}")


@app.command(short_help="Generate a fraud / AML transaction graph with labelled typologies.")
def fraud(
    scale: float = typer.Option(0.001, help="1.0 = ~10M accounts / ~90M transactions (gen-fraud-graph convention)."),
    hardness: str = typer.Option("medium", help="low | medium | high: how hard the fraud is to find."),
    seed: int = typer.Option(None, help="Seed for a reproducible dataset."),
    out: str = typer.Option("fraud_data", help="Output directory."),
    sink: str = typer.Option(
        "parquet",
        help="parquet | neo4j | neo4j-admin | ladybug | duckdb | pyg | gen-fraud-graph. Parquet (nodes/, edges/, truth/, manifest) is always written.",
    ),
    period_days: int = typer.Option(90, help="Length of the transaction period in days."),
    workers: int = typer.Option(1, help="Processes for entity sampling. Does not change the result."),
    report: bool = typer.Option(True, help="Print the hardness and realism reports."),
    blind: bool = typer.Option(False, "--blind", help="Keep the ground truth out of the database sink (it is still written to truth/ on disk)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="No progress logging; warnings and errors only."),
    as_json: bool = typer.Option(False, "--json", help="Print the manifest and the reports as one JSON document on stdout."),
):
    """Shortcut for `graphfaker generate fraud ...` that also prints the reports."""
    from graphfaker.domains import get
    from graphfaker.domains.fraud.hardness import hardness_report, realism_report

    _quiet(quiet)
    run = get("fraud").run(seed=seed, workers=workers, scale=scale, hardness=hardness, period_days=period_days)
    _write_sink(run, out, sink, blind)
    if as_json:
        _dump({
            "manifest": run.manifest.model_dump(),
            "hardness": hardness_report(run).as_dict() if report else None,
            "realism": realism_report(run) if report else None,
        })
    elif report:
        typer.echo(hardness_report(run).summary())
        typer.echo("")
        typer.echo("realism: " + ", ".join(f"{k}={v:.3f}" for k, v in realism_report(run).items()))


@app.command(short_help="Score flagged accounts / transactions against a fraud run's truth.")
def evaluate(
    data: str = typer.Argument(..., help="Directory written by `graphfaker fraud`."),
    accounts: str = typer.Option(None, help="File with one flagged account id per line."),
    transactions: str = typer.Option(None, help="File with one flagged transaction id per line."),
    ring_threshold: float = typer.Option(1.0, help="Fraction of a pattern's accounts that must be flagged to count it as found."),
    as_json: bool = typer.Option(False, "--json", help="Print the scores as JSON instead of text."),
):
    from graphfaker.domains.fraud.evaluate import evaluate as _evaluate

    def read(path):
        if not path:
            return []
        with open(path, encoding="utf-8") as fh:
            return [line.strip() for line in fh if line.strip()]

    result = _evaluate(data, read(accounts), read(transactions), ring_threshold=ring_threshold)
    if as_json:
        _dump(result.as_dict())
    else:
        typer.echo(result.summary())


load_app = typer.Typer(no_args_is_help=True, help="Load a generated dataset into a live database.")
verify_app = typer.Typer(no_args_is_help=True, help="Check a loaded database against the dataset on disk.")
app.add_typer(load_app, name="load")
app.add_typer(verify_app, name="verify")


def _target(uri: str, user: str, password: str, database: str):
    """Connection details, with unset flags falling back to NEO4J_* env vars."""
    from graphfaker.sinks.neo4j_live import Target

    given = {k: v for k, v in
             {"uri": uri, "user": user, "password": password, "database": database}.items()
             if v is not None}
    return Target(**given)


@load_app.command("neo4j", short_help="Load a dataset directory into a running Neo4j over Bolt.")
def load_neo4j(
    data: str = typer.Argument(..., help="Directory written by `graphfaker generate` or `graphfaker fraud`."),
    uri: str = typer.Option(None, help="Bolt URI. Default $NEO4J_URI or neo4j://127.0.0.1:7687."),
    user: str = typer.Option(None, help="User. Default $NEO4J_USER or neo4j."),
    password: str = typer.Option(None, help="Password. Default $NEO4J_PASSWORD.", envvar="NEO4J_PASSWORD"),
    database: str = typer.Option(None, help="Database name. Default $NEO4J_DATABASE or neo4j."),
    truth: bool = typer.Option(
        True,
        "--truth/--blind",
        help="--blind loads the graph only: no (:Pattern) nodes and no is_fraud, so the dataset stays usable as an unbiased benchmark.",
    ),
    wipe: bool = typer.Option(False, help="Delete everything in the target database first."),
    create: bool = typer.Option(False, help="CREATE DATABASE first (Neo4j Enterprise or Aura)."),
    batch_size: int = typer.Option(10_000, help="Rows per write transaction. Lower it if the server heap is small."),
    verify: bool = typer.Option(True, help="Run the checks in `graphfaker verify neo4j` after loading."),
):
    """Example: graphfaker load neo4j ./bank --database fraud --create --wipe"""
    from graphfaker.sinks.neo4j_live import load_directory
    from graphfaker.sinks.neo4j_verify import verify_directory

    target = _target(uri, user, password, database)
    typer.echo(f"loading {data} into {target.uri} database {target.database!r}" + ("" if truth else " (blind)"))
    try:
        report = load_directory(
            data, target, truth=truth, batch_size=batch_size, wipe_first=wipe, create=create
        )
    except (RuntimeError, ImportError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(report.summary())
    if verify:
        typer.echo("")
        result = verify_directory(data, target, truth=truth)
        typer.echo(result.summary())
        if not result.ok:
            raise typer.Exit(code=1)


@verify_app.command("neo4j", short_help="Compare a Neo4j database against the dataset it was loaded from.")
def verify_neo4j(
    data: str = typer.Argument(..., help="The dataset directory that was loaded. It is the oracle."),
    uri: str = typer.Option(None, help="Bolt URI. Default $NEO4J_URI or neo4j://127.0.0.1:7687."),
    user: str = typer.Option(None, help="User. Default $NEO4J_USER or neo4j."),
    password: str = typer.Option(None, help="Password. Default $NEO4J_PASSWORD.", envvar="NEO4J_PASSWORD"),
    database: str = typer.Option(None, help="Database name. Default $NEO4J_DATABASE or neo4j."),
    truth: bool = typer.Option(True, "--truth/--blind", help="--blind for a database loaded without the truth subgraph."),
    sample: int = typer.Option(25, help="Rows per label fetched back and compared property by property. 0 skips it."),
    verbose: bool = typer.Option(False, help="Print every check, not only the failures."),
):
    """Exits non-zero if the database does not match the dataset."""
    from graphfaker.sinks.neo4j_verify import verify_directory

    result = verify_directory(data, _target(uri, user, password, database), truth=truth, sample=sample)
    typer.echo(result.summary(verbose=verbose))
    if not result.ok:
        raise typer.Exit(code=1)


@load_app.command("ladybug", short_help="Load a dataset directory into a new embedded LadybugDB / Kùzu database.")
def load_ladybug(
    data: str = typer.Argument(..., help="Directory written by `graphfaker generate` or `graphfaker fraud`."),
    db: str = typer.Option(None, help="Database path to create. Default <data>/graph.lbdb."),
    truth: bool = typer.Option(
        True,
        "--truth/--blind",
        help="--blind loads the graph only: no Pattern nodes and no is_fraud, so the dataset stays usable as an unbiased benchmark.",
    ),
    wipe: bool = typer.Option(False, help="Replace the database if it already exists."),
    verify: bool = typer.Option(True, help="Run the checks in `graphfaker verify ladybug` after loading."),
):
    """Example: graphfaker load ladybug ./bank --db ./bank/graph.lbdb"""
    from graphfaker.sinks.ladybug import load_directory, verify_directory

    db_path = db or os.path.join(data, "graph.lbdb")
    typer.echo(f"loading {data} into {db_path}" + ("" if truth else " (blind)"))
    try:
        report = load_directory(data, db_path, truth=truth, wipe_first=wipe)
    except (RuntimeError, ImportError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(report.summary())
    if verify:
        typer.echo("")
        result = verify_directory(data, db_path, truth=truth)
        typer.echo(result.summary())
        if not result.ok:
            raise typer.Exit(code=1)


@verify_app.command("ladybug", short_help="Compare a LadybugDB / Kùzu database against the dataset it was loaded from.")
def verify_ladybug(
    data: str = typer.Argument(..., help="The dataset directory that was loaded. It is the oracle."),
    db: str = typer.Option(None, help="Database path. Default <data>/graph.lbdb."),
    truth: bool = typer.Option(True, "--truth/--blind", help="--blind for a database loaded without the truth subgraph."),
    sample: int = typer.Option(25, help="Rows per label fetched back and compared property by property. 0 skips it."),
    verbose: bool = typer.Option(False, help="Print every check, not only the failures."),
):
    """Exits non-zero if the database does not match the dataset."""
    from graphfaker.sinks.ladybug import verify_directory

    result = verify_directory(data, db or os.path.join(data, "graph.lbdb"), truth=truth, sample=sample)
    typer.echo(result.summary(verbose=verbose))
    if not result.ok:
        raise typer.Exit(code=1)


@load_app.command("duckdb", short_help="Load a dataset directory into a new DuckDB database with a SQL/PGQ property graph.")
def load_duckdb(
    data: str = typer.Argument(..., help="Directory written by `graphfaker generate` or `graphfaker fraud`."),
    db: str = typer.Option(None, help="Database file to create. Default <data>/graph.duckdb."),
    graph: str = typer.Option(None, help="Property graph name. Default: the dataset's schema name (fraud, social)."),
    truth: bool = typer.Option(
        True,
        "--truth/--blind",
        help="--blind loads the graph only: no Pattern table and no is_fraud, so the dataset stays usable as an unbiased benchmark.",
    ),
    wipe: bool = typer.Option(False, help="Replace the database if it already exists."),
    verify: bool = typer.Option(True, help="Run the checks in `graphfaker verify duckdb` after loading."),
):
    """Example: graphfaker load duckdb ./bank --db ./bank/graph.duckdb"""
    from graphfaker.sinks.duckdb import load_directory, verify_directory

    db_path = db or os.path.join(data, "graph.duckdb")
    typer.echo(f"loading {data} into {db_path}" + ("" if truth else " (blind)"))
    try:
        report = load_directory(data, db_path, truth=truth, graph=graph, wipe_first=wipe)
    except (RuntimeError, ImportError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(report.summary())
    if verify:
        typer.echo("")
        result = verify_directory(data, db_path, truth=truth)
        typer.echo(result.summary())
        if not result.ok:
            raise typer.Exit(code=1)


@verify_app.command("duckdb", short_help="Compare a DuckDB database against the dataset it was loaded from.")
def verify_duckdb(
    data: str = typer.Argument(..., help="The dataset directory that was loaded. It is the oracle."),
    db: str = typer.Option(None, help="Database file. Default <data>/graph.duckdb."),
    truth: bool = typer.Option(True, "--truth/--blind", help="--blind for a database loaded without the truth."),
    sample: int = typer.Option(25, help="Rows per table fetched back and compared column by column. 0 skips it."),
    verbose: bool = typer.Option(False, help="Print every check, not only the failures."),
):
    """Exits non-zero if the database does not match the dataset."""
    from graphfaker.sinks.duckdb import verify_directory

    result = verify_directory(data, db or os.path.join(data, "graph.duckdb"), truth=truth, sample=sample)
    typer.echo(result.summary(verbose=verbose))
    if not result.ok:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
