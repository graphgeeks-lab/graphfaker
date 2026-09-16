"""PyTorch Geometric export: a ``HeteroData`` with features, labels and splits.

A generated graph is a set of typed node and relationship tables, which is
exactly what PyG's ``HeteroData`` holds: one node store per node type, one
edge store per ``(source type, relationship, target type)``. This module
turns the tables into tensors and puts the ground truth where a training
loop expects it.

Features (``x``), per node type, one float column each:

* numeric columns as they are, standardised (mean 0, sd 1) unless
  ``standardize=False``;
* booleans as 0/1;
* dates and timestamps as days since the earliest value in the column;
* strings with at most ``max_categories`` distinct values, each used more
  than once on average, one-hot encoded;
* everything else (ids, names, emails, free text, foreign keys) left out.

Latent factor columns (``region``, ``community``: whatever the truth carries
a frame for) are the hidden variables the generator used, so they are kept
out of ``x`` and attached as their own tensor (``data["Person"].community``)
for use as a label, not a feature.

Labels, when ``truth`` is given:

* the node type the truth's ``accounts`` frame refers to gets ``y`` (1 for a
  node in a fraud pattern, 0 otherwise), ``decoy`` (1 for a node that is in
  a legitimate look-alike pattern only), and stratified ``train_mask``,
  ``val_mask`` and ``test_mask``;
* every relationship with a ``tx_id`` gets ``y`` (1 for an injected fraud
  transaction) and ``edge_time`` in seconds, for temporal splits.

Feature names are kept in ``data[type].feature_names`` so a column can be
found again after training, and the standardisation statistics in
``data[type].feature_stats``.

The tensors are built with numpy and only wrapped in torch at the end, so
:func:`arrays` can be tested and used without PyTorch installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from graphfaker.backends.tables import ID, SOURCE, TARGET, GraphTables
from graphfaker.logger import logger
from graphfaker.sinks.neo4j import infer_endpoints
from graphfaker.sinks.neo4j_live import read_truth

DEFAULT_SPLIT = (0.6, 0.2, 0.2)
DEFAULT_MAX_CATEGORIES = 64

_NUMERIC = (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64, pl.Float32, pl.Float64)


@dataclass
class NodeArrays:
    ids: np.ndarray
    x: np.ndarray
    feature_names: list[str]
    feature_stats: dict[str, tuple[float, float]] = field(default_factory=dict)
    latent: dict[str, np.ndarray] = field(default_factory=dict)
    y: np.ndarray | None = None
    decoy: np.ndarray | None = None
    masks: dict[str, np.ndarray] = field(default_factory=dict)


@dataclass
class EdgeArrays:
    src_type: str
    dst_type: str
    edge_index: np.ndarray
    edge_attr: np.ndarray | None
    feature_names: list[str]
    edge_time: np.ndarray | None = None
    y: np.ndarray | None = None


@dataclass
class GraphArrays:
    """The export before torch: numpy arrays keyed like the ``HeteroData``."""

    nodes: dict[str, NodeArrays]
    edges: dict[tuple[str, str, str], EdgeArrays]

    def summary(self) -> str:
        lines = []
        for name, node in self.nodes.items():
            extra = f", y={int(node.y.sum())} positive" if node.y is not None else ""
            lines.append(f"  {name}: {len(node.ids):,} nodes, {node.x.shape[1]} features{extra}")
        for (src, rel, dst), edge in self.edges.items():
            extra = f", y={int(edge.y.sum())} positive" if edge.y is not None else ""
            attrs = edge.edge_attr.shape[1] if edge.edge_attr is not None else 0
            lines.append(f"  ({src})-[{rel}]->({dst}): {edge.edge_index.shape[1]:,} edges, {attrs} attributes{extra}")
        return "\n".join(lines)


# ---------------------------------------------------------------- features


def _is_numeric(dtype: pl.DataType) -> bool:
    return any(dtype == t for t in _NUMERIC)


def _is_temporal(dtype: pl.DataType) -> bool:
    return dtype == pl.Date or isinstance(dtype, pl.Datetime)


def _days(series: pl.Series) -> np.ndarray:
    """Days since the earliest value; nulls become the column's mean."""
    values = series.cast(pl.Datetime("us")).dt.epoch("s").cast(pl.Float64) / 86400.0
    values = values - values.min()
    return values.fill_null(values.mean()).to_numpy().astype(np.float32)


