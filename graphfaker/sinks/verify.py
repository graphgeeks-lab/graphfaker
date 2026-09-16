"""Check that what is in a graph database is the dataset that was generated.

A load that reports no error is not a load that is correct. Rows get dropped
when a relationship's endpoint is missing, properties vanish when a column
name collides with a keyword, a ``double`` silently becomes a string, a
re-run doubles every edge, a timestamp loses its time. None of that raises.
It just quietly gives you a different graph than the one whose ground truth
you are about to trust.

So the dataset on disk is the oracle and the database is the thing under
test. Each check states what Parquet says, asks the database the same
question, and reports both. The families, cheapest first:

* **cardinality**: a count per label and per relationship type, and no
  labels or types beyond the expected ones.
* **structure**: constraints (or primary keys) in place, no duplicate keys,
  and every relationship between the endpoint labels its type joins.
* **content**: one aggregate scan per label and type: how many rows carry
  each property, and the sum, min and max of every numeric and temporal
  column. This is what catches coercion and truncation, which counts cannot.
* **round trip**: a sample of rows fetched back and compared property by
  property against Parquet, which catches everything the aggregates hide.

Plus the truth subgraph, when it was loaded.

The checks are written once, against :class:`~graphfaker.backends.tables.GraphTables`,
and run through a :class:`Backend`, which answers a dozen questions (how
many rows carry a label, the aggregates of a table, the edges whose
endpoints are wrong, a node fetched back as a dict) in whatever language the
store speaks. :class:`CypherBackend` answers them in Cypher and is what the
Neo4j and LadybugDB adapters inherit; the DuckDB adapter answers them in
SQL. A new sink gets verification for the price of that adapter.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any, Protocol

import polars as pl

from graphfaker.backends.tables import ID, SOURCE, TARGET, GraphTables
from graphfaker.sinks.neo4j import infer_endpoints

PATTERN_LABEL = "Pattern"
MEMBER_REL = "IN_PATTERN"

#: Rows per label fetched back for the round-trip check.
DEFAULT_SAMPLE = 25

#: Relative tolerance for float aggregates. Doubles round-trip exactly, but
#: a sum of 600k of them depends on summation order.
TOLERANCE = 1e-9

_NUMERIC = (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64, pl.Float32, pl.Float64)


class Backend(Protocol):
    """What a store must answer for the checks to run against it.

    ``schema`` arguments are a table's Polars schema (column -> dtype); the
    aggregate methods return one row keyed like :func:`aggregate_plan`
    aliases plus ``total`` (and ``distinct_ids`` for nodes).
    """

    #: Shown in the summary, e.g. ``database 'fraud'`` or ``bank.lbdb``.
    name: str

    def labels_in_use(self) -> list[str]:
        """Node labels (tables) that actually hold rows."""
        ...

    def types_in_use(self) -> list[str]:
        """Relationship types (tables) that actually hold rows."""
        ...

    def constraints(self, labels: list[str]) -> set[str] | None:
        """Names of uniqueness constraints present for ``labels``, or ``None``
        when the store has no such catalogue (keys are part of the schema)."""
        ...

    def count_nodes(self, label: str, *, flag: str | None = None) -> int:
        """Nodes with ``label``; only those whose boolean ``flag`` is true when given."""
        ...

    def count_edges(self, rel: str, *, flag: str | None = None, present: str | None = None) -> int:
        """Relationships of type ``rel``; only those whose ``flag`` is true or
        whose ``present`` property is not null when given."""
        ...

    def node_aggregates(self, label: str, schema: dict[str, pl.DataType]) -> dict[str, Any]: ...

    def edge_aggregates(self, rel: str, schema: dict[str, pl.DataType]) -> dict[str, Any]: ...

    def duplicate_tx_ids(self, rel: str) -> int:
        """``tx_id`` values carried by more than one relationship of type ``rel``."""
        ...

    def endpoint_mismatches(self, rel: str, src: str, dst: str) -> int:
        """Relationships of type ``rel`` not joining ``src`` to ``dst``."""
        ...

    def membership(self) -> tuple[int, int]:
        """(``IN_PATTERN`` relationships, distinct accounts carrying one)."""
        ...

    def accounts_without_membership(self, ids: list[Any]) -> int:
        """How many of the accounts with these ids have no ``IN_PATTERN``."""
        ...

    def fetch_nodes(self, label: str, ids: list[Any]) -> dict[Any, dict[str, Any]]:
        """Property maps of the nodes with these ids, keyed by id."""
        ...

    def fetch_edges(self, rel: str, tx_ids: list[Any]) -> dict[Any, dict[str, Any]]:
        """Property maps of the edges with these ``tx_id``s, keyed by ``tx_id``,
        each including ``source`` and ``target`` ids."""
        ...


class CypherBackend:
    """The questions above, asked in Cypher. Subclasses supply ``run``,
    ``quote`` and the catalogue methods (``labels_in_use``, ``types_in_use``,
    ``constraints``, ``endpoint_mismatches``, ``fetch_nodes``, ``fetch_edges``)."""

    name: str

    def run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        raise NotImplementedError

    def quote(self, name: str) -> str:
        raise NotImplementedError

    def count_nodes(self, label: str, *, flag: str | None = None) -> int:
        q = self.quote
        where = f" WHERE n.{q(flag)}" if flag else ""
        return self.run(f"MATCH (n:{q(label)}){where} RETURN count(n) AS n")[0]["n"]

    def count_edges(self, rel: str, *, flag: str | None = None, present: str | None = None) -> int:
        q = self.quote
        conditions = [f"r.{q(flag)}"] if flag else []
        conditions += [f"r.{q(present)} IS NOT NULL"] if present else []
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        return self.run(f"MATCH ()-[r:{q(rel)}]->(){where} RETURN count(r) AS n")[0]["n"]

    def _clauses(self, variable: str, schema: dict[str, pl.DataType]) -> list[str]:
        return [f"{func}({variable}.{self.quote(column)}) AS {alias}" for alias, func, column in aggregate_plan(schema)]

    def node_aggregates(self, label: str, schema: dict[str, pl.DataType]) -> dict[str, Any]:
        q = self.quote
        clauses = ", ".join(["count(n) AS total", f"count(DISTINCT n.{q(ID)}) AS distinct_ids", *self._clauses("n", schema)])
        return self.run(f"MATCH (n:{q(label)}) RETURN {clauses}")[0]

    def edge_aggregates(self, rel: str, schema: dict[str, pl.DataType]) -> dict[str, Any]:
        clauses = ", ".join(["count(r) AS total", *self._clauses("r", schema)])
        return self.run(f"MATCH ()-[r:{self.quote(rel)}]->() RETURN {clauses}")[0]

    def duplicate_tx_ids(self, rel: str) -> int:
        return self.run(
            f"MATCH ()-[r:{self.quote(rel)}]->() WITH r.tx_id AS k, count(*) AS c WHERE c > 1 RETURN count(*) AS n"
        )[0]["n"]

    def membership(self) -> tuple[int, int]:
        q = self.quote
        row = self.run(
            f"MATCH (a:Account)-[r:{q(MEMBER_REL)}]->(p:{q(PATTERN_LABEL)}) RETURN count(r) AS rels, count(DISTINCT a) AS accounts"
        )[0]
        return row["rels"], row["accounts"]

    def accounts_without_membership(self, ids: list[Any]) -> int:
        q = self.quote
        return self.run(
            f"UNWIND $ids AS id MATCH (a:Account {{id: id}}) "
            f"WHERE NOT EXISTS {{ MATCH (a)-[:{q(MEMBER_REL)}]->() }} RETURN count(a) AS n",
            ids=ids,
        )[0]["n"]


# ---------------------------------------------------------------- results


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
        head = f"{'PASS' if self.ok else 'FAIL'}: {passed}/{len(self.checks)} checks on {self.database}"
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


# ------------------------------------------------------------- aggregates


def _alias(kind: str, column: str) -> str:
    """Result aliases avoid punctuation so no dialect needs to quote them."""
    return f"{kind}__{column}"


def aggregate_plan(schema: dict[str, pl.DataType]) -> list[tuple[str, str, str]]:
    """``(alias, function, column)`` for the ``count``/``sum``/``min``/``max``
    a backend computes per column, all in one scan."""
    plan = []
    for column, dtype in schema.items():
        plan.append((_alias("present", column), "count", column))
        numeric = any(dtype == t for t in _NUMERIC)
        if numeric:
            plan.append((_alias("sum", column), "sum", column))
        if numeric or dtype == pl.Date or isinstance(dtype, pl.Datetime):
            plan.append((_alias("min", column), "min", column))
            plan.append((_alias("max", column), "max", column))
    return plan


def _expected_aggregates(frame: pl.DataFrame) -> dict[str, Any]:
    """The same aggregates, computed from Parquet, keyed like the aliases."""
    expected: dict[str, Any] = {}
    for column, dtype in frame.schema.items():
        series = frame[column]
        expected[_alias("present", column)] = frame.height - series.null_count()
        numeric = any(dtype == t for t in _NUMERIC)
        if numeric:
            expected[_alias("sum", column)] = series.sum()
        if numeric or dtype == pl.Date or isinstance(dtype, pl.Datetime):
            expected[_alias("min", column)] = series.min()
            expected[_alias("max", column)] = series.max()
    return expected


def _ask(v: Verification, name: str, question, *args: Any) -> Any:
    """``question(*args)``, or ``None`` with a failed check recorded.

    The aggregate scans are the expensive part and the part most likely to
    hit a server limit; a failure there is reported as a failed check rather
    than aborting the remaining checks."""
    try:
        return question(*args)
    except Exception as exc:
        v.checks.append(Check(name, ok=False, expected="query to run", actual=f"{type(exc).__name__}: {exc}"))
        return None


def truth_labels(truth: dict[str, pl.DataFrame] | None) -> set[str]:
    """Labels the truth subgraph adds: ``Pattern``, plus one per latent
    factor frame (``truth/region.parquet`` -> ``Region``)."""
    if not truth:
        return set()
    labels = {PATTERN_LABEL} if truth.get("patterns") is not None else set()
    for name, frame in truth.items():
        if name not in ("patterns", "accounts", "transactions") and "group" in frame.columns:
            labels.add(name.title())
    return labels


# ------------------------------------------------------------------- checks


def check_structure(backend: Backend, tables: GraphTables, v: Verification, truth: dict[str, pl.DataFrame] | None) -> None:
    extra = truth_labels(truth)
    labels = [*tables.nodes, *extra]
    present = backend.constraints(labels)
    if present is not None:
        expected = {f"gf_{label}_id" for label in labels}
        v.add("structure/constraints", sorted(expected), sorted(present & expected))

    v.add("structure/labels", sorted(set(tables.nodes) | extra), sorted(backend.labels_in_use()), "no labels beyond the schema")

    expected_types = {rel for rel, frame in tables.edges.items() if frame.height}
    if truth and truth.get("accounts") is not None:
        expected_types.add(MEMBER_REL)
    v.add("structure/relationship_types", sorted(expected_types), sorted(backend.types_in_use()))


def check_nodes(backend: Backend, tables: GraphTables, v: Verification) -> None:
    for label, frame in tables.nodes.items():
        row = _ask(v, f"content/{label}", backend.node_aggregates, label, frame.schema)
        if row is None:
            continue
        v.add(f"count/{label}", frame.height, row["total"])
        v.add(f"structure/{label}.unique_id", frame.height, row["distinct_ids"], "no duplicated nodes")
        for key, expected in _expected_aggregates(frame).items():
            v.add(f"content/{label}.{key.replace('__', ':')}", expected, _native(row[key]))


def check_edges(backend: Backend, tables: GraphTables, v: Verification) -> None:
    endpoints = infer_endpoints(tables)
    for rel, frame in tables.edges.items():
        if frame.height == 0:
            continue
        attrs = {c: t for c, t in frame.schema.items() if c not in (SOURCE, TARGET)}
        src, dst = endpoints[rel]
        row = _ask(v, f"content/{rel}", backend.edge_aggregates, rel, attrs)
        if row is None:
            continue
        v.add(f"count/{rel}", frame.height, row["total"])
        for key, expected in _expected_aggregates(frame.select(list(attrs))).items():
            v.add(f"content/{rel}.{key.replace('__', ':')}", expected, _native(row[key]))

        v.add(f"structure/{rel}.endpoints", 0, backend.endpoint_mismatches(rel, src, dst), f"every edge is ({src})->({dst})")

        if "tx_id" in attrs:
            name = f"structure/{rel}.unique_tx_id"
            dupes = _ask(v, name, backend.duplicate_tx_ids, rel)
            if dupes is not None:
                v.add(name, 0, dupes, "no transaction loaded twice")


def check_truth(backend: Backend, tables: GraphTables, truth: dict[str, pl.DataFrame], v: Verification) -> None:
    patterns = truth.get("patterns")
    if patterns is not None and patterns.height:
        v.add(f"truth/{PATTERN_LABEL}.count", patterns.height, backend.count_nodes(PATTERN_LABEL))
        fraud = backend.count_nodes(PATTERN_LABEL, flag="is_fraud")
        v.add(f"truth/{PATTERN_LABEL}.is_fraud", patterns.filter(pl.col("is_fraud")).height, fraud, "decoy patterns stay unflagged")

    members = truth.get("accounts")
    if members is not None and members.height:
        rels, accounts = backend.membership()
        v.add(f"truth/{MEMBER_REL}.count", members.height, rels, "one per membership")
        v.add(f"truth/{MEMBER_REL}.accounts", members["account_id"].n_unique(), accounts, "accounts can be in several patterns")
        # The membership must reach the account the truth names, not just any.
        missing = backend.accounts_without_membership(members["account_id"].unique().to_list())
        v.add(f"truth/{MEMBER_REL}.reachable", 0, missing, "every truth account has a membership")

    transactions = truth.get("transactions")
    if transactions is not None and transactions.height:
        for rel, edges in tables.edges.items():
            if "tx_id" not in edges.columns:
                continue
            expected = transactions.join(edges.select("tx_id"), on="tx_id", how="semi")
            v.add(f"truth/{rel}.labelled", expected.height, backend.count_edges(rel, present="pattern_id"))
            if expected.height:
                v.add(f"truth/{rel}.is_fraud", expected.filter(pl.col("is_fraud")).height, backend.count_edges(rel, flag="is_fraud"))

    for name, frame in truth.items():
        if name in ("patterns", "accounts", "transactions") or "group" not in frame.columns:
            continue
        label = name.title()
        v.add(f"truth/{label}.count", frame.height, backend.count_nodes(label))


def check_round_trip(backend: Backend, tables: GraphTables, v: Verification, sample: int) -> None:
    """Fetch rows back and compare every property, which is the only check
    that would notice a value being replaced by a different value of the
    same type."""
    for label, frame in tables.nodes.items():
        if frame.height == 0:
            continue
        step = max(1, frame.height // sample)
        wanted = frame.gather(range(0, frame.height, step)[:sample])
        got = backend.fetch_nodes(label, wanted[ID].to_list())
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
        v.add(f"roundtrip/{label}", 0, len(mismatches), f"{wanted.height} sampled" + (f"; {mismatches[:3]}" if mismatches else ""))

    for rel, frame in tables.edges.items():
        if frame.height == 0 or "tx_id" not in frame.columns:
            continue
        step = max(1, frame.height // sample)
        wanted = frame.gather(range(0, frame.height, step)[:sample])
        got = backend.fetch_edges(rel, wanted["tx_id"].to_list())
        mismatches = []
        for expected in wanted.to_dicts():
            actual = got.get(expected["tx_id"])
            if actual is None:
                mismatches.append(f"{expected['tx_id']} missing")
                continue
            for column, value in expected.items():
                if value is None:
                    continue
                if not _equal(value, _native(actual.get(column))):
                    mismatches.append(f"{expected['tx_id']}.{column}={_native(actual.get(column))!r} != {value!r}")
        v.add(f"roundtrip/{rel}", 0, len(mismatches), f"{wanted.height} sampled" + (f"; {mismatches[:3]}" if mismatches else ""))


# -------------------------------------------------------------------- entry


def verify(
    backend: Backend,
    tables: GraphTables,
    truth: dict[str, pl.DataFrame] | None = None,
    *,
    sample: int = DEFAULT_SAMPLE,
) -> Verification:
    """Run every family of check against ``backend``."""
    v = Verification(database=backend.name)
    check_structure(backend, tables, v, truth)
    check_nodes(backend, tables, v)
    check_edges(backend, tables, v)
    if truth:
        check_truth(backend, tables, truth, v)
    if sample:
        check_round_trip(backend, tables, v, sample)
    return v
