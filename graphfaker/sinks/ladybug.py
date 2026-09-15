"""LadybugDB (formerly Kùzu) sink: load, ground truth, verification.

An embedded graph database with Cypher and Parquet scanning is the best
zero-infrastructure place to land a generated graph: one ``pip install``,
one file, and the whole dataset is queryable. This module writes the DDL and
load script from the tables' schema, runs it when a driver is installed
(``ladybug`` or ``kuzu``; the API is the same), loads a dataset that is
already on disk, and verifies the result with the same checks the Neo4j
sink uses.

Data goes in as Arrow. The database accepts an in-memory Arrow table as a
statement parameter (``COPY Account FROM $df``), and a Polars frame is Arrow
memory already, so loading never serialises anything: no CSV, no staging
files, no row-by-row inserts. ``load.cypher`` is also written, with file
paths in place of the parameters, so the same load can be reproduced by
hand from the dataset directory alone. The ground truth is loaded the same
way with ``LOAD FROM``:

* ``truth/patterns.parquet`` becomes a ``Pattern`` node table.
* ``truth/accounts.parquet`` becomes ``IN_PATTERN`` relationships from
  ``Account`` to ``Pattern`` carrying the role, because an account can be in
  several patterns.
* ``truth/transactions.parquet`` sets ``is_fraud``, ``pattern_id`` and
  ``typology`` on the money relationship it names. Those columns exist only
  when the truth is loaded, so a blind database has nothing to leak.
* Any other truth frame with a ``group`` column is a latent factor and
  becomes a small node table named after the frame (``Region``).
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

import polars as pl

from graphfaker.backends.tables import ID, SOURCE, TARGET, GraphTables
from graphfaker.logger import logger
from graphfaker.sinks.neo4j import infer_endpoints
from graphfaker.sinks.neo4j_live import LoadReport, read_truth
from graphfaker.sinks.verify import DEFAULT_SAMPLE, MEMBER_REL, PATTERN_LABEL, Verification, verify

_TYPES = {
    pl.String: "STRING", pl.Utf8: "STRING",
    pl.Int8: "INT64", pl.Int16: "INT64", pl.Int32: "INT64", pl.Int64: "INT64",
    pl.UInt8: "INT64", pl.UInt16: "INT64", pl.UInt32: "INT64", pl.UInt64: "INT64",
    pl.Float32: "DOUBLE", pl.Float64: "DOUBLE",
    pl.Boolean: "BOOLEAN", pl.Date: "DATE",
}

#: Columns the truth adds to a money relationship (one carrying ``tx_id``).
TRUTH_EDGE_COLUMNS = {"pattern_id": "STRING", "typology": "STRING", "is_fraud": "BOOLEAN"}


def _type(dtype: pl.DataType) -> str:
    if isinstance(dtype, pl.Datetime):
        return "TIMESTAMP"
    if isinstance(dtype, pl.List):
        return _type(dtype.inner) + "[]"
    for candidate, name in _TYPES.items():
        if dtype == candidate:
            return name
    return "STRING"


def _quote(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _path(root: Path, *parts: str) -> str:
    return (root / Path(*parts)).resolve().as_posix()


# --------------------------------------------------------------------- DDL


def _latent_frames(truth: dict[str, pl.DataFrame] | None) -> dict[str, pl.DataFrame]:
    if not truth:
        return {}
    return {
        name.title(): frame
        for name, frame in truth.items()
        if name not in ("patterns", "accounts", "transactions") and "group" in frame.columns
    }


class _Source:
    """Where a statement reads its rows from: a file path when rendering
    ``load.cypher``, an Arrow table bound as ``$df`` when loading."""

    def __init__(self, root: Path | None):
        self.root = root

    def __call__(self, frame: pl.DataFrame, *parts: str) -> tuple[str, dict[str, Any]]:
        if self.root is not None:
            return f'"{_path(self.root, *parts)}"', {}
        return "$df", {"df": frame.to_arrow()}


def statements(
    tables: GraphTables,
    truth: dict[str, pl.DataFrame] | None = None,
    data_dir: str | Path | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """The DDL and load statements, each with its parameters.

    With ``data_dir`` the statements read Parquet files under it; without,
    they read the frames in memory (Arrow, zero copy).
    """
    source = _Source(Path(data_dir) if data_dir is not None else None)
    endpoints = infer_endpoints(tables)
    has_tx_truth = bool(truth) and truth.get("transactions") is not None and truth["transactions"].height > 0
    out: list[tuple[str, dict[str, Any]]] = []

    for node_type, frame in tables.nodes.items():
        columns = [f"{_quote(ID)} STRING PRIMARY KEY"]
        columns += [f"{_quote(c)} {_type(t)}" for c, t in frame.schema.items() if c != ID]
        out.append((f"CREATE NODE TABLE {_quote(node_type)}({', '.join(columns)});", {}))
    for rel, frame in tables.edges.items():
        if rel not in endpoints:
            continue
        src, dst = endpoints[rel]
        attrs = [f"{_quote(c)} {_type(t)}" for c, t in frame.schema.items() if c not in (SOURCE, TARGET)]
        if has_tx_truth and "tx_id" in frame.columns:
            attrs += [f"{_quote(c)} {t}" for c, t in TRUTH_EDGE_COLUMNS.items()]
        out.append((f"CREATE REL TABLE {_quote(rel)}({', '.join([f'FROM {_quote(src)} TO {_quote(dst)}', *attrs])});", {}))

    if truth:
        patterns = truth.get("patterns")
        if patterns is not None:
            columns = [f"{_quote(ID)} STRING PRIMARY KEY"]
            columns += [f"{_quote(c)} {_type(t)}" for c, t in patterns.schema.items() if c != "pattern_id"]
            out.append((f"CREATE NODE TABLE {_quote(PATTERN_LABEL)}({', '.join(columns)});", {}))
        members = truth.get("accounts")
        if members is not None:
            attrs = [f"{_quote(c)} {_type(t)}" for c, t in members.schema.items() if c not in ("account_id", "pattern_id")]
            spec = ", ".join([f"FROM {_quote('Account')} TO {_quote(PATTERN_LABEL)}", *attrs])
            out.append((f"CREATE REL TABLE {_quote(MEMBER_REL)}({spec});", {}))
        for label, frame in _latent_frames(truth).items():
            columns = [f"{_quote(ID)} STRING PRIMARY KEY"] + [f"{_quote(c)} {_type(t)}" for c, t in frame.schema.items()]
            out.append((f"CREATE NODE TABLE {_quote(label)}({', '.join(columns)});", {}))

    for node_type, frame in tables.nodes.items():
        src_sql, params = source(frame, "nodes", node_type + ".parquet")
        out.append((f"COPY {_quote(node_type)} FROM {src_sql};", params))
    for rel, frame in tables.edges.items():
        if rel not in endpoints:
            continue
        src_sql, params = source(frame, "edges", rel + ".parquet")
        if has_tx_truth and "tx_id" in frame.columns:
            # The table has more columns than the data; name the property
            # columns the data fills (the first two columns are always the
            # endpoints and are not listed).
            file_columns = ", ".join(_quote(c) for c in frame.columns if c not in (SOURCE, TARGET))
            out.append((f"COPY {_quote(rel)}({file_columns}) FROM {src_sql};", params))
        else:
            out.append((f"COPY {_quote(rel)} FROM {src_sql};", params))

    if truth:
        out += _truth_statements(source, tables, truth)
    return out


def ladybug_script(tables: GraphTables, data_dir: str | Path, truth: dict[str, pl.DataFrame] | None = None) -> str:
    """The load as a Cypher script reading Parquet under ``data_dir``, for
    running by hand or from another tool."""
    return "\n".join(cypher for cypher, _ in statements(tables, truth, data_dir)) + "\n"


def _truth_statements(source: _Source, tables: GraphTables, truth: dict[str, pl.DataFrame]) -> list[tuple[str, dict[str, Any]]]:
    """``LOAD FROM`` the truth frames into the truth subgraph."""
    out: list[tuple[str, dict[str, Any]]] = []
    patterns = truth.get("patterns")
    if patterns is not None and patterns.height:
        props = ", ".join(f"{_quote(ID if c == 'pattern_id' else c)}: {_quote(c)}" for c in patterns.columns)
        src_sql, params = source(patterns, "truth", "patterns.parquet")
        out.append((f"LOAD FROM {src_sql} CREATE (:{_quote(PATTERN_LABEL)} {{{props}}});", params))
    members = truth.get("accounts")
    if members is not None and members.height:
        attrs = [c for c in members.columns if c not in ("account_id", "pattern_id")]
        props = ", ".join(f"{_quote(c)}: {_quote(c)}" for c in attrs)
        src_sql, params = source(members, "truth", "accounts.parquet")
        out.append((
            f"LOAD FROM {src_sql} "
            f"MATCH (a:{_quote('Account')} {{{_quote(ID)}: {_quote('account_id')}}}), "
            f"(p:{_quote(PATTERN_LABEL)} {{{_quote(ID)}: {_quote('pattern_id')}}}) "
            f"CREATE (a)-[:{_quote(MEMBER_REL)} {{{props}}}]->(p);",
            params,
        ))
    transactions = truth.get("transactions")
    if transactions is not None and transactions.height:
        sets = ", ".join(f"r.{_quote(c)} = {_quote(c)}" for c in TRUTH_EDGE_COLUMNS if c in transactions.columns)
        for rel, frame in tables.edges.items():
            if "tx_id" not in frame.columns:
                continue
            src_sql, params = source(transactions, "truth", "transactions.parquet")
            out.append((
                f"LOAD FROM {src_sql} MATCH ()-[r:{_quote(rel)}]->() WHERE r.{_quote('tx_id')} = {_quote('tx_id')} SET {sets};",
                params,
            ))
    for label, frame in _latent_frames(truth).items():
        name = label.lower()
        props = ", ".join([f"{_quote(ID)}: '{name}_' + cast({_quote('group')} AS STRING)"] + [f"{_quote(c)}: {_quote(c)}" for c in frame.columns])
        src_sql, params = source(frame, "truth", name + ".parquet")
        out.append((f"LOAD FROM {src_sql} CREATE (:{_quote(label)} {{{props}}});", params))
    return out


# -------------------------------------------------------------------- load


def _driver():
    for name in ("ladybug", "kuzu"):
        try:
            return __import__(name)
        except ImportError:
            continue
    raise ImportError("install the driver with `pip install ladybug` (or `kuzu`) to load a database")


def connect(db_path: str | Path):
    """A connection to the database at ``db_path``, created if absent."""
    lb = _driver()
    return lb.Connection(lb.Database(str(db_path)))


def execute_script(conn, script: str) -> None:
    """Run a ``load.cypher`` file's statements."""
    for statement in script.split(";\n"):
        statement = statement.strip()
        if statement:
            conn.execute(statement + ";")


