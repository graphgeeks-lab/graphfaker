"""Export graphs to formats other graph engines can load.

Rather than maintaining a live driver per database — which means a service to
authenticate against, a version matrix, and a test environment for each — this
module writes files that every engine's own loader already understands:

  - ``export_csv``          generic node/edge CSV, for pandas, Gephi, or any
                            bulk loader
  - ``export_neo4j_csv``    CSV with ``neo4j-admin database import`` headers
                            (``:ID``, ``:LABEL``, ``:START_ID``, ``:TYPE``)
  - ``export_cypher``       a runnable script for Neo4j, Memgraph, Kuzu,
                            Amazon Neptune (openCypher), or ISO GQL

The CSV output is also the path into TigerGraph (``LOAD``), Amazon Neptune
(bulk loader), and graph-data-science workflows, since all of them ingest
node/edge tables.

Nodes are typed by their ``type`` attribute and edges by ``relationship``,
matching what the GraphFaker fetchers produce. Both are configurable.

Example:
    >>> from graphfaker import GraphFaker
    >>> from graphfaker.export import export_cypher, export_csv
    >>> gf = GraphFaker(seed=42)
    >>> G = gf.generate_graph(source="faker", total_nodes=100)
    >>> export_csv(G, "nodes.csv", "edges.csv")
    >>> export_cypher(G, "load.cypher", dialect="neo4j")
"""

from __future__ import annotations

import csv
import json
import os
import re
from collections.abc import Hashable, Iterable, Sequence
from typing import Any

import networkx as nx

from graphfaker.logger import logger

__all__ = [
    "DIALECTS",
    "export_csv",
    "export_cypher",
    "export_neo4j_csv",
    "flatten_value",
]

#: Cypher-family dialects. ISO GQL (2024) uses INSERT where Cypher uses CREATE.
DIALECTS = ("neo4j", "opencypher", "gql")

_UNSAFE_IDENT = re.compile(r"[^0-9a-zA-Z_]")
_DEFAULT_LABEL = "Node"
_DEFAULT_REL_TYPE = "RELATED_TO"


# --------------------------------------------------------------------------- #
# value handling
# --------------------------------------------------------------------------- #


