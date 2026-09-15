"""Load graph tables into a running Neo4j over Bolt.

``neo4j-admin database import`` (see :mod:`graphfaker.sinks.neo4j`) is the
fastest way in, but it is an offline tool: it wants the database stopped, the
binary on your PATH and the CSVs inside the server's import directory. That
is the wrong shape for the common case: a Neo4j you already have running,
in Desktop or a container, that you want a generated dataset in *now*. This
sink is that path: batched ``UNWIND`` writes through the driver, no restart,
no file staging, works against Aura.

Three things make it more than a loop over rows:

* **Uniqueness constraints first.** Relationship loading matches its
  endpoints by ``id``; without a constraint-backed index that is a full scan
  per row and the load never finishes. The constraints are also the
  correctness contract the verifier checks.
* **Endpoint labels are resolved, not guessed.** ``infer_endpoints`` reads
  them off the data, so ``MATCH (a:Account {id: ...})`` hits one index
  instead of scanning every node.
* **Ground truth is a subgraph, not a column.** An account takes part in
  more than one pattern, so membership and its role live on
  ``(:Account)-[:IN_PATTERN {role}]->(:Pattern)`` rather than being
  flattened onto the account. ``truth=False`` loads none of it, which is how
  you get a copy of the dataset that can still be used as a blind benchmark.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from graphfaker.backends.tables import ID, SOURCE, TARGET, GraphTables
from graphfaker.logger import logger
from graphfaker.sinks.neo4j import infer_endpoints

if TYPE_CHECKING:  # pragma: no cover - import cost, driver is optional
    from neo4j import Driver

#: Rows per write transaction. Big enough that round trips stop mattering,
#: small enough to fit a default 1G heap.
DEFAULT_BATCH = 10_000

#: Label given to a pattern from ``truth/patterns.parquet``.
PATTERN_LABEL = "Pattern"
MEMBER_REL = "IN_PATTERN"


@dataclass(frozen=True)
class Target:
    """Where to load. Every field falls back to the conventional Neo4j
    environment variable, so a configured shell needs no flags at all."""

    uri: str = field(default_factory=lambda: os.environ.get("NEO4J_URI", "neo4j://127.0.0.1:7687"))
    user: str = field(default_factory=lambda: os.environ.get("NEO4J_USER", "neo4j"))
    password: str = field(default_factory=lambda: os.environ.get("NEO4J_PASSWORD", "neo4j"))
    database: str = field(default_factory=lambda: os.environ.get("NEO4J_DATABASE", "neo4j"))

    def connect(self) -> Driver:
        """An open, verified driver. Raises if Neo4j is unreachable or the
        credentials are wrong, rather than failing on the first write."""
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:  # pragma: no cover - depends on install
            raise ImportError(
                "the Neo4j sink needs the driver: `pip install 'graphfaker[neo4j]'`"
            ) from exc
        driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        try:
            driver.verify_connectivity()
        except Exception:
            driver.close()
            raise
        return driver


@dataclass
class LoadReport:
    """What was written. The counts are what the loader believes it sent;
    :mod:`graphfaker.sinks.neo4j_verify` is what confirms it arrived."""

    database: str
    nodes: dict[str, int] = field(default_factory=dict)
    edges: dict[str, int] = field(default_factory=dict)
    truth: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def node_total(self) -> int:
        return sum(self.nodes.values())

    @property
    def edge_total(self) -> int:
        return sum(self.edges.values())

    def summary(self) -> str:
        def counts(mapping: dict[str, int]) -> str:
            return ", ".join(f"{k}={v}" for k, v in mapping.items()) or "none"

        rate = (self.node_total + self.edge_total) / self.seconds if self.seconds else 0.0
        return "\n".join(
            [
                f"loaded into database {self.database!r} in {self.seconds:.1f}s ({rate:,.0f} rows/s)",
                f"  nodes  {self.node_total:>9,}  {counts(self.nodes)}",
                f"  edges  {self.edge_total:>9,}  {counts(self.edges)}",
                f"  truth  {sum(self.truth.values()):>9,}  {counts(self.truth)}",
            ]
        )


# --------------------------------------------------------------- primitives


def _batches(frame: pl.DataFrame, size: int) -> Iterator[list[dict[str, Any]]]:
    """Rows as driver-ready dicts. Polars hands back ``date``/``datetime``
    objects, which the driver maps to Neo4j temporal types directly."""
    for chunk in frame.iter_slices(size):
        yield chunk.to_dicts()


def _run(driver: Driver, database: str, cypher: str, **params: Any) -> list[dict[str, Any]]:
    records, _, _ = driver.execute_query(cypher, database_=database, **params)
    return [record.data() for record in records]


def _write_batches(
    driver: Driver, database: str, cypher: str, frame: pl.DataFrame, size: int, label: str
) -> int:
    """Run ``cypher`` once per batch of ``frame``, with ``$rows`` bound."""
    written = 0
    for rows in _batches(frame, size):
        _run(driver, database, cypher, rows=rows)
        written += len(rows)
        if frame.height > size:
            logger.debug("neo4j: %s %d/%d", label, written, frame.height)
    logger.info("neo4j: %s x %d", label, written)
    return written


def database_counts(driver: Driver, database: str) -> tuple[int, int]:
    """``(nodes, relationships)`` currently in the database."""
    rows = _run(
        driver,
        database,
        "MATCH (n) WITH count(n) AS nodes "
        "CALL () { MATCH ()-[r]->() RETURN count(r) AS rels } RETURN nodes, rels",
    )
    return rows[0]["nodes"], rows[0]["rels"]


#: Neo4j database names: 3-63 characters, starting with a letter, and only
#: ASCII letters, digits, dots and dashes. Underscores are *not* allowed,
#: which catches everyone who names a dataset directory ``fraud_data``.
_DATABASE_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9.\-]{2,62}$")


def check_database_name(database: str) -> None:
    """Raise if Neo4j would reject ``database``, before a round trip does."""
    if not _DATABASE_NAME.match(database):
        raise ValueError(
            f"{database!r} is not a usable Neo4j database name: 3-63 characters, "
            "starting with a letter, and only letters, digits, dots and dashes "
            "(no underscores). Try " + (database.replace("_", "-") or "graphfaker") + "."
        )


def create_database(driver: Driver, database: str) -> None:
    """Create ``database`` if it does not exist. Enterprise and Aura only;
    on Community the single database cannot be added to, and the caller is
    told so rather than being handed a driver error on the first write."""
    check_database_name(database)
    try:
        _run(driver, "system", f"CREATE DATABASE {_quote(database)} IF NOT EXISTS WAIT")
    except Exception as exc:
        raise RuntimeError(
            f"could not create database {database!r}: {exc}. "
            "Multi-database needs Neo4j Enterprise or Aura; on Community use --database neo4j."
        ) from exc


def wipe(driver: Driver, database: str, size: int = 50_000) -> int:
    """Delete every node and relationship, in batches so the transaction
    never has to hold the whole graph in the heap."""
    deleted = 0
    while True:
        rows = _run(
            driver,
            database,
            f"MATCH (n) WITH n LIMIT {size} DETACH DELETE n RETURN count(n) AS n",
        )
        gone = rows[0]["n"] if rows else 0
        deleted += gone
        if gone == 0:
            return deleted
        logger.debug("neo4j: wiped %d nodes", deleted)


def _quote(name: str) -> str:
    """Backtick-quote an identifier that gets interpolated into Cypher.

    Labels, relationship types and database names cannot be parameters. All
    of them come from schema and file names here, never from a query result,
    but an escaped backtick still keeps a name like ``Odd`Label`` from
    changing the statement."""
    return "`" + name.replace("`", "``") + "`"