def execute(conn, plan: list[tuple[str, dict[str, Any]]]) -> None:
    for cypher, params in plan:
        conn.execute(cypher, parameters=params) if params else conn.execute(cypher)


def load_tables(
    tables: GraphTables,
    db_path: str | Path,
    truth: dict[str, pl.DataFrame] | None = None,
    *,
    conn=None,
) -> LoadReport:
    """Load in-memory tables (and ``truth``) into the database at ``db_path``
    straight from Arrow, without writing files."""
    started = time.perf_counter()
    conn = conn or connect(db_path)
    execute(conn, statements(tables, truth))
    report = LoadReport(database=str(db_path))
    report.nodes, report.edges, report.truth = _table_counts(conn, tables, truth)
    report.seconds = time.perf_counter() - started
    logger.info("ladybug: loaded %s in %.1fs", db_path, report.seconds)
    return report


def _table_counts(conn, tables: GraphTables, truth: dict[str, pl.DataFrame] | None) -> tuple[dict, dict, dict]:
    backend = LadybugBackend(conn, "")
    nodes = {t: backend.count_nodes(t) for t in tables.nodes}
    edges = {r: backend.count_edges(r) for r in tables.edges}
    extra: dict[str, int] = {}
    if truth:
        if truth.get("patterns") is not None:
            extra[PATTERN_LABEL] = backend.count_nodes(PATTERN_LABEL)
        if truth.get("accounts") is not None:
            extra[MEMBER_REL] = backend.count_edges(MEMBER_REL)
        if truth.get("transactions") is not None:
            for rel, frame in tables.edges.items():
                if "tx_id" in frame.columns:
                    extra[f"{rel}.is_fraud"] = backend.run(
                        f"MATCH ()-[r:{_quote(rel)}]->() WHERE r.is_fraud RETURN count(r) AS n"
                    )[0]["n"]
        for label in _latent_frames(truth):
            extra[label] = backend.count_nodes(label)
    return nodes, edges, extra