def encode_features(
    frame: pl.DataFrame,
    *,
    exclude: set[str],
    max_categories: int = DEFAULT_MAX_CATEGORIES,
    standardize: bool = True,
) -> tuple[np.ndarray, list[str], dict[str, tuple[float, float]]]:
    """``(x, feature_names, stats)`` for one table, following the rules in
    the module docstring."""
    columns: list[np.ndarray] = []
    names: list[str] = []
    stats: dict[str, tuple[float, float]] = {}
    for column, dtype in frame.schema.items():
        if column in exclude:
            continue
        series = frame[column]
        if dtype == pl.Boolean:
            columns.append(series.fill_null(False).cast(pl.Float32).to_numpy())
            names.append(column)
        elif _is_numeric(dtype):
            values = series.cast(pl.Float64)
            values = values.fill_null(values.mean() if values.null_count() < len(values) else 0.0).to_numpy().astype(np.float32)
            if standardize:
                mean, sd = float(values.mean()), float(values.std())
                sd = sd if sd > 0 else 1.0
                values = (values - mean) / sd
                stats[column] = (mean, sd)
            columns.append(values)
            names.append(column)
        elif _is_temporal(dtype):
            values = _days(series)
            if standardize:
                mean, sd = float(values.mean()), float(values.std())
                sd = sd if sd > 0 else 1.0
                values = (values - mean) / sd
                stats[column] = (mean, sd)
            columns.append(values)
            names.append(column)
        elif dtype in (pl.String, pl.Utf8, pl.Categorical):
            # a category is a value that repeats; a column that is nearly
            # unique (names, emails, IBANs) is an identifier, whatever its size
            non_null = series.drop_nulls()
            categories = non_null.unique().sort().to_list()
            if 1 < len(categories) <= max_categories and len(categories) <= 0.5 * len(non_null):
                codes = series.cast(pl.String).to_numpy()
                for category in categories:
                    columns.append((codes == category).astype(np.float32))
                    names.append(f"{column}={category}")
    if not columns:
        return np.zeros((frame.height, 1), dtype=np.float32), ["constant"], stats
    return np.stack(columns, axis=1), names, stats


def _latent_names(truth: dict[str, pl.DataFrame] | None) -> set[str]:
    if not truth:
        return set()
    return {name for name, frame in truth.items() if name not in ("patterns", "accounts", "transactions") and "group" in frame.columns}


def _labelled_node_type(tables: GraphTables, members: pl.DataFrame) -> str | None:
    """Which node table the truth's ``accounts`` frame refers to."""
    wanted = set(members["account_id"].to_list()[:100])
    for name, frame in tables.nodes.items():
        if wanted and wanted <= set(frame[ID].to_list()):
            return name
    return None


def _split_masks(y: np.ndarray, split: tuple[float, float, float], seed: int) -> dict[str, np.ndarray]:
    """Stratified train/val/test masks: each class is divided in the same
    proportions, so a rare positive class is present in every part."""
    rng = np.random.default_rng(seed)
    masks = {name: np.zeros(len(y), dtype=bool) for name in ("train_mask", "val_mask", "test_mask")}
    for value in np.unique(y):
        index = np.flatnonzero(y == value)
        rng.shuffle(index)
        n_train = round(len(index) * split[0])
        n_val = round(len(index) * split[1])
        masks["train_mask"][index[:n_train]] = True
        masks["val_mask"][index[n_train : n_train + n_val]] = True
        masks["test_mask"][index[n_train + n_val :]] = True
    return masks


