"""DuckDB sink: the dataset's tables as they are, plus a SQL/PGQ property graph.

A generated dataset is already a set of tables, and DuckDB reads Parquet in
place, so this is the sink that needs no graph database at all. The tables
land as tables (``Account``, ``TRANSFERS``, ...), one ``CREATE PROPERTY
GRAPH`` statement declares which are vertices and which are edges, and the
DuckPGQ community extension answers graph pattern queries in SQL/PGQ, the
ISO SQL:2023 graph syntax::

    FROM GRAPH_TABLE (fraud
      MATCH (a:Account)-[t:TRANSFERS]->(b:Account)
      WHERE t.amount > 9000
      COLUMNS (a.id AS payer, b.id AS payee, t.amount))

Three ways in, one result:

* :func:`load_tables` loads in-memory tables (Arrow, no files).
* :func:`load_directory` loads a dataset on disk; DuckDB reads the Parquet
  files itself.
* ``load.sql``, written by :func:`write_duckdb`, is the same load as a script
  for the DuckDB shell or any other client.

The ground truth lands the way it does in LadybugDB: ``Pattern`` is a vertex
table, ``IN_PATTERN`` an edge table from ``Account`` to ``Pattern`` carrying
the role, and ``truth/transactions.parquet`` sets ``is_fraud``,
``pattern_id`` and ``typology`` on the money relationship it names. A blind
load leaves all of it out.

The property graph definition needs the extension (``INSTALL duckpgq FROM
community``). When it cannot be installed, the tables still load and verify;
the definition is in ``load.sql`` for later. DuckPGQ is built per DuckDB
release, so the ``duckdb`` extra pins a version it exists for.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

import polars as pl

from graphfaker.backends.tables import ID, SOURCE, TARGET, GraphTables
from graphfaker.logger import logger
from graphfaker.sinks.neo4j import infer_endpoints
from graphfaker.sinks.neo4j_live import LoadReport, read_truth
from graphfaker.sinks.verify import (
    DEFAULT_SAMPLE,
    MEMBER_REL,
    PATTERN_LABEL,
    Verification,
    aggregate_plan,
    verify,
)

#: Default property graph name when the dataset does not say.
DEFAULT_GRAPH = "graph"

#: The view name a statement's Arrow table is registered under while it runs.
SOURCE_VIEW = "gf_source"

_TYPES = {
    pl.String: "VARCHAR", pl.Utf8: "VARCHAR",
    pl.Int8: "BIGINT", pl.Int16: "BIGINT", pl.Int32: "BIGINT", pl.Int64: "BIGINT",
    pl.UInt8: "BIGINT", pl.UInt16: "BIGINT", pl.UInt32: "BIGINT", pl.UInt64: "BIGINT",
    pl.Float32: "DOUBLE", pl.Float64: "DOUBLE",
    pl.Boolean: "BOOLEAN", pl.Date: "DATE",
}

#: Columns the truth adds to a money relationship (one carrying ``tx_id``).
TRUTH_EDGE_COLUMNS = {"pattern_id": "VARCHAR", "typology": "VARCHAR", "is_fraud": "BOOLEAN"}

TRUTH_FRAMES = ("patterns", "accounts", "transactions")


def _type(dtype: pl.DataType) -> str:
    if isinstance(dtype, pl.Datetime):
        return "TIMESTAMP"
    if isinstance(dtype, pl.List):
        return _type(dtype.inner) + "[]"
    for candidate, name in _TYPES.items():
        if dtype == candidate:
            return name
    return "VARCHAR"


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _latent_frames(truth: dict[str, pl.DataFrame] | None) -> dict[str, pl.DataFrame]:
    if not truth:
        return {}
    return {name.title(): frame for name, frame in truth.items() if name not in TRUTH_FRAMES and "group" in frame.columns}


# --------------------------------------------------------------------- DDL


class _Source:
    """Where a statement reads its rows from: ``read_parquet`` of a file when
    rendering ``load.sql`` or loading a directory, an Arrow table registered
    as a view when loading from memory."""

    def __init__(self, root: Path | None):
        self.root = root

    def __call__(self, frame: pl.DataFrame, *parts: str) -> tuple[str, dict[str, Any]]:
        if self.root is not None:
            return f"read_parquet({_literal((self.root / Path(*parts)).resolve().as_posix())})", {}
        return SOURCE_VIEW, {SOURCE_VIEW: frame.to_arrow()}


def _create(table: str, columns: list[str], key: str | None = None) -> str:
    spec = list(columns)
    if key is not None:
        spec.append(f"PRIMARY KEY ({_quote(key)})")
    return f"CREATE TABLE {_quote(table)} ({', '.join(spec)});"


def statements(
    tables: GraphTables,
    truth: dict[str, pl.DataFrame] | None = None,
    data_dir: str | Path | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """The table DDL and load statements, each with its Arrow parameters.

    With ``data_dir`` the statements read Parquet files under it; without,
    they read the frames in memory. The property graph is a separate
    statement, :func:`property_graph`, because it needs the extension.
    """
    source = _Source(Path(data_dir) if data_dir is not None else None)
    endpoints = infer_endpoints(tables)
    has_tx_truth = bool(truth) and truth.get("transactions") is not None and truth["transactions"].height > 0
    out: list[tuple[str, dict[str, Any]]] = []

    for node_type, frame in tables.nodes.items():
        columns = [f"{_quote(c)} {_type(t)}" for c, t in frame.schema.items()]
        out.append((_create(node_type, columns, key=ID), {}))
    for rel, frame in tables.edges.items():
        if rel not in endpoints:
            continue
        columns = [f"{_quote(c)} {_type(t)}" for c, t in frame.schema.items()]
        if has_tx_truth and "tx_id" in frame.columns:
            columns += [f"{_quote(c)} {t}" for c, t in TRUTH_EDGE_COLUMNS.items()]
        out.append((_create(rel, columns), {}))

    if truth:
        patterns = truth.get("patterns")
        if patterns is not None:
            columns = [f"{_quote(ID if c == 'pattern_id' else c)} {_type(t)}" for c, t in patterns.schema.items()]
            out.append((_create(PATTERN_LABEL, columns, key=ID), {}))
        members = truth.get("accounts")
        if members is not None:
            columns = [f"{_quote(SOURCE)} VARCHAR", f"{_quote(TARGET)} VARCHAR"]
            columns += [f"{_quote(c)} {_type(t)}" for c, t in members.schema.items() if c not in ("account_id", "pattern_id")]
            out.append((_create(MEMBER_REL, columns), {}))
        for label, frame in _latent_frames(truth).items():
            columns = [f"{_quote(ID)} VARCHAR"] + [f"{_quote(c)} {_type(t)}" for c, t in frame.schema.items()]
            out.append((_create(label, columns, key=ID), {}))

    for node_type, frame in tables.nodes.items():
        src_sql, params = source(frame, "nodes", node_type + ".parquet")
        out.append((f"INSERT INTO {_quote(node_type)} BY NAME SELECT * FROM {src_sql};", params))
    for rel, frame in tables.edges.items():
        if rel not in endpoints:
            continue
        src_sql, params = source(frame, "edges", rel + ".parquet")
        out.append((f"INSERT INTO {_quote(rel)} BY NAME SELECT * FROM {src_sql};", params))

    if truth:
        out += _truth_statements(source, tables, truth)
    return out


def _truth_statements(source: _Source, tables: GraphTables, truth: dict[str, pl.DataFrame]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    patterns = truth.get("patterns")
    if patterns is not None and patterns.height:
        src_sql, params = source(patterns, "truth", "patterns.parquet")
        out.append((
            f"INSERT INTO {_quote(PATTERN_LABEL)} BY NAME SELECT * RENAME ({_quote('pattern_id')} AS {_quote(ID)}) FROM {src_sql};",
            params,
        ))
    members = truth.get("accounts")
    if members is not None and members.height:
        src_sql, params = source(members, "truth", "accounts.parquet")
        out.append((
            f"INSERT INTO {_quote(MEMBER_REL)} BY NAME SELECT * RENAME "
            f"({_quote('account_id')} AS {_quote(SOURCE)}, {_quote('pattern_id')} AS {_quote(TARGET)}) FROM {src_sql};",
            params,
        ))
    transactions = truth.get("transactions")
    if transactions is not None and transactions.height:
        columns = [c for c in TRUTH_EDGE_COLUMNS if c in transactions.columns]
        sets = ", ".join(f"{_quote(c)} = t.{_quote(c)}" for c in columns)
        for rel, frame in tables.edges.items():
            if "tx_id" not in frame.columns:
                continue
            src_sql, params = source(transactions, "truth", "transactions.parquet")
            out.append((
                f"UPDATE {_quote(rel)} SET {sets} FROM {src_sql} AS t WHERE {_quote(rel)}.{_quote('tx_id')} = t.{_quote('tx_id')};",
                params,
            ))
    for label, frame in _latent_frames(truth).items():
        name = label.lower()
        src_sql, params = source(frame, "truth", name + ".parquet")
        out.append((
            f"INSERT INTO {_quote(label)} BY NAME SELECT {_literal(name + '_')} || CAST({_quote('group')} AS VARCHAR) AS {_quote(ID)}, * FROM {src_sql};",
            params,
        ))
    return out


def property_graph(tables: GraphTables, truth: dict[str, pl.DataFrame] | None = None, graph: str = DEFAULT_GRAPH) -> str:
    """The ``CREATE PROPERTY GRAPH`` statement: node tables as vertex tables,
    relationship tables as edge tables keyed on ``source`` and ``target``."""
    endpoints = infer_endpoints(tables)
    vertices = list(tables.nodes)
    edges = [(rel, *endpoints[rel]) for rel in tables.edges if rel in endpoints]
    if truth:
        if truth.get("patterns") is not None:
            vertices.append(PATTERN_LABEL)
        if truth.get("accounts") is not None:
            edges.append((MEMBER_REL, "Account", PATTERN_LABEL))
        vertices += list(_latent_frames(truth))
    edge_specs = [
        f"    {_quote(rel)} SOURCE KEY ({_quote(SOURCE)}) REFERENCES {_quote(src)} ({_quote(ID)}) "
        f"DESTINATION KEY ({_quote(TARGET)}) REFERENCES {_quote(dst)} ({_quote(ID)})"
        for rel, src, dst in edges
    ]
    return (
        f"CREATE OR REPLACE PROPERTY GRAPH {_quote(graph)}\n"
        f"  VERTEX TABLES ({', '.join(_quote(v) for v in vertices)})\n"
        f"  EDGE TABLES (\n" + ",\n".join(edge_specs) + "\n  );"
    )


def duckdb_script(
    tables: GraphTables,
    data_dir: str | Path,
    truth: dict[str, pl.DataFrame] | None = None,
    graph: str = DEFAULT_GRAPH,
) -> str:
    """The whole load as SQL reading Parquet under ``data_dir``: tables, then
    the extension and the property graph."""
    lines = [sql for sql, _ in statements(tables, truth, data_dir)]
    lines += ["INSTALL duckpgq FROM community;", "LOAD duckpgq;", property_graph(tables, truth, graph)]
    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------- load


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise ImportError("install DuckDB with `pip install \"graphfaker[duckdb]\"` to load a database") from exc
    return duckdb


def connect(db_path: str | Path):
    """A connection to the database file at ``db_path`` (``:memory:`` works too)."""
    return _duckdb().connect(str(db_path))


def execute(conn, plan: list[tuple[str, dict[str, Any]]]) -> None:
    for sql, params in plan:
        for name, table in params.items():
            conn.register(name, table)
        try:
            conn.execute(sql)
        finally:
            for name in params:
                conn.unregister(name)


def execute_script(conn, script: str) -> None:
    """Run a ``load.sql`` file's statements, one at a time."""
    for statement in script.split(";\n"):
        statement = statement.strip()
        if statement:
            conn.execute(statement + ";")