def flatten_value(value: Any) -> Any:
    """Reduce a value to something a tabular or query format can carry.

    Containers become strings — GraphML, CSV, and Cypher property values are all
    scalar-only. Coordinate tuples and the provenance that `resolve()` attaches
    to merged nodes both land here.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(flatten_value(item)) for item in value)
    if isinstance(value, dict):
        return "; ".join(
            f"{key}={flatten_value(val)}" for key, val in sorted(value.items(), key=str)
        )
    return str(value)


def _collect_fields(records: Iterable[dict[str, Any]], skip: Sequence[str] = ()) -> list[str]:
    """Union of keys across all records, in first-seen order.

    Taking the first record's keys — the obvious shortcut — silently corrupts
    heterogeneous graphs: a Person's header would be written and then a Place's
    values would be filed under it. Graph nodes of different types rarely share
    an attribute set, so the union is the only safe option.
    """
    skipped = set(skip)
    fields: list[str] = []
    seen = set()
    for record in records:
        for key in record:
            if key not in seen and key not in skipped:
                seen.add(key)
                fields.append(key)
    return fields


def _ensure_parent(path: str) -> str:
    absolute = os.path.abspath(path)
    parent = os.path.dirname(absolute)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return absolute


def _edge_records(G: nx.Graph) -> list[tuple[Hashable, Hashable, dict[str, Any]]]:
    """Edges as (source, target, data), flattening multigraph keys away."""
    if G.is_multigraph():
        return [(u, v, data) for u, v, _, data in G.edges(keys=True, data=True)]
    return list(G.edges(data=True))


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #


def export_csv(
    G: nx.Graph,
    nodes_path: str = "nodes.csv",
    edges_path: str = "edges.csv",
    encoding: str = "utf-8",
) -> tuple[str, str]:
    """Write the graph as two CSV files: one for nodes, one for edges.

    Every attribute seen on any node becomes a column; nodes missing it get an
    empty cell. This is what makes the output safe for graphs whose node types
    carry different attributes.

    Args:
        G: Graph to export.
        nodes_path: Destination for node rows. Gains an ``id`` column.
        edges_path: Destination for edge rows. Gains ``source`` and ``target``.
        encoding: Output encoding. UTF-8 by default, because generated names
            frequently contain non-ASCII characters and the platform default
            would fail on them.

    Returns:
        The absolute paths written, as ``(nodes_path, edges_path)``.
    """
    nodes_absolute = _ensure_parent(nodes_path)
    edges_absolute = _ensure_parent(edges_path)

    node_data = [data for _, data in G.nodes(data=True)]
    node_fields = _collect_fields(node_data, skip=("id",))
    with open(nodes_absolute, "w", newline="", encoding=encoding) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["id"] + node_fields, restval="", extrasaction="ignore"
        )
        writer.writeheader()
        for node, data in G.nodes(data=True):
            row = {key: flatten_value(value) for key, value in data.items()}
            row["id"] = node
            writer.writerow(row)

    edges = _edge_records(G)
    edge_fields = _collect_fields(
        (data for _, _, data in edges), skip=("source", "target")
    )
    with open(edges_absolute, "w", newline="", encoding=encoding) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["source", "target"] + edge_fields,
            restval="",
            extrasaction="ignore",
        )
        writer.writeheader()
        for source, target, data in edges:
            row = {key: flatten_value(value) for key, value in data.items()}
            row["source"] = source
            row["target"] = target
            writer.writerow(row)

    logger.info(
        "export_csv: %d nodes -> %s, %d edges -> %s",
        G.number_of_nodes(),
        nodes_absolute,
        len(edges),
        edges_absolute,
    )
    return nodes_absolute, edges_absolute


def export_neo4j_csv(
    G: nx.Graph,
    directory: str = "neo4j_import",
    type_attr: str = "type",
    relationship_attr: str = "relationship",
    encoding: str = "utf-8",
) -> dict[str, str]:
    """Write CSV shaped for ``neo4j-admin database import``.

    Differs from `export_csv` in the header row, which uses Neo4j's typed
    column syntax so the bulk importer assigns labels and relationship types
    rather than treating them as ordinary properties. This is the fast path for
    large graphs; `export_cypher` is friendlier for small ones.

    Returns:
        A dict with ``nodes``, ``edges``, and ``command`` keys — the last being
        the import command to run, which is also logged.
    """
    target = os.path.abspath(directory)
    os.makedirs(target, exist_ok=True)
    nodes_path = os.path.join(target, "nodes.csv")
    edges_path = os.path.join(target, "edges.csv")

    node_fields = _collect_fields(
        (data for _, data in G.nodes(data=True)), skip=(type_attr,)
    )
    with open(nodes_path, "w", newline="", encoding=encoding) as handle:
        writer = csv.writer(handle)
        writer.writerow(["id:ID"] + node_fields + [":LABEL"])
        for node, data in G.nodes(data=True):
            label = _safe_identifier(str(data.get(type_attr) or _DEFAULT_LABEL))
            writer.writerow(
                [node]
                + [flatten_value(data.get(field, "")) for field in node_fields]
                + [label]
            )

    edges = _edge_records(G)
    edge_fields = _collect_fields(
        (data for _, _, data in edges), skip=(relationship_attr,)
    )
    with open(edges_path, "w", newline="", encoding=encoding) as handle:
        writer = csv.writer(handle)
        writer.writerow([":START_ID"] + edge_fields + [":TYPE", ":END_ID"])
        for source, target, data in edges:
            rel = _safe_identifier(
                str(data.get(relationship_attr) or _DEFAULT_REL_TYPE), upper=True
            )
            writer.writerow(
                [source]
                + [flatten_value(data.get(field, "")) for field in edge_fields]
                + [rel, target]
            )

    command = (
        "neo4j-admin database import full "
        f"--nodes={nodes_path} --relationships={edges_path} "
        "--overwrite-destination neo4j"
    )
    logger.info("export_neo4j_csv: wrote %s and %s", nodes_path, edges_path)
    logger.info("export_neo4j_csv: import with -> %s", command)
    return {"nodes": nodes_path, "edges": edges_path, "command": command}


# --------------------------------------------------------------------------- #
# Cypher / GQL
# --------------------------------------------------------------------------- #


def _safe_identifier(raw: str, upper: bool = False, default: str = _DEFAULT_LABEL) -> str:
    """Make a string usable as a label or relationship type.

    Labels cannot contain punctuation or start with a digit unescaped, and
    generated data does produce such values.
    """
    # Only trailing underscores are trimmed. A leading one is significant:
    # stripping it rewrites the `_gf_id` property that the relationship MATCH
    # clauses look up, and every relationship then silently fails to create.
    cleaned = _UNSAFE_IDENT.sub("_", raw).rstrip("_")
    if not cleaned.strip("_"):
        cleaned = default
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned.upper() if upper else cleaned


def _cypher_literal(value: Any) -> str:
    """Render a Python value as a Cypher/GQL literal."""
    flat = flatten_value(value)
    if isinstance(flat, bool):
        return "true" if flat else "false"
    if isinstance(flat, (int, float)):
        return repr(flat)
    return json.dumps(str(flat), ensure_ascii=False)


def _properties(data: dict[str, Any], skip: Sequence[str] = ()) -> str:
    skipped = set(skip)
    items = [
        f"{_safe_identifier(key, default='prop')}: {_cypher_literal(value)}"
        for key, value in data.items()
        if key not in skipped
    ]
    return "{" + ", ".join(items) + "}" if items else "{}"


def export_cypher(
    G: nx.Graph,
    path: str = "graph.cypher",
    dialect: str = "neo4j",
    type_attr: str = "type",
    relationship_attr: str = "relationship",
    batch_size: int = 500,
    encoding: str = "utf-8",
) -> str:
    """Write a runnable script that recreates the graph.

    Args:
        G: Graph to export.
        path: Destination file.
        dialect: One of ``neo4j``, ``opencypher``, or ``gql``. Neo4j gets a
            uniqueness constraint so the relationship lookups are indexed;
            ISO GQL uses ``INSERT`` in place of ``CREATE``.
        type_attr: Node attribute supplying the label.
        relationship_attr: Edge attribute supplying the relationship type.
        batch_size: Statements between transaction boundaries. Neo4j Browser
            and cypher-shell both dislike one enormous transaction.

    Returns:
        The absolute path written.

    Note:
        Node ids are written as a ``_gf_id`` property and used to match
        endpoints when creating relationships, so importing into a database
        that already holds ``_gf_id`` values will connect to those instead.
    """
    if dialect not in DIALECTS:
        raise ValueError(f"dialect must be one of {DIALECTS}, got {dialect!r}")

    absolute = _ensure_parent(path)
    insert = "INSERT" if dialect == "gql" else "CREATE"
    edges = _edge_records(G)

    with open(absolute, "w", encoding=encoding) as handle:
        handle.write(f"// Generated by graphfaker ({dialect} dialect)\n")
        handle.write(f"// {G.number_of_nodes()} nodes, {len(edges)} relationships\n\n")

        if dialect == "neo4j":
            # Without this the MATCH pairs below degrade to full scans.
            handle.write(
                "CREATE CONSTRAINT gf_id IF NOT EXISTS\n"
                "FOR (n:__GraphFaker__) REQUIRE n._gf_id IS UNIQUE;\n\n"
            )

        for written, (node, data) in enumerate(G.nodes(data=True), start=1):
            label = _safe_identifier(str(data.get(type_attr) or _DEFAULT_LABEL))
            labels = f":{label}:__GraphFaker__" if dialect == "neo4j" else f":{label}"
            properties = _properties({"_gf_id": node, **data}, skip=(type_attr,))
            handle.write(f"{insert} (n{labels} {properties});\n")
            if batch_size and written % batch_size == 0:
                handle.write(":commit\n:begin\n" if dialect == "neo4j" else "\n")

        handle.write("\n")
        for source, target, data in edges:
            rel = _safe_identifier(
                str(data.get(relationship_attr) or _DEFAULT_REL_TYPE), upper=True
            )
            properties = _properties(data, skip=(relationship_attr,))
            handle.write(
                f"MATCH (a {{_gf_id: {_cypher_literal(source)}}}), "
                f"(b {{_gf_id: {_cypher_literal(target)}}}) "
                f"{insert} (a)-[r:{rel} {properties}]->(b);\n"
            )

    logger.info(
        "export_cypher: %d nodes and %d relationships -> %s (%s)",
        G.number_of_nodes(),
        len(edges),
        absolute,
        dialect,
    )
    return absolute
