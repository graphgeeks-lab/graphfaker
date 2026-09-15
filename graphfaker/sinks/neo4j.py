"""Neo4j admin-import files from graph tables.

``neo4j-admin database import`` is the only way to load a large graph into
Neo4j quickly; it wants one CSV per label and per relationship type with
typed headers. This sink writes those and the command to run them.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import polars as pl

from graphfaker.backends.tables import ID, SOURCE, TARGET, GraphTables

_TYPE_HINTS = {
    pl.Int8: "int", pl.Int16: "int", pl.Int32: "int", pl.Int64: "long",
    pl.UInt8: "int", pl.UInt16: "int", pl.UInt32: "int", pl.UInt64: "long",
    pl.Float32: "float", pl.Float64: "double",
    pl.Boolean: "boolean", pl.Date: "date", pl.Datetime: "datetime",
}


def infer_endpoints(tables: GraphTables) -> dict[str, tuple[str, str]]:
    """Source and target node type of every relationship, found by looking
    the first row's ids up in the node tables. Relationships in generated
    graphs have one endpoint type each; a mixed one falls back to the first
    match."""
    id_sets = {name: set(frame[ID].to_list()) for name, frame in tables.nodes.items()}

    def type_of(value: str) -> str:
        for name, ids in id_sets.items():
            if value in ids:
                return name
        raise KeyError(f"id {value!r} is in no node table")

    endpoints = {}
    for rel, frame in tables.edges.items():
        if frame.height == 0:
            continue
        endpoints[rel] = (type_of(frame[SOURCE][0]), type_of(frame[TARGET][0]))
    return endpoints


def _header(frame: pl.DataFrame, skip: set[str]) -> list[str]:
    columns = []
    for name, dtype in frame.schema.items():
        if name in skip:
            continue
        hint = next((h for t, h in _TYPE_HINTS.items() if dtype == t or isinstance(dtype, t)), None)
        columns.append(f"{name}:{hint}" if hint else name)
    return columns


def _stringify(frame: pl.DataFrame) -> pl.DataFrame:
    """Neo4j's importer parses its own types; datetimes need ISO text."""
    exprs = []
    for name, dtype in frame.schema.items():
        if isinstance(dtype, pl.Datetime):
            exprs.append(pl.col(name).dt.strftime("%Y-%m-%dT%H:%M:%S").alias(name))
        elif dtype == pl.List:
            exprs.append(pl.col(name).list.join(";").alias(name))
    return frame.with_columns(exprs) if exprs else frame


def write_neo4j_admin(tables: GraphTables, directory: str | Path, database: str = "graphfaker") -> Path:
    """Write ``nodes_<Type>.csv``, ``rels_<REL>.csv`` and ``import.sh``."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    endpoints = infer_endpoints(tables)
    node_args, rel_args = [], []

    for node_type, frame in tables.nodes.items():
        header = [f"{ID}:ID({node_type})", *_header(frame, {ID}), ":LABEL"]
        out = _stringify(frame)
        body = out.select([ID, *[c for c in out.columns if c != ID]]).with_columns(pl.lit(node_type).alias(":LABEL"))
        path = root / f"nodes_{node_type}.csv"
        body.rename(dict(zip(body.columns, header))).write_csv(path)
        node_args.append(f"--nodes={node_type}={path.name}")

    for rel, frame in tables.edges.items():
        if rel not in endpoints:
            continue
        src_type, dst_type = endpoints[rel]
        attrs = [c for c in frame.columns if c not in (SOURCE, TARGET)]
        header = [f":START_ID({src_type})", f":END_ID({dst_type})", *_header(frame.select(attrs), set()), ":TYPE"]
        out = _stringify(frame)
        body = out.select([SOURCE, TARGET, *attrs]).with_columns(pl.lit(rel).alias(":TYPE"))
        path = root / f"rels_{rel}.csv"
        body.rename(dict(zip(body.columns, header))).write_csv(path)
        rel_args.append(f"--relationships={rel}={path.name}")

    command = " \\\n  ".join(
        ["neo4j-admin database import full", *node_args, *rel_args, shlex.quote(database)]
    )
    (root / "import.sh").write_text(f"#!/bin/sh\n# run from this directory, with the database stopped\n{command}\n", encoding="utf-8")
    return root