def ensure_extension(conn) -> bool:
    """Load DuckPGQ, installing it first when needed. ``False`` (with a
    warning) when the extension is not available for this DuckDB."""
    try:
        conn.execute("LOAD duckpgq")
        return True
    except Exception:
        pass
    try:
        conn.execute("INSTALL duckpgq FROM community")
        conn.execute("LOAD duckpgq")
        return True
    except Exception as exc:
        version = _duckdb().__version__
        logger.warning(
            "duckpgq is not available for DuckDB %s (%s); tables loaded, property graph not created. "
            "Pin the DuckDB version the extension is built for (see the duckdb extra).",
            version,
            str(exc).splitlines()[0],
        )
        return False


def create_property_graph(conn, tables: GraphTables, truth: dict[str, pl.DataFrame] | None, graph: str) -> bool:
    if not ensure_extension(conn):
        return False
    conn.execute(property_graph(tables, truth, graph))
    return True


def load_tables(
    tables: GraphTables,
    db_path: str | Path,
    truth: dict[str, pl.DataFrame] | None = None,
    *,
    graph: str = DEFAULT_GRAPH,
    conn=None,
    data_dir: str | Path | None = None,
) -> LoadReport:
    """Load tables (and ``truth``) into the database at ``db_path``, then
    declare the property graph ``graph``.

    From memory the frames go in as Arrow; with ``data_dir`` DuckDB reads
    the dataset's Parquet files directly instead.
    """
    started = time.perf_counter()
    conn = conn or connect(db_path)
    execute(conn, statements(tables, truth, data_dir))
    report = LoadReport(database=str(db_path))
    report.nodes, report.edges, report.truth = _table_counts(conn, tables, truth)
    if create_property_graph(conn, tables, truth, graph):
        logger.info("duckdb: property graph %r declared", graph)
    report.seconds = time.perf_counter() - started
    logger.info("duckdb: loaded %s in %.1fs", db_path, report.seconds)
    return report


