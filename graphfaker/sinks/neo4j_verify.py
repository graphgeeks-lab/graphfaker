"""Verify a Neo4j database against the dataset it was loaded from.

The checks live in :mod:`graphfaker.sinks.verify` and are shared with the
LadybugDB sink; this module supplies the Neo4j side of the
:class:`~graphfaker.sinks.verify.Backend` protocol (catalogue queries, the
constraint list, and how a node comes back) plus the two entry points the CLI
and ``docs/neo4j.md`` use.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from graphfaker.backends.tables import ID, GraphTables
from graphfaker.sinks.neo4j_live import (
    MEMBER_REL,
    PATTERN_LABEL,
    Target,
    _quote,
    _run,
    read_truth,
)
from graphfaker.sinks.verify import (
    DEFAULT_SAMPLE,
    TOLERANCE,
    Check,
    Verification,
    _equal,
    _native,
    truth_labels,
    verify,
)

if TYPE_CHECKING:  # pragma: no cover
    from neo4j import Driver

__all__ = [
    "DEFAULT_SAMPLE",
    "MEMBER_REL",
    "PATTERN_LABEL",
    "TOLERANCE",
    "Check",
    "Neo4jBackend",
    "Verification",
    "_equal",
    "_native",
    "_truth_labels",
    "verify_directory",
    "verify_tables",
]

#: Kept under the old private names for callers and tests that import them.
_truth_labels = truth_labels


class Neo4jBackend:
    def __init__(self, driver: Driver, database: str):
        self.driver = driver
        self.database = database
        self.name = f"database {database!r}"

    def run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        return _run(self.driver, self.database, cypher, **params)

    def quote(self, name: str) -> str:
        return _quote(name)

    def labels_in_use(self) -> list[str]:
        """Labels that actually have nodes.

        ``db.labels()`` lists label *tokens*, and a token outlives the last node
        that carried it: load a dataset with truth, wipe, reload with ``--blind``
        and ``Pattern`` is still listed with nothing under it. Counting per label
        is answered from the count store, so asking what is populated rather than
        what is registered costs nothing and is the question we mean."""
        tokens = self.run("CALL db.labels() YIELD label RETURN collect(label) AS labels")[0]["labels"]
        return [label for label in tokens if self.run(f"MATCH (n:{_quote(label)}) RETURN count(n) AS n")[0]["n"]]

    def types_in_use(self) -> list[str]:
        tokens = self.run("CALL db.relationshipTypes() YIELD relationshipType RETURN collect(relationshipType) AS t")[0]["t"]
        return [rel for rel in tokens if self.run(f"MATCH ()-[r:{_quote(rel)}]->() RETURN count(r) AS n")[0]["n"]]

    def constraints(self, labels: list[str]) -> set[str] | None:
        rows = self.run("SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties RETURN name")
        return {row["name"] for row in rows}

    def endpoint_mismatches(self, rel: str, src: str, dst: str) -> int:
        return self.run(
            f"MATCH (a)-[r:{_quote(rel)}]->(b) WHERE NOT a:{_quote(src)} OR NOT b:{_quote(dst)} RETURN count(r) AS n"
        )[0]["n"]

    def fetch_nodes(self, label: str, ids: list[Any]) -> dict[Any, dict[str, Any]]:
        rows = self.run(f"UNWIND $ids AS id MATCH (n:{_quote(label)} {{{ID}: id}}) RETURN n {{.*}} AS n", ids=ids)
        return {row["n"][ID]: row["n"] for row in rows}

    def fetch_edges(self, rel: str, tx_ids: list[Any]) -> dict[Any, dict[str, Any]]:
        rows = self.run(
            f"UNWIND $ids AS id MATCH (a)-[r:{_quote(rel)}]->(b) WHERE r.tx_id = id "
            "RETURN r {.*} AS r, a.id AS source, b.id AS target",
            ids=tx_ids,
        )
        return {row["r"]["tx_id"]: {**row["r"], "source": row["source"], "target": row["target"]} for row in rows}


def verify_tables(
    tables: GraphTables,
    target: Target | None = None,
    truth: dict[str, pl.DataFrame] | None = None,
    *,
    sample: int = DEFAULT_SAMPLE,
    driver: Driver | None = None,
) -> Verification:
    """Compare a loaded Neo4j database against the tables it came from."""
    target = target or Target()
    own_driver = driver is None
    driver = driver or target.connect()
    try:
        return verify(Neo4jBackend(driver, target.database), tables, truth, sample=sample)
    finally:
        if own_driver:
            driver.close()


def verify_directory(
    directory: str | Path,
    target: Target | None = None,
    *,
    truth: bool = True,
    sample: int = DEFAULT_SAMPLE,
    driver: Driver | None = None,
) -> Verification:
    """Verify Neo4j against the dataset directory that was loaded into it.

    ``truth=False`` for a database loaded with ``--blind``: the truth
    subgraph is then expected to be absent, and its checks are skipped.
    """
    root = Path(directory)
    tables = GraphTables.read_parquet(root)
    return verify_tables(tables, target, read_truth(root) if truth else None, sample=sample, driver=driver)

