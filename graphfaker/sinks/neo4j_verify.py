"""Check that what is in Neo4j is the dataset that was generated.

A load that reports no error is not a load that is correct. Rows get dropped
when a relationship's endpoint is missing, properties vanish when a column
name collides with a Cypher keyword, a ``double`` silently becomes a string,
a re-run doubles every edge, a timestamp loses its time. None of that raises
It just quietly gives you a different graph than the one whose ground
truth you are about to trust.

So the dataset on disk is the oracle and Neo4j is the thing under test. Each
check states what Parquet says, asks Neo4j the same question, and reports
both. The four families, cheapest first:

* **cardinality**: a count per label and per relationship type, and no
  labels or types beyond the expected ones.
* **structure**: every constraint online, no duplicate keys, and every
  relationship between the endpoint labels its type is supposed to join.
* **content**: one aggregate scan per label and type: how many nodes
  actually carry each property, and the sum, min and max of every numeric
  and temporal column. This is what catches coercion and truncation, which
  counts cannot see.
* **round trip**: a sample of rows fetched back and compared property by
  property against Parquet, which catches everything the aggregates hide.

Plus the truth subgraph, when it was loaded. Failures print expected against
actual so the output is a diagnosis, not a red light.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from graphfaker.backends.tables import ID, SOURCE, TARGET, GraphTables
from graphfaker.sinks.neo4j import infer_endpoints
from graphfaker.sinks.neo4j_live import (
    MEMBER_REL,
    PATTERN_LABEL,
    Target,
    _quote,
    _run,
    read_truth,
)

if TYPE_CHECKING:  # pragma: no cover
    from neo4j import Driver

#: Rows per label fetched back and compared field by field.
DEFAULT_SAMPLE = 25

#: Relative tolerance for float aggregates. Doubles round-trip exactly, but
#: a sum of 600k of them depends on summation order.
TOLERANCE = 1e-9

_NUMERIC = (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64, pl.Float32, pl.Float64)


@dataclass
class Check:
    name: str
    ok: bool
    expected: Any = None
    actual: Any = None
    detail: str = ""

    def line(self) -> str:
        mark = "ok  " if self.ok else "FAIL"
        body = f"{mark} {self.name}"
        if not self.ok:
            body += f": expected {self.expected!r}, got {self.actual!r}"
        if self.detail:
            body += f" ({self.detail})"
        return body


@dataclass
class Verification:
    database: str
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, expected: Any, actual: Any, detail: str = "") -> Check:
        check = Check(name, _equal(expected, actual), expected, actual, detail)
        self.checks.append(check)
        return check

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self, verbose: bool = False) -> str:
        passed = len(self.checks) - len(self.failures)
        head = (
            f"{'PASS' if self.ok else 'FAIL'}: {passed}/{len(self.checks)} checks on "
            f"database {self.database!r}"
        )
        shown = self.checks if verbose else self.failures
        return "\n".join([head, *(f"  {c.line()}" for c in shown)])


def _equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, float) or isinstance(actual, float):
        if expected is None or actual is None:
            return expected is actual
        if math.isnan(float(expected)) or math.isnan(float(actual)):
            return math.isnan(float(expected)) and math.isnan(float(actual))
        return math.isclose(float(expected), float(actual), rel_tol=TOLERANCE, abs_tol=1e-6)
    if isinstance(expected, (list, set)) and isinstance(actual, (list, set)):
        return sorted(map(str, expected)) == sorted(map(str, actual))
    return expected == actual


def _native(value: Any) -> Any:
    """Driver temporals and numbers as comparable Python objects."""
    if hasattr(value, "to_native"):
        value = value.to_native()
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    return value


def _aggregate_clauses(schema: dict[str, pl.DataType], variable: str) -> list[str]:
    """``count``/``sum``/``min``/``max`` per column, all in one scan."""
    clauses = []
    for column, dtype in schema.items():
        ref = f"{variable}.{_quote(column)}"
        clauses.append(f"count({ref}) AS {_quote(f'present:{column}')}")
        if any(dtype == t for t in _NUMERIC):
            clauses.append(f"sum({ref}) AS {_quote(f'sum:{column}')}")
        if any(dtype == t for t in _NUMERIC) or dtype == pl.Date or isinstance(dtype, pl.Datetime):
            clauses.append(f"min({ref}) AS {_quote(f'min:{column}')}")
            clauses.append(f"max({ref}) AS {_quote(f'max:{column}')}")
    return clauses


def _expected_aggregates(frame: pl.DataFrame) -> dict[str, Any]:
    """The same aggregates, computed from Parquet."""
    expected: dict[str, Any] = {}
    for column, dtype in frame.schema.items():
        series = frame[column]
        expected[f"present:{column}"] = frame.height - series.null_count()
        numeric = any(dtype == t for t in _NUMERIC)
        if numeric:
            expected[f"sum:{column}"] = series.sum()
        if numeric or dtype == pl.Date or isinstance(dtype, pl.Datetime):
            expected[f"min:{column}"] = series.min()
            expected[f"max:{column}"] = series.max()
    return expected


def _first(
    v: Verification, name: str, driver: Driver, database: str, cypher: str, **params: Any
) -> dict[str, Any] | None:
    """The first row of ``cypher``, or ``None`` with a failed check recorded.

    The aggregate scans are the expensive part of the report and the part
    most likely to hit a server limit; a failure there has to be reported as
    a failed check rather than abort the remaining checks."""
    try:
        rows = _run(driver, database, cypher, **params)
    except Exception as exc:
        v.checks.append(
            Check(name, ok=False, expected="query to run", actual=f"{type(exc).__name__}: {exc}")
        )
        return None
    return rows[0] if rows else None


# ------------------------------------------------------------------- checks


def _labels_in_use(driver: Driver, database: str) -> list[str]:
    """Labels that actually have nodes.

    ``db.labels()`` lists label *tokens*, and a token outlives the last node
    that carried it: load a dataset with truth, wipe, reload with ``--blind``
    and ``Pattern`` is still listed with nothing under it. Counting per label
    is answered from the count store, so asking what is populated rather than
    what is registered costs nothing and is the question we mean."""
    tokens = _run(driver, database, "CALL db.labels() YIELD label RETURN collect(label) AS labels")[0]["labels"]
    return [
        label
        for label in tokens
        if _run(driver, database, f"MATCH (n:{_quote(label)}) RETURN count(n) AS n")[0]["n"]
    ]


def _types_in_use(driver: Driver, database: str) -> list[str]:
    """Relationship types that actually have relationships; see
    :func:`_labels_in_use` for why the token list is not enough."""
    tokens = _run(
        driver, database, "CALL db.relationshipTypes() YIELD relationshipType RETURN collect(relationshipType) AS t"
    )[0]["t"]
    return [
        rel
        for rel in tokens
        if _run(driver, database, f"MATCH ()-[r:{_quote(rel)}]->() RETURN count(r) AS n")[0]["n"]
    ]


def _truth_labels(truth: dict[str, pl.DataFrame] | None) -> set[str]:
    """Labels the truth subgraph adds: ``Pattern``, plus one per latent
    factor frame (``truth/region.parquet`` -> ``(:Region)``)."""
    if not truth:
        return set()
    labels = {PATTERN_LABEL} if truth.get("patterns") is not None else set()
    for name, frame in truth.items():
        if name not in ("patterns", "accounts", "transactions") and "group" in frame.columns:
            labels.add(name.title())
    return labels


def _check_structure(
    driver: Driver,
    database: str,
    tables: GraphTables,
    v: Verification,
    truth: dict[str, pl.DataFrame] | None,
) -> None:
    extra = _truth_labels(truth)
    rows = _run(driver, database, "SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties RETURN name")
    present = {row["name"] for row in rows}
    expected = {f"gf_{label}_id" for label in [*tables.nodes, *extra]}
    v.add("structure/constraints", sorted(expected), sorted(present & expected))

    v.add(
        "structure/labels",
        sorted(set(tables.nodes) | extra),
        sorted(_labels_in_use(driver, database)),
        "no labels beyond the schema",
    )

    expected_types = {rel for rel, frame in tables.edges.items() if frame.height}
    if truth and truth.get("accounts") is not None:
        expected_types.add(MEMBER_REL)
    v.add(
        "structure/relationship_types",
        sorted(expected_types),
        sorted(_types_in_use(driver, database)),
    )


def _check_nodes(driver: Driver, database: str, tables: GraphTables, v: Verification) -> None:
    for label, frame in tables.nodes.items():
        clauses = ", ".join(
            [
                "count(n) AS total",
                f"count(DISTINCT n.{_quote(ID)}) AS distinct_ids",
                *_aggregate_clauses(frame.schema, "n"),
            ]
        )
        row = _first(
            v, f"content/{label}", driver, database, f"MATCH (n:{_quote(label)}) RETURN {clauses}"
        )
        if row is None:
            continue
        v.add(f"count/{label}", frame.height, row["total"])
        v.add(f"structure/{label}.unique_id", frame.height, row["distinct_ids"], "no duplicated nodes")
        for key, expected in _expected_aggregates(frame).items():
            v.add(f"content/{label}.{key}", expected, _native(row[key]))


def _check_edges(driver: Driver, database: str, tables: GraphTables, v: Verification) -> None:
    endpoints = infer_endpoints(tables)
    for rel, frame in tables.edges.items():
        if frame.height == 0:
            continue
        attrs = {c: t for c, t in frame.schema.items() if c not in (SOURCE, TARGET)}
        src, dst = endpoints[rel]
        clauses = ", ".join(["count(r) AS total", *_aggregate_clauses(attrs, "r")])
        row = _first(
            v,
            f"content/{rel}",
            driver,
            database,
            f"MATCH ()-[r:{_quote(rel)}]->() RETURN {clauses}",
        )
        if row is None:
            continue
        v.add(f"count/{rel}", frame.height, row["total"])
        for key, expected in _expected_aggregates(frame.select(list(attrs))).items():
            v.add(f"content/{rel}.{key}", expected, _native(row[key]))

        wrong = _run(
            driver,
            database,
            f"MATCH (a)-[r:{_quote(rel)}]->(b) "
            f"WHERE NOT a:{_quote(src)} OR NOT b:{_quote(dst)} RETURN count(r) AS n",
        )[0]["n"]
        v.add(f"structure/{rel}.endpoints", 0, wrong, f"every edge is ({src})->({dst})")

        if "tx_id" in attrs:
            name = f"structure/{rel}.unique_tx_id"
            dupes = _first(
                v,
                name,
                driver,
                database,
                f"MATCH ()-[r:{_quote(rel)}]->() WITH r.tx_id AS k, count(*) AS c "
                "WHERE c > 1 RETURN count(*) AS n",
            )
            if dupes is not None:
                v.add(name, 0, dupes["n"], "no transaction loaded twice")


def _check_truth(
    driver: Driver, database: str, tables: GraphTables, truth: dict[str, pl.DataFrame], v: Verification
) -> None:
    patterns = truth.get("patterns")
    if patterns is not None and patterns.height:
        total = _run(driver, database, f"MATCH (p:{_quote(PATTERN_LABEL)}) RETURN count(p) AS n")[0]["n"]
        v.add(f"truth/{PATTERN_LABEL}.count", patterns.height, total)
        fraud = _run(
            driver, database, f"MATCH (p:{_quote(PATTERN_LABEL)}) WHERE p.is_fraud RETURN count(p) AS n"
        )[0]["n"]
        v.add(
            f"truth/{PATTERN_LABEL}.is_fraud",
            patterns.filter(pl.col("is_fraud")).height,
            fraud,
            "decoy patterns stay unflagged",
        )

    members = truth.get("accounts")
    if members is not None and members.height:
        row = _run(
            driver,
            database,
            f"MATCH (a:Account)-[r:{_quote(MEMBER_REL)}]->(p:{_quote(PATTERN_LABEL)}) "
            "RETURN count(r) AS rels, count(DISTINCT a) AS accounts",
        )[0]
        v.add(f"truth/{MEMBER_REL}.count", members.height, row["rels"], "one per membership")
        v.add(
            f"truth/{MEMBER_REL}.accounts",
            members["account_id"].n_unique(),
            row["accounts"],
            "accounts can be in several patterns",
        )
        # The membership must reach the account the truth names, not just any.
        missing = _run(
            driver,
            database,
            "UNWIND $ids AS id MATCH (a:Account {id: id}) "
            f"WHERE NOT (a)-[:{_quote(MEMBER_REL)}]->() RETURN count(a) AS n",
            ids=members["account_id"].unique().to_list(),
        )[0]["n"]
        v.add(f"truth/{MEMBER_REL}.reachable", 0, missing, "every truth account has a membership")

    transactions = truth.get("transactions")
    if transactions is not None and transactions.height:
        for rel, edges in tables.edges.items():
            if "tx_id" not in edges.columns:
                continue
            expected = transactions.join(edges.select("tx_id"), on="tx_id", how="semi")
            actual = _run(
                driver,
                database,
                f"MATCH ()-[r:{_quote(rel)}]->() WHERE r.pattern_id IS NOT NULL RETURN count(r) AS n",
            )[0]["n"]
            v.add(f"truth/{rel}.labelled", expected.height, actual)
            if expected.height:
                flagged = _run(
                    driver,
                    database,
                    f"MATCH ()-[r:{_quote(rel)}]->() WHERE r.is_fraud RETURN count(r) AS n",
                )[0]["n"]
                v.add(f"truth/{rel}.is_fraud", expected.filter(pl.col("is_fraud")).height, flagged)

    for name, frame in truth.items():
        if name in ("patterns", "accounts", "transactions") or "group" not in frame.columns:
            continue
        label = name.title()
        total = _run(driver, database, f"MATCH (n:{_quote(label)}) RETURN count(n) AS n")[0]["n"]
        v.add(f"truth/{label}.count", frame.height, total)


def _check_round_trip(
    driver: Driver, database: str, tables: GraphTables, v: Verification, sample: int
) -> None:
    """Fetch rows back and compare every property, which is the only check
    that would notice a value being replaced by a different value of the
    same type."""
    for label, frame in tables.nodes.items():
        if frame.height == 0:
            continue
        step = max(1, frame.height // sample)
        wanted = frame.gather(range(0, frame.height, step)[:sample])
        rows = _run(
            driver,
            database,
            f"UNWIND $ids AS id MATCH (n:{_quote(label)} {{{ID}: id}}) RETURN n {{.*}} AS n",
            ids=wanted[ID].to_list(),
        )
        got = {row["n"][ID]: row["n"] for row in rows}
        mismatches = []
        for expected in wanted.to_dicts():
            actual = got.get(expected[ID])
            if actual is None:
                mismatches.append(f"{expected[ID]} missing")
                continue
            for column, value in expected.items():
                if value is None:
                    continue
                if not _equal(value, _native(actual.get(column))):
                    mismatches.append(f"{expected[ID]}.{column}={_native(actual.get(column))!r} != {value!r}")
        v.add(
            f"roundtrip/{label}",
            0,
            len(mismatches),
            f"{wanted.height} sampled" + (f"; {mismatches[:3]}" if mismatches else ""),
        )

    for rel, frame in tables.edges.items():
        if frame.height == 0 or "tx_id" not in frame.columns:
            continue
        step = max(1, frame.height // sample)
        wanted = frame.gather(range(0, frame.height, step)[:sample])
        rows = _run(
            driver,
            database,
            f"UNWIND $ids AS id MATCH (a)-[r:{_quote(rel)}]->(b) WHERE r.tx_id = id "
            "RETURN r {.*} AS r, a.id AS source, b.id AS target",
            ids=wanted["tx_id"].to_list(),
        )
        got = {row["r"]["tx_id"]: row for row in rows}
        mismatches = []
        for expected in wanted.to_dicts():
            actual = got.get(expected["tx_id"])
            if actual is None:
                mismatches.append(f"{expected['tx_id']} missing")
                continue
            for column, value in expected.items():
                if value is None:
                    continue
                found = actual[column] if column in (SOURCE, TARGET) else actual["r"].get(column)
                if not _equal(value, _native(found)):
                    mismatches.append(f"{expected['tx_id']}.{column}={_native(found)!r} != {value!r}")
        v.add(
            f"roundtrip/{rel}",
            0,
            len(mismatches),
            f"{wanted.height} sampled" + (f"; {mismatches[:3]}" if mismatches else ""),
        )


# -------------------------------------------------------------------- entry


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
    v = Verification(database=target.database)
    try:
        _check_structure(driver, target.database, tables, v, truth)
        _check_nodes(driver, target.database, tables, v)
        _check_edges(driver, target.database, tables, v)
        if truth:
            _check_truth(driver, target.database, tables, truth, v)
        if sample:
            _check_round_trip(driver, target.database, tables, v, sample)
        return v
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
    return verify_tables(
        tables,
        target,
        read_truth(root) if truth else None,
        sample=sample,
        driver=driver,
    )