def _table_counts(conn, tables: GraphTables, truth: dict[str, pl.DataFrame] | None) -> tuple[dict, dict, dict]:
    backend = DuckDBBackend(conn, "")
    nodes = {t: backend.count_nodes(t) for t in tables.nodes}
    edges = {r: backend.count_edges(r) for r in tables.edges}
    extra: dict[str, Any] = {}
    if truth:
        if truth.get("patterns") is not None:
            extra[PATTERN_LABEL] = backend.count_nodes(PATTERN_LABEL)
        if truth.get("accounts") is not None:
            extra[MEMBER_REL] = backend.count_edges(MEMBER_REL)
        if truth.get("transactions") is not None:
            for rel, frame in tables.edges.items():
                if "tx_id" in frame.columns:
                    extra[f"{rel}.is_fraud"] = backend.count_edges(rel, flag="is_fraud")
        for label in _latent_frames(truth):
            extra[label] = backend.count_nodes(label)
    return nodes, edges, extra


def write_duckdb(
    tables: GraphTables,
    data_dir: str | Path,
    db_path: str | Path | None = None,
    truth: dict[str, pl.DataFrame] | None = None,
    graph: str = DEFAULT_GRAPH,
) -> Path:
    """Write Parquet plus ``load.sql``; when ``db_path`` is given and DuckDB
    is installed, create the database and load it from memory.

    ``truth`` (a run's ``truth`` frames) loads the ground truth as well.
    Leave it ``None`` for a blind database.
    """
    root = Path(data_dir)
    tables.write_parquet(root)
    if truth:
        (root / "truth").mkdir(exist_ok=True)
        for name, frame in truth.items():
            frame.write_parquet(root / "truth" / f"{name}.parquet", use_pyarrow=True)
    (root / "load.sql").write_text(duckdb_script(tables, root, truth, graph), encoding="utf-8")
    if db_path is not None:
        load_tables(tables, db_path, truth, graph=graph)
    return root


