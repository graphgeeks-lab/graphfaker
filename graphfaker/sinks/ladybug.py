"""LadybugDB (formerly Kùzu) sink.

An embedded graph database with Cypher and ``COPY ... FROM`` Parquet is the
best zero-infrastructure place to land a generated graph: one ``pip install
ladybug``, one file, and the whole dataset is queryable. The sink writes the
DDL and COPY script from the tables' schema and, when the driver is
installed, runs it. Kùzu's API is identical, so ``kuzu`` is accepted too.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from graphfaker.backends.tables import ID, SOURCE, TARGET, GraphTables
from graphfaker.sinks.neo4j import infer_endpoints

_TYPES = {
    pl.String: "STRING", pl.Utf8: "STRING",
    pl.Int8: "INT64", pl.Int16: "INT64", pl.Int32: "INT64", pl.Int64: "INT64",
    pl.UInt8: "INT64", pl.UInt16: "INT64", pl.UInt32: "INT64", pl.UInt64: "INT64",
    pl.Float32: "DOUBLE", pl.Float64: "DOUBLE",
    pl.Boolean: "BOOLEAN", pl.Date: "DATE",
}


def _type(dtype: pl.DataType) -> str:
    if isinstance(dtype, pl.Datetime):
        return "TIMESTAMP"
    for candidate, name in _TYPES.items():
        if dtype == candidate:
            return name
    return "STRING"


def _quote(name: str) -> str:
    return f"`{name}`"


def ladybug_script(tables: GraphTables, data_dir: str | Path) -> str:
    """DDL and ``COPY`` statements for tables written with
    :meth:`GraphTables.write_parquet` to ``data_dir``."""
    root = Path(data_dir).resolve()
    endpoints = infer_endpoints(tables)
    lines: list[str] = []
    for node_type, frame in tables.nodes.items():
        columns = [f"{_quote(ID)} STRING PRIMARY KEY"]
        columns += [f"{_quote(c)} {_type(t)}" for c, t in frame.schema.items() if c != ID]
        lines.append(f"CREATE NODE TABLE {node_type}({', '.join(columns)});")
    for rel, frame in tables.edges.items():
        if rel not in endpoints:
            continue
        src, dst = endpoints[rel]
        attrs = [f"{_quote(c)} {_type(t)}" for c, t in frame.schema.items() if c not in (SOURCE, TARGET)]
        spec = ", ".join([f"FROM {src} TO {dst}", *attrs])
        lines.append(f"CREATE REL TABLE {rel}({spec});")
    for node_type in tables.nodes:
        path = (root / "nodes" / f"{node_type}.parquet").as_posix()
        lines.append(f'COPY {node_type} FROM "{path}";')
    for rel in tables.edges:
        if rel in endpoints:
            path = (root / "edges" / f"{rel}.parquet").as_posix()
            lines.append(f'COPY {rel} FROM "{path}";')
    return "\n".join(lines) + "\n"


def _driver():
    for name in ("ladybug", "kuzu"):
        try:
            return __import__(name)
        except ImportError:
            continue
    raise ImportError("install the driver with `pip install ladybug` (or `kuzu`) to load a database")


def write_ladybug(tables: GraphTables, data_dir: str | Path, db_path: str | Path | None = None) -> Path:
    """Write Parquet plus ``load.cypher``; when ``db_path`` is given and a
    driver is installed, create the database and run the script."""
    root = Path(data_dir)
    tables.write_parquet(root)
    script = ladybug_script(tables, root)
    (root / "load.cypher").write_text(script, encoding="utf-8")
    if db_path is not None:
        lb = _driver()
        conn = lb.Connection(lb.Database(str(db_path)))
        for statement in script.split(";\n"):
            if statement.strip():
                conn.execute(statement.strip() + ";")
    return root