def write_ladybug(
    tables: GraphTables,
    data_dir: str | Path,
    db_path: str | Path | None = None,
    truth: dict[str, pl.DataFrame] | None = None,
) -> Path:
    """Write Parquet plus ``load.cypher``; when ``db_path`` is given and a
    driver is installed, create the database and load it from memory.

    ``truth`` (a run's ``truth`` frames) loads the ground truth subgraph as
    well. Leave it ``None`` for a blind database.
    """
    root = Path(data_dir)
    tables.write_parquet(root)
    if truth:
        (root / "truth").mkdir(exist_ok=True)
        for name, frame in truth.items():
            frame.write_parquet(root / "truth" / f"{name}.parquet", use_pyarrow=True)
    (root / "load.cypher").write_text(ladybug_script(tables, root, truth), encoding="utf-8")
    if db_path is not None:
        load_tables(tables, db_path, truth)
    return root


def load_directory(
    directory: str | Path,
    db_path: str | Path,
    *,
    truth: bool = True,
    wipe_first: bool = False,
) -> LoadReport:
    """Load a dataset written by ``graphfaker generate`` / ``graphfaker fraud``
    into a new database at ``db_path``.

    An existing database is refused unless ``wipe_first`` is set, because
    the tables would already exist and a second load would either fail
    halfway or double every row.
    """
    root = Path(directory)
    target = Path(db_path)
    if target.exists():
        if not wipe_first:
            raise RuntimeError(f"{target} already exists; pass wipe_first=True (CLI: --wipe) to replace it")
        shutil.rmtree(target) if target.is_dir() else target.unlink()
        for side in (target.with_name(target.name + ".wal"), target.with_name(target.name + ".lock")):
            if side.exists():
                side.unlink()
    tables = GraphTables.read_parquet(root)
    return load_tables(tables, target, read_truth(root) if truth else None)