def graph_name(directory: str | Path) -> str:
    """The property graph name for a dataset: its schema name from
    ``manifest.json`` when there is one."""
    manifest = Path(directory) / "manifest.json"
    if manifest.exists():
        try:
            return json.loads(manifest.read_text(encoding="utf-8")).get("schema_name") or DEFAULT_GRAPH
        except ValueError:
            pass
    return DEFAULT_GRAPH


def load_directory(
    directory: str | Path,
    db_path: str | Path,
    *,
    truth: bool = True,
    graph: str | None = None,
    wipe_first: bool = False,
) -> LoadReport:
    """Load a dataset written by ``graphfaker generate`` / ``graphfaker fraud``
    into a new database at ``db_path``. DuckDB reads the Parquet files itself.

    An existing database is refused unless ``wipe_first`` is set, because
    the tables would already exist.
    """
    root = Path(directory)
    target = Path(db_path)
    if target.exists():
        if not wipe_first:
            raise RuntimeError(f"{target} already exists; pass wipe_first=True (CLI: --wipe) to replace it")
        shutil.rmtree(target) if target.is_dir() else target.unlink()
        wal = target.with_name(target.name + ".wal")
        if wal.exists():
            wal.unlink()
    tables = GraphTables.read_parquet(root)
    return load_tables(tables, target, read_truth(root) if truth else None, graph=graph or graph_name(root), data_dir=root)