# ------------------------------------------------------------------ arrays


def arrays(
    tables: GraphTables,
    truth: dict[str, pl.DataFrame] | None = None,
    *,
    seed: int = 0,
    split: tuple[float, float, float] = DEFAULT_SPLIT,
    max_categories: int = DEFAULT_MAX_CATEGORIES,
    standardize: bool = True,
    exclude: dict[str, list[str]] | None = None,
) -> GraphArrays:
    """Features, edge indices, labels and masks as numpy arrays.

    ``exclude`` names columns to leave out of ``x`` per node or relationship
    type (``{"Customer": ["kyc_tier"]}``) on top of the defaults.
    """
    if abs(sum(split) - 1.0) > 1e-9:
        raise ValueError(f"split must sum to 1, got {split}")
    exclude = exclude or {}
    latent = _latent_names(truth)
    endpoints = infer_endpoints(tables)

    nodes: dict[str, NodeArrays] = {}
    positions: dict[str, dict[Any, int]] = {}
    all_ids = _all_ids(tables)
    for name, frame in tables.nodes.items():
        skip = {ID, *latent, *exclude.get(name, [])}
        # foreign keys are structure, and structure is the edges
        skip |= {c for c, t in frame.schema.items() if c != ID and t in (pl.String, pl.Utf8) and _is_foreign_key(frame[c], all_ids)}
        x, names, stats = encode_features(frame, exclude=skip, max_categories=max_categories, standardize=standardize)
        ids = frame[ID].to_numpy()
        positions[name] = {v: i for i, v in enumerate(ids.tolist())}
        node = NodeArrays(ids=ids, x=x, feature_names=names, feature_stats=stats)
        for column in latent:
            if column in frame.columns:
                node.latent[column] = frame[column].to_numpy().astype(np.int64)
        nodes[name] = node

    edges: dict[tuple[str, str, str], EdgeArrays] = {}
    tx_truth = truth.get("transactions") if truth else None
    for rel, frame in tables.edges.items():
        if rel not in endpoints or frame.height == 0:
            continue
        src, dst = endpoints[rel]
        src_pos, dst_pos = positions[src], positions[dst]
        edge_index = np.stack(
            [
                np.fromiter((src_pos[v] for v in frame[SOURCE].to_list()), dtype=np.int64, count=frame.height),
                np.fromiter((dst_pos[v] for v in frame[TARGET].to_list()), dtype=np.int64, count=frame.height),
            ]
        )
        skip = {SOURCE, TARGET, "tx_id", *exclude.get(rel, [])}
        attrs = frame.drop([c for c in (SOURCE, TARGET) if c in frame.columns])
        x, names, _ = encode_features(attrs, exclude=skip, max_categories=max_categories, standardize=standardize)
        edge = EdgeArrays(src_type=src, dst_type=dst, edge_index=edge_index, edge_attr=x if names != ["constant"] else None, feature_names=names if names != ["constant"] else [])
        if "timestamp" in frame.columns:
            edge.edge_time = frame["timestamp"].cast(pl.Datetime("us")).dt.epoch("s").to_numpy().astype(np.int64)
        if tx_truth is not None and "tx_id" in frame.columns:
            fraud_ids = tx_truth.filter(pl.col("is_fraud"))["tx_id"].unique()
            edge.y = frame["tx_id"].is_in(fraud_ids.implode()).to_numpy().astype(np.int64)
        edges[(src, rel, dst)] = edge

    members = truth.get("accounts") if truth else None
    if members is not None and members.height:
        label_type = _labelled_node_type(tables, members)
        if label_type is None:
            logger.warning("pyg: the truth's accounts match no node table; no node labels written")
        else:
            node = nodes[label_type]
            fraud_ids = set(members.filter(pl.col("is_fraud"))["account_id"].to_list())
            decoy_ids = set(members.filter(~pl.col("is_fraud"))["account_id"].to_list()) - fraud_ids
            ids = node.ids.tolist()
            node.y = np.fromiter((1 if v in fraud_ids else 0 for v in ids), dtype=np.int64, count=len(ids))
            node.decoy = np.fromiter((1 if v in decoy_ids else 0 for v in ids), dtype=np.int64, count=len(ids))
            node.masks = _split_masks(node.y, split, seed)

    return GraphArrays(nodes=nodes, edges=edges)