# ---------------------------------------------------------------- structure


def ensure_constraints(driver: Driver, database: str, tables: GraphTables, truth: bool = True) -> list[str]:
    """One uniqueness constraint per node label on ``id``, plus a property
    index on ``tx_id`` for every relationship type that has one.

    The constraints make relationship loading an index lookup instead of a
    scan; the ``tx_id`` indexes are what let the truth pass find the
    transactions it labels. Returns the constraint and index names created.
    """
    names = []
    labels = list(tables.nodes)
    if truth:
        labels.append(PATTERN_LABEL)
    for label in labels:
        name = f"gf_{label}_id"
        _run(
            driver,
            database,
            f"CREATE CONSTRAINT {_quote(name)} IF NOT EXISTS "
            f"FOR (n:{_quote(label)}) REQUIRE n.{ID} IS UNIQUE",
        )
        names.append(name)
    for rel, frame in tables.edges.items():
        if "tx_id" not in frame.columns:
            continue
        name = f"gf_{rel}_tx_id"
        _run(
            driver,
            database,
            f"CREATE INDEX {_quote(name)} IF NOT EXISTS "
            f"FOR ()-[r:{_quote(rel)}]-() ON (r.tx_id)",
        )
        names.append(name)
    _run(driver, database, "CALL db.awaitIndexes(600)")
    logger.info("neo4j: %d constraints and indexes online", len(names))
    return names