# ------------------------------------------------------------------ verify


def _strip_internal(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if not k.startswith("_")}


class LadybugBackend:
    """The :class:`~graphfaker.sinks.verify.Backend` for LadybugDB / Kùzu.

    Primary keys and relationship endpoint types are part of the table
    definitions here, so two of the Neo4j checks are answered by the schema:
    there is no constraint catalogue to consult, and an edge cannot join the
    wrong node tables.
    """

    def __init__(self, conn, name: str):
        self.conn = conn
        self.name = name

    def run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        result = self.conn.execute(cypher, parameters=params) if params else self.conn.execute(cypher)
        columns = result.get_column_names()
        rows = []
        while result.has_next():
            rows.append(dict(zip(columns, result.get_next())))
        return rows

    def quote(self, name: str) -> str:
        return _quote(name)

    def _tables(self, kind: str) -> list[str]:
        rows = self.run("CALL show_tables() RETURN *")
        return [row["name"] for row in rows if str(row.get("type", "")).upper() == kind]

    def count_nodes(self, label: str) -> int:
        return self.run(f"MATCH (n:{_quote(label)}) RETURN count(n) AS n")[0]["n"]

    def count_edges(self, rel: str) -> int:
        return self.run(f"MATCH ()-[r:{_quote(rel)}]->() RETURN count(r) AS n")[0]["n"]

    def labels_in_use(self) -> list[str]:
        return [t for t in self._tables("NODE") if self.count_nodes(t)]

    def types_in_use(self) -> list[str]:
        return [t for t in self._tables("REL") if self.count_edges(t)]

    def constraints(self, labels: list[str]) -> set[str] | None:
        return None

    def endpoint_mismatches(self, rel: str, src: str, dst: str) -> int:
        return 0

    def fetch_nodes(self, label: str, ids: list[Any]) -> dict[Any, dict[str, Any]]:
        rows = self.run(f"UNWIND $ids AS id MATCH (n:{_quote(label)} {{{_quote(ID)}: id}}) RETURN n", ids=ids)
        return {row["n"][ID]: _strip_internal(row["n"]) for row in rows}

    def fetch_edges(self, rel: str, tx_ids: list[Any]) -> dict[Any, dict[str, Any]]:
        rows = self.run(
            f"UNWIND $ids AS id MATCH (a)-[r:{_quote(rel)}]->(b) WHERE r.{_quote('tx_id')} = id "
            f"RETURN r, a.{_quote(ID)} AS source, b.{_quote(ID)} AS target",
            ids=tx_ids,
        )
        return {row["r"]["tx_id"]: {**_strip_internal(row["r"]), "source": row["source"], "target": row["target"]} for row in rows}


def verify_tables(
    tables: GraphTables,
    db_path: str | Path,
    truth: dict[str, pl.DataFrame] | None = None,
    *,
    sample: int = DEFAULT_SAMPLE,
    conn=None,
) -> Verification:
    """Compare the database at ``db_path`` against the tables it came from."""
    backend = LadybugBackend(conn or connect(db_path), str(db_path))
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
