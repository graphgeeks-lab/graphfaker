"""Generate a graph from a schema.

``generate`` is the one entry point: schema in, :class:`GraphRun` out. A run
carries the tables, the ground truth the generator knows (latent group
membership, per-group parameters) and a manifest that pins down everything
needed to reproduce it byte for byte: schema digest, seed, shard size,
engine version.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
from collections.abc import Iterator
from concurrent.futures import Executor, ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from graphfaker.backends.tables import ID, GraphTables, write_parquet
from graphfaker.engine.sampling import (
    DEFAULT_SHARD_SIZE,
    GroupParams,
    sample_latent,
    sample_nodes,
)
from graphfaker.engine.seeding import Streams
from graphfaker.engine.topology import build_edges
from graphfaker.logger import logger
from graphfaker.schema.graph import GraphSchema

ENGINE_VERSION = "1"


class Manifest(BaseModel):
    """What was generated and how to generate it again."""

    model_config = ConfigDict(extra="forbid")

    schema_name: str
    schema_digest: str
    seed: int | None
    shard_size: int
    engine_version: str = ENGINE_VERSION
    graphfaker_version: str
    created_at: str
    node_counts: dict[str, int] = Field(default_factory=dict)
    edge_counts: dict[str, int] = Field(default_factory=dict)
    #: Domain-pack configuration and anything else needed to reproduce the
    #: run beyond the schema (e.g. ``{"fraud": FraudConfig.model_dump()}``).
    extra: dict[str, Any] = Field(default_factory=dict)

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return target

    @classmethod
    def read(cls, path: str | Path) -> Manifest:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


@dataclass
class GraphRun:
    schema: GraphSchema
    tables: GraphTables
    manifest: Manifest
    #: Ground truth: one frame per latent factor with ``group`` and its
    #: sampled parameters. Node membership is on the node tables themselves.
    truth: dict[str, pl.DataFrame] = field(default_factory=dict)

    def to_networkx(self) -> nx.DiGraph:
        return self.tables.to_networkx()

    def write(self, directory: str | Path) -> Path:
        """``nodes/``, ``edges/``, ``truth/``, ``schema.yaml``, ``manifest.json``."""
        root = Path(directory)
        self.tables.write_parquet(root)
        (root / "truth").mkdir(parents=True, exist_ok=True)
        for name, frame in self.truth.items():
            write_parquet(frame, root / "truth" / f"{name}.parquet")
        self.schema.to_yaml(root / "schema.yaml")
        self.manifest.write(root / "manifest.json")
        return root


@contextlib.contextmanager
def node_pool(workers: int) -> Iterator[Executor | None]:
    """One process pool for a whole run, or ``None`` for in-process."""
    if workers <= 1:
        yield None
        return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        yield pool


def _truth_frames(latent: dict[str, GroupParams]) -> dict[str, pl.DataFrame]:
    frames = {}
    for name, params in latent.items():
        rows = [{"group": group, **values} for group, values in enumerate(params.values)]
        frames[name] = pl.DataFrame(rows) if rows else pl.DataFrame({"group": []})
    return frames


def generate(
    schema: GraphSchema,
    seed: int | None = None,
    shard_size: int = DEFAULT_SHARD_SIZE,
    workers: int = 1,
) -> GraphRun:
    """Generate every node type in foreign-key order, then form edges.

    ``workers`` parallelises node sampling across processes without
    changing the result; ``shard_size`` does change it and is recorded.

    Streams are spawned in a fixed order (latent factors, then one child per
    node type in schema order, then one for edges) so adding a node type at
    the end of a schema does not change the ones before it.
    """
    from graphfaker import __version__

    root = Streams.root(seed)
    latent = sample_latent(schema, root)

    node_streams = dict(zip((node.name for node in schema.nodes), root.spawn(len(schema.nodes))))
    edge_streams = root.spawn(1)[0]

    tables: dict[str, pl.DataFrame] = {}
    with node_pool(workers) as executor:
        for node in schema.generation_order():
            tables[node.name] = sample_nodes(
                node, schema, latent, tables, node_streams[node.name], shard_size=shard_size, workers=workers, executor=executor
            )
            logger.debug("nodes: %s x %d", node.name, tables[node.name].height)

    # Preserve schema order in the output regardless of generation order.
    ordered = GraphTables(nodes={node.name: tables[node.name] for node in schema.nodes})
    built = build_edges(ordered, schema, edge_streams) if schema.edges else ordered

    manifest = Manifest(
        schema_name=schema.name,
        schema_digest=schema.digest(),
        seed=seed,
        shard_size=shard_size,
        graphfaker_version=__version__,
        created_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        node_counts={name: frame.height for name, frame in built.nodes.items()},
        edge_counts={name: frame.height for name, frame in built.edges.items()},
    )
    return GraphRun(schema=schema, tables=built, manifest=manifest, truth=_truth_frames(latent))


def fingerprint(run: GraphRun) -> str:
    """A digest of the generated content, for reproducibility checks.

    Tables are visited by name and rows sorted, so the digest depends on the
    content only: a run read back from Parquet (where table order follows
    the directory listing) fingerprints the same as the run that wrote it.
    """
    import hashlib

    digest = hashlib.sha256()
    for node_type in sorted(run.tables.nodes):
        digest.update(node_type.encode())
        digest.update(json.dumps(run.tables.nodes[node_type].sort(ID).rows(), default=str).encode())
    for rel in sorted(run.tables.edges):
        frame = run.tables.edges[rel]
        digest.update(rel.encode())
        digest.update(json.dumps(frame.sort(frame.columns).rows(), default=str).encode())
    return digest.hexdigest()[:16]