# --------------------------------------------------------------------- load


def _load_nodes(driver: Driver, database: str, tables: GraphTables, size: int) -> dict[str, int]:
    counts = {}
    for label, frame in tables.nodes.items():
        cypher = f"UNWIND $rows AS row CREATE (n:{_quote(label)}) SET n = row"
        counts[label] = _write_batches(driver, database, cypher, frame, size, f"(:{label})")
    return counts


def _load_edges(driver: Driver, database: str, tables: GraphTables, size: int) -> dict[str, int]:
    endpoints = infer_endpoints(tables)
    counts = {}
    for rel, frame in tables.edges.items():
        if frame.height == 0:
            continue
        src, dst = endpoints[rel]
        rows, has_props = _edge_rows(frame)
        cypher = (
            "UNWIND $rows AS row "
            f"MATCH (a:{_quote(src)} {{{ID}: row.{SOURCE}}}) "
            f"MATCH (b:{_quote(dst)} {{{ID}: row.{TARGET}}}) "
            f"CREATE (a)-[r:{_quote(rel)}]->(b)" + (" SET r = row.props" if has_props else "")
        )
        counts[rel] = _write_batches(driver, database, cypher, rows, size, f"[:{rel}]")
    return counts


def _edge_rows(frame: pl.DataFrame) -> tuple[pl.DataFrame, bool]:
    """``source``, ``target`` and a ``props`` struct of the attributes, so
    they go in with one ``SET r = row.props``.

    Plenty of relationships have no attributes at all, most of the social
    graph included, and polars has no empty struct, so the flag says whether the
    caller should emit the ``SET`` clause."""
    attrs = [c for c in frame.columns if c not in (SOURCE, TARGET)]
    if not attrs:
        return frame.select([SOURCE, TARGET]), False
    return frame.select([SOURCE, TARGET, pl.struct(attrs).alias("props")]), True


def _load_truth(
    driver: Driver, database: str, tables: GraphTables, truth: dict[str, pl.DataFrame], size: int
) -> dict[str, int]:
    """Land ``truth/`` as a subgraph.

    ``patterns`` become ``(:Pattern)`` nodes; ``accounts`` become
    ``IN_PATTERN`` relationships carrying the account's role, because an
    account can be in several patterns; ``transactions`` set ``is_fraud`` and
    the owning pattern on the money relationship they name. Any other frame
    with a ``group`` column is a latent factor from the schema and becomes a
    small node set labelled after the frame, joined to the graph on the
    matching node property (``a.region = r.group``).
    """
    counts: dict[str, int] = {}

    patterns = truth.get("patterns")
    if patterns is not None and patterns.height:
        # accounts/roles are the denormalised form of IN_PATTERN; drop them.
        keep = [c for c in patterns.columns if c not in ("accounts", "roles")]
        frame = patterns.select(keep).rename({"pattern_id": ID})
        cypher = f"UNWIND $rows AS row CREATE (p:{_quote(PATTERN_LABEL)}) SET p = row"
        counts[PATTERN_LABEL] = _write_batches(
            driver, database, cypher, frame, size, f"(:{PATTERN_LABEL})"
        )

    members = truth.get("accounts")
    if members is not None and members.height:
        renamed = members.rename({"account_id": SOURCE, "pattern_id": TARGET})
        frame, has_props = _edge_rows(renamed)
        cypher = (
            "UNWIND $rows AS row "
            f"MATCH (a:Account {{{ID}: row.{SOURCE}}}) "
            f"MATCH (p:{_quote(PATTERN_LABEL)} {{{ID}: row.{TARGET}}}) "
            f"CREATE (a)-[r:{_quote(MEMBER_REL)}]->(p)" + (" SET r = row.props" if has_props else "")
        )
        counts[MEMBER_REL] = _write_batches(
            driver, database, cypher, frame, size, f"[:{MEMBER_REL}]"
        )

    transactions = truth.get("transactions")
    if transactions is not None and transactions.height:
        # Partition by relationship type so each update is index-backed and
        # the counts are attributable per type.
        for rel, edges in tables.edges.items():
            if "tx_id" not in edges.columns:
                continue
            labelled = transactions.join(edges.select("tx_id"), on="tx_id", how="semi")
            if not labelled.height:
                continue
            attrs = [c for c in labelled.columns if c != "tx_id"]
            frame = labelled.select(["tx_id", pl.struct(attrs).alias("props")])
            cypher = (
                "UNWIND $rows AS row "
                f"MATCH ()-[r:{_quote(rel)}]->() WHERE r.tx_id = row.tx_id "
                "SET r += row.props"
            )
            counts[f"{rel}.is_fraud"] = _write_batches(
                driver, database, cypher, frame, size, f"[:{rel}] truth"
            )

    for name, frame in truth.items():
        if name in ("patterns", "accounts", "transactions") or "group" not in frame.columns:
            continue
        label = name.title()
        _run(
            driver,
            database,
            f"CREATE CONSTRAINT {_quote(f'gf_{label}_id')} IF NOT EXISTS "
            f"FOR (n:{_quote(label)}) REQUIRE n.{ID} IS UNIQUE",
        )
        rows = frame.with_columns(
            (pl.lit(f"{name}_") + pl.col("group").cast(pl.String)).alias(ID)
        )
        cypher = f"UNWIND $rows AS row CREATE (n:{_quote(label)}) SET n = row"
        counts[label] = _write_batches(driver, database, cypher, rows, size, f"(:{label})")

    return counts