def _all_ids(tables: GraphTables) -> pl.Series:
    return pl.concat([frame[ID] for frame in tables.nodes.values()]) if tables.nodes else pl.Series([], dtype=pl.String)


def _is_foreign_key(series: pl.Series, all_ids: pl.Series) -> bool:
    values = series.drop_nulls()
    return len(values) > 0 and bool(values.is_in(all_ids.implode()).all())


# ------------------------------------------------------------------- torch


def _torch():
    try:
        import torch
        from torch_geometric.data import HeteroData
    except ImportError as exc:
        raise ImportError("install PyTorch Geometric with `pip install \"graphfaker[pyg]\"` to build a HeteroData") from exc
    return torch, HeteroData


def to_hetero_data(
    tables: GraphTables,
    truth: dict[str, pl.DataFrame] | None = None,
    **options: Any,
):
    """A ``torch_geometric.data.HeteroData`` built from :func:`arrays`.

    Node stores carry ``x``, ``node_id`` (the dataset's string ids, as a
    list), ``feature_names``, ``feature_stats``, any latent factor tensor, and
    ``y``/``decoy``/masks on the labelled type. Edge stores carry
    ``edge_index``, ``edge_attr`` when there are attributes, ``edge_time``
    when there are timestamps, and ``y`` on transaction relationships.
    """
    torch, HeteroData = _torch()
    built = arrays(tables, truth, **options)
    data = HeteroData()
    for name, node in built.nodes.items():
        store = data[name]
        store.x = torch.from_numpy(np.ascontiguousarray(node.x))
        store.num_nodes = len(node.ids)
        store.node_id = node.ids.tolist()
        store.feature_names = node.feature_names
        store.feature_stats = node.feature_stats
        for column, values in node.latent.items():
            setattr(store, column, torch.from_numpy(values))
        if node.y is not None:
            store.y = torch.from_numpy(node.y)
            store.decoy = torch.from_numpy(node.decoy)
            for mask, values in node.masks.items():
                setattr(store, mask, torch.from_numpy(values))
    for key, edge in built.edges.items():
        store = data[key]
        store.edge_index = torch.from_numpy(np.ascontiguousarray(edge.edge_index))
        if edge.edge_attr is not None:
            store.edge_attr = torch.from_numpy(np.ascontiguousarray(edge.edge_attr))
            store.feature_names = edge.feature_names
        if edge.edge_time is not None:
            store.edge_time = torch.from_numpy(edge.edge_time)
        if edge.y is not None:
            store.y = torch.from_numpy(edge.y)
    return data


def write_pyg(
    tables: GraphTables,
    path: str | Path,
    truth: dict[str, pl.DataFrame] | None = None,
    **options: Any,
) -> Path:
    """Build the ``HeteroData`` and ``torch.save`` it to ``path`` (``.pt``).
    Load it back with ``torch.load(path, weights_only=False)``."""
    torch, _ = _torch()
    data = to_hetero_data(tables, truth, **options)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    torch.save(data, target)
    logger.info("pyg: wrote %s", target)
    return target


def from_directory(directory: str | Path, *, truth: bool = True, **options: Any):
    """``HeteroData`` from a dataset written by ``graphfaker generate``."""
    root = Path(directory)
    return to_hetero_data(GraphTables.read_parquet(root), read_truth(root) if truth else None, **options)
