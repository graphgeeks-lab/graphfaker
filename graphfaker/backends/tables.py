"""Columnar graph representation.

At scale a graph is a node table and an edge table, not an object graph:
100M edges is two integer columns plus attributes, it streams to Parquet, and
it is what every loader (Neo4j admin import, LadybugDB ``COPY``, Neptune
bulk, PyG tensors) wants. ``GraphTables`` is that representation. NetworkX
remains available as a view for graphs that fit, and is still where the
sequential realism models run.

The frames are Polars because generation and verification need expressions
and joins, but the interchange format is Arrow: a Polars frame is Arrow
memory, :meth:`GraphTables.to_arrow` hands it over without a copy, and
sinks that accept Arrow (LadybugDB's ``COPY ... FROM $df``) get it that way
rather than through a file.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
import polars as pl
import pyarrow as pa

ID = "id"
TYPE = "type"
SOURCE = "source"
TARGET = "target"
RELATIONSHIP = "relationship"


@dataclass
class GraphTables:
    """One node frame per node type, one edge frame per relationship.

    Node frames carry an ``id`` column plus attributes; the node type is the
    dict key. Edge frames carry ``source`` and ``target`` plus attributes; the
    relationship name is the dict key.
    """

    nodes: dict[str, pl.DataFrame] = field(default_factory=dict)
    edges: dict[str, pl.DataFrame] = field(default_factory=dict)

    # ---------------------------------------------------------------- counts

    @property
    def node_count(self) -> int:
        return sum(frame.height for frame in self.nodes.values())

    @property
    def edge_count(self) -> int:
        return sum(frame.height for frame in self.edges.values())

    def iter_nodes(self) -> Iterator[tuple[str, str, dict[str, Any]]]:
        for node_type, frame in self.nodes.items():
            for row in frame.iter_rows(named=True):
                node_id = row.pop(ID)
                yield node_type, node_id, row

    def iter_edges(self) -> Iterator[tuple[str, str, str, dict[str, Any]]]:
        for relationship, frame in self.edges.items():
            for row in frame.iter_rows(named=True):
                yield relationship, row.pop(SOURCE), row.pop(TARGET), row

    # -------------------------------------------------------------- networkx

    def to_networkx(self) -> nx.DiGraph:
        """Materialise as a directed graph with the attribute layout the rest of
        GraphFaker (export, resolve, metrics) already understands: ``type`` on
        nodes, ``relationship`` on edges. ``None`` attributes are dropped
        because GraphML cannot serialise them."""
        G = nx.DiGraph()
        for node_type, node_id, data in self.iter_nodes():
            G.add_node(node_id, type=node_type, **_drop_none(data))
        for relationship, source, target, data in self.iter_edges():
            G.add_edge(source, target, relationship=relationship, **_drop_none(data))
        return G

    @classmethod
    def from_networkx(cls, G: nx.Graph) -> GraphTables:
        by_type: dict[str, list[dict[str, Any]]] = {}
        for node_id, data in G.nodes(data=True):
            row = {ID: node_id, **{k: v for k, v in data.items() if k != TYPE}}
            by_type.setdefault(str(data.get(TYPE, "Node")), []).append(row)
        by_rel: dict[str, list[dict[str, Any]]] = {}
        for source, target, data in G.edges(data=True):
            row = {
                SOURCE: source,
                TARGET: target,
                **{k: v for k, v in data.items() if k != RELATIONSHIP},
            }
            by_rel.setdefault(str(data.get(RELATIONSHIP, "RELATED_TO")), []).append(row)
        return cls(
            nodes={t: _frame(rows) for t, rows in by_type.items()},
            edges={r: _frame(rows) for r, rows in by_rel.items()},
        )

    # ----------------------------------------------------------------- arrow

    def to_arrow(self) -> tuple[dict[str, pa.Table], dict[str, pa.Table]]:
        """Node and edge tables as ``pyarrow.Table``, zero copy."""
        return (
            {name: frame.to_arrow() for name, frame in self.nodes.items()},
            {name: frame.to_arrow() for name, frame in self.edges.items()},
        )

    @classmethod
    def from_arrow(cls, nodes: dict[str, pa.Table], edges: dict[str, pa.Table]) -> GraphTables:
        return cls(
            nodes={name: pl.from_arrow(table) for name, table in nodes.items()},
            edges={name: pl.from_arrow(table) for name, table in edges.items()},
        )

    # --------------------------------------------------------------- parquet

    def write_parquet(self, directory: str | Path) -> Path:
        """``<dir>/nodes/<Type>.parquet`` and ``<dir>/edges/<REL>.parquet``."""
        root = Path(directory)
        (root / "nodes").mkdir(parents=True, exist_ok=True)
        (root / "edges").mkdir(parents=True, exist_ok=True)
        for node_type, frame in self.nodes.items():
            write_parquet(frame, root / "nodes" / f"{node_type}.parquet")
        for relationship, frame in self.edges.items():
            write_parquet(frame, root / "edges" / f"{relationship}.parquet")
        return root

    @classmethod
    def read_parquet(cls, directory: str | Path) -> GraphTables:
        root = Path(directory)
        nodes = {
            path.stem: pl.read_parquet(path) for path in sorted((root / "nodes").glob("*.parquet"))
        }
        edges = {
            path.stem: pl.read_parquet(path) for path in sorted((root / "edges").glob("*.parquet"))
        }
        return cls(nodes=nodes, edges=edges)


def write_parquet(frame: pl.DataFrame, path: str | Path) -> None:
    """Write through pyarrow: its timestamp annotation is what third-party
    loaders (Kùzu/LadybugDB ``COPY``, Spark) read; polars' native writer
    produces one Kùzu rejects as INT64."""
    frame.write_parquet(path, use_pyarrow=True)


def _drop_none(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


def _frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    """Build a frame whose column order follows first appearance and whose
    missing cells are null, tolerating rows with different keys."""
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return pl.DataFrame(
        {column: [row.get(column) for row in rows] for column in columns},
        strict=False,
    )