def load_tables(
    tables: GraphTables,
    target: Target | None = None,
    truth: dict[str, pl.DataFrame] | None = None,
    *,
    batch_size: int = DEFAULT_BATCH,
    wipe_first: bool = False,
    create: bool = False,
    driver: Driver | None = None,
) -> LoadReport:
    """Load ``tables`` (and ``truth``, when given) into Neo4j.

    Refuses to write into a database that already holds data unless
    ``wipe_first`` is set: the loader uses ``CREATE``, so a second run into a
    populated database would silently double everything.
    """
    target = target or Target()
    own_driver = driver is None
    driver = driver or target.connect()
    started = time.perf_counter()
    try:
        if create:
            create_database(driver, target.database)
        nodes, rels = database_counts(driver, target.database)
        if nodes or rels:
            if not wipe_first:
                raise RuntimeError(
                    f"database {target.database!r} already holds {nodes:,} nodes and "
                    f"{rels:,} relationships; pass wipe_first=True (CLI: --wipe) to replace it, "
                    "or load into a different --database"
                )
            logger.info("neo4j: wiping %d nodes from %r", nodes, target.database)
            wipe(driver, target.database)

        ensure_constraints(driver, target.database, tables, truth=bool(truth))
        report = LoadReport(database=target.database)
        report.nodes = _load_nodes(driver, target.database, tables, batch_size)
        report.edges = _load_edges(driver, target.database, tables, batch_size)
        if truth:
            report.truth = _load_truth(driver, target.database, tables, truth, batch_size)
        report.seconds = time.perf_counter() - started
        return report
    finally:
        if own_driver:
            driver.close()


def read_truth(directory: str | Path) -> dict[str, pl.DataFrame]:
    """``truth/*.parquet`` from a dataset directory, by frame name."""
    root = Path(directory) / "truth"
    return {path.stem: pl.read_parquet(path) for path in sorted(root.glob("*.parquet"))}


def load_directory(
    directory: str | Path,
    target: Target | None = None,
    *,
    truth: bool = True,
    batch_size: int = DEFAULT_BATCH,
    wipe_first: bool = False,
    create: bool = False,
    driver: Driver | None = None,
) -> LoadReport:
    """Load a dataset written by ``graphfaker generate`` / ``graphfaker fraud``.

    ``truth=False`` loads the graph only: no ``(:Pattern)`` nodes and no
    ``is_fraud`` anywhere. That is the copy to hand to a detector you want
    to evaluate honestly.
    """
    root = Path(directory)
    tables = GraphTables.read_parquet(root)
    return load_tables(
        tables,
        target,
        read_truth(root) if truth else None,
        batch_size=batch_size,
        wipe_first=wipe_first,
        create=create,
        driver=driver,
    )