# ------------------------------------------------------------------ verify


class DuckDBBackend:
    """The :class:`~graphfaker.sinks.verify.Backend` for DuckDB, in SQL.

    Node and relationship tables are told apart by their columns: a table
    with ``source`` and ``target`` is a relationship. Primary keys are the
    constraint catalogue, and the endpoint check joins each relationship
    table to the node tables it should point at.
    """

    def __init__(self, conn, name: str):
        self.conn = conn
        self.name = name

    def run(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        cursor = self.conn.execute(sql, params or None)
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def quote(self, name: str) -> str:
        return _quote(name)

    def _tables(self) -> dict[str, set[str]]:
        rows = self.run(
            "SELECT table_name, list(column_name) AS columns FROM duckdb_columns() "
            "WHERE NOT internal AND table_name NOT LIKE '\\_\\_%' ESCAPE '\\' GROUP BY table_name"
        )
        return {row["table_name"]: set(row["columns"]) for row in rows}

    def count_nodes(self, label: str, *, flag: str | None = None) -> int:
        where = f" WHERE {_quote(flag)}" if flag else ""
        return self.run(f"SELECT count(*) AS n FROM {_quote(label)}{where}")[0]["n"]

    def count_edges(self, rel: str, *, flag: str | None = None, present: str | None = None) -> int:
        conditions = [_quote(flag)] if flag else []
        conditions += [f"{_quote(present)} IS NOT NULL"] if present else []
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        return self.run(f"SELECT count(*) AS n FROM {_quote(rel)}{where}")[0]["n"]

    def labels_in_use(self) -> list[str]:
        return [t for t, cols in self._tables().items() if not {SOURCE, TARGET} <= cols and self.count_nodes(t)]

    def types_in_use(self) -> list[str]:
        return [t for t, cols in self._tables().items() if {SOURCE, TARGET} <= cols and self.count_edges(t)]

    def constraints(self, labels: list[str]) -> set[str] | None:
        rows = self.run(
            "SELECT table_name FROM duckdb_constraints() "
            "WHERE constraint_type = 'PRIMARY KEY' AND constraint_column_names = [$id]",
            id=ID,
        )
        return {f"gf_{row['table_name']}_id" for row in rows}

    def _clauses(self, schema: dict[str, pl.DataType]) -> list[str]:
        return [f"{func}({_quote(column)}) AS {alias}" for alias, func, column in aggregate_plan(schema)]

    def node_aggregates(self, label: str, schema: dict[str, pl.DataType]) -> dict[str, Any]:
        clauses = ", ".join(["count(*) AS total", f"count(DISTINCT {_quote(ID)}) AS distinct_ids", *self._clauses(schema)])
        return self.run(f"SELECT {clauses} FROM {_quote(label)}")[0]

    def edge_aggregates(self, rel: str, schema: dict[str, pl.DataType]) -> dict[str, Any]:
        clauses = ", ".join(["count(*) AS total", *self._clauses(schema)])
        return self.run(f"SELECT {clauses} FROM {_quote(rel)}")[0]

    def duplicate_tx_ids(self, rel: str) -> int:
        return self.run(
            f"SELECT count(*) AS n FROM (SELECT tx_id FROM {_quote(rel)} GROUP BY tx_id HAVING count(*) > 1)"
        )[0]["n"]

    def endpoint_mismatches(self, rel: str, src: str, dst: str) -> int:
        return self.run(
            f"SELECT count(*) AS n FROM {_quote(rel)} r "
            f"LEFT JOIN {_quote(src)} a ON r.{_quote(SOURCE)} = a.{_quote(ID)} "
            f"LEFT JOIN {_quote(dst)} b ON r.{_quote(TARGET)} = b.{_quote(ID)} "
            f"WHERE a.{_quote(ID)} IS NULL OR b.{_quote(ID)} IS NULL"
        )[0]["n"]

    def membership(self) -> tuple[int, int]:
        row = self.run(
            f"SELECT count(*) AS rels, count(DISTINCT m.{_quote(SOURCE)}) AS accounts FROM {_quote(MEMBER_REL)} m "
            f"JOIN {_quote('Account')} a ON m.{_quote(SOURCE)} = a.{_quote(ID)} "
            f"JOIN {_quote(PATTERN_LABEL)} p ON m.{_quote(TARGET)} = p.{_quote(ID)}"
        )[0]
        return row["rels"], row["accounts"]

    def accounts_without_membership(self, ids: list[Any]) -> int:
        return self.run(
            f"SELECT count(*) AS n FROM {_quote('Account')} a WHERE a.{_quote(ID)} IN (SELECT unnest($ids)) "
            f"AND NOT EXISTS (SELECT 1 FROM {_quote(MEMBER_REL)} m WHERE m.{_quote(SOURCE)} = a.{_quote(ID)})",
            ids=ids,
        )[0]["n"]

    def fetch_nodes(self, label: str, ids: list[Any]) -> dict[Any, dict[str, Any]]:
        rows = self.run(f"SELECT * FROM {_quote(label)} WHERE {_quote(ID)} IN (SELECT unnest($ids))", ids=ids)
        return {row[ID]: row for row in rows}

    def fetch_edges(self, rel: str, tx_ids: list[Any]) -> dict[Any, dict[str, Any]]:
        rows = self.run(f"SELECT * FROM {_quote(rel)} WHERE tx_id IN (SELECT unnest($ids))", ids=tx_ids)
        return {row["tx_id"]: row for row in rows}


def verify_tables(
    tables: GraphTables,
    db_path: str | Path,
    truth: dict[str, pl.DataFrame] | None = None,
    *,
    sample: int = DEFAULT_SAMPLE,
    conn=None,
) -> Verification:
    """Compare the database at ``db_path`` against the tables it came from."""
    backend = DuckDBBackend(conn or connect(db_path), str(db_path))
    return verify(backend, tables, truth, sample=sample)


def verify_directory(
    directory: str | Path,
    db_path: str | Path,
    *,
    truth: bool = True,
    sample: int = DEFAULT_SAMPLE,
    conn=None,
) -> Verification:
    """Verify a database against the dataset directory that was loaded into
    it. ``truth=False`` for a blind load."""
    root = Path(directory)
    tables = GraphTables.read_parquet(root)
    return verify_tables(tables, db_path, read_truth(root) if truth else None, sample=sample, conn=conn)
