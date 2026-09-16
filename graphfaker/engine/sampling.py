"""Turn samplers into columns.

Node generation is embarrassingly parallel once latent-group parameters are
fixed: each shard of a node type draws its rows from its own seeded stream
and never looks at another shard. The result is a function of the seed and
the shard size only (both are recorded in the manifest), so how many
workers execute the shards is a performance knob and not a semantic one.

Values are drawn row by row. Faker providers dominate the cost, so a
vectorised numeric path would not change the picture; sharding across
processes will, and it is what the shard boundary here is for.
"""

from __future__ import annotations

import contextlib
import math
import os
import pickle
import tempfile
from collections.abc import Iterator
from concurrent.futures import Executor, ProcessPoolExecutor
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import numpy as np
import polars as pl

from graphfaker.backends.tables import ID
from graphfaker.engine.seeding import Streams
from graphfaker.schema.graph import GraphSchema, LatentFactor, NodeType
from graphfaker.schema.samplers import (
    BernoulliSampler,
    CategorySampler,
    ConstantSampler,
    ExpressionSampler,
    FakerSampler,
    ForeignKeySampler,
    GaussianSampler,
    LognormalSampler,
    MixtureSampler,
    PoissonSampler,
    ReferenceSampler,
    SubcategorySampler,
    UniformSampler,
    is_ref,
    parse_ref,
)

DEFAULT_SHARD_SIZE = 10_000


@dataclass
class GroupParams:
    """Sampled parameters for every group of one latent factor."""

    factor: str
    values: list[dict[str, Any]]

    @property
    def groups(self) -> int:
        return len(self.values)


@dataclass
class RowContext:
    """Everything a sampler may look at while drawing one row."""

    streams: Streams
    #: factor name -> group index of every row in the shard
    group_ids: dict[str, np.ndarray] = field(default_factory=dict)
    latent: dict[str, GroupParams] = field(default_factory=dict)
    #: attributes drawn so far, column -> values for the shard
    columns: dict[str, list[Any]] = field(default_factory=dict)
    #: (node_type, factor|None) -> {group: [ids]} for foreign keys
    fk_index: dict[tuple[str, str | None], FKIndex] = field(default_factory=dict)

    def param(self, value: Any, row: int) -> Any:
        """Resolve a ``@factor.param`` reference for a row, or pass through."""
        if not is_ref(value):
            return value
        factor, name = parse_ref(value)
        group = int(self.group_ids[factor][row])
        return self.latent[factor].values[group][name]

    def namespace(self, row: int) -> dict[str, Any]:
        """Scope for expression samplers: attributes so far, latent params as
        ``factor.param``, ``math`` and the seeded ``rand``."""
        scope: dict[str, Any] = {name: values[row] for name, values in self.columns.items()}
        for factor, ids in self.group_ids.items():
            scope[factor] = SimpleNamespace(**self.latent[factor].values[int(ids[row])])
        scope["math"] = math
        scope["rand"] = self.streams.rand
        return scope


def _round(value: float, decimals: int | None) -> float:
    return round(value, decimals) if decimals is not None else value


def draw(sampler: Any, ctx: RowContext, row: int) -> Any:
    """One value from a sampler for one row."""
    rand = ctx.streams.rand
    if isinstance(sampler, ConstantSampler):
        return sampler.value
    if isinstance(sampler, CategorySampler):
        if sampler.weights is None:
            return rand.choice(sampler.values)
        return rand.choices(sampler.values, weights=sampler.weights, k=1)[0]
    if isinstance(sampler, SubcategorySampler):
        parent = ctx.columns[sampler.parent][row]
        options = sampler.values.get(parent)
        if not options:
            raise ValueError(
                f"subcategory sampler has no values for parent value {parent!r}"
            )
        return rand.choice(options)
    if isinstance(sampler, UniformSampler):
        low, high = ctx.param(sampler.low, row), ctx.param(sampler.high, row)
        if sampler.integer:
            return rand.randint(int(low), int(high))
        return _round(rand.uniform(low, high), sampler.decimals)
    if isinstance(sampler, GaussianSampler):
        value = rand.gauss(ctx.param(sampler.mean, row), ctx.param(sampler.sd, row))
        if sampler.low is not None:
            value = max(sampler.low, value)
        if sampler.high is not None:
            value = min(sampler.high, value)
        return int(value) if sampler.integer else _round(value, sampler.decimals)
    if isinstance(sampler, LognormalSampler):
        value = rand.lognormvariate(ctx.param(sampler.mu, row), ctx.param(sampler.sigma, row))
        return _round(value, sampler.decimals)
    if isinstance(sampler, PoissonSampler):
        return int(ctx.streams.rng.poisson(ctx.param(sampler.lam, row)))
    if isinstance(sampler, BernoulliSampler):
        return rand.random() < ctx.param(sampler.p, row)
    if isinstance(sampler, FakerSampler):
        return _finish_faker(sampler, getattr(ctx.streams.fake, sampler.provider)(**sampler.kwargs))
    if isinstance(sampler, ExpressionSampler):
        value = eval(sampler.expr, {"__builtins__": _SAFE_BUILTINS}, ctx.namespace(row))
        return _round(value, sampler.decimals) if isinstance(value, float) else value
    if isinstance(sampler, ReferenceSampler):
        return ctx.param(sampler.ref, row)
    if isinstance(sampler, MixtureSampler):
        weights = [component.weight for component in sampler.components]
        chosen = rand.choices(sampler.components, weights=weights, k=1)[0]
        return draw(chosen.sampler, ctx, row)
    if isinstance(sampler, ForeignKeySampler):
        group = int(ctx.group_ids[sampler.same_group][row]) if sampler.same_group is not None else None
        return _foreign_keys(sampler, ctx, np.array([group]) if group is not None else None, 1)[0]
    raise TypeError(f"unsupported sampler {type(sampler).__name__}")


_SAFE_BUILTINS = {
    name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
    for name in ("abs", "int", "float", "round", "min", "max", "len", "str", "bool", "sum")
}


def _finish_faker(sampler: FakerSampler, value: Any) -> Any:
    if sampler.join is not None:
        value = sampler.join.join(str(item) for item in value)
    if sampler.transform is not None:
        value = getattr(str(value), sampler.transform)()
    if sampler.as_type is not None:
        value = {"str": str, "float": float, "int": int}[sampler.as_type](value)
    return value


_VECTOR = (ConstantSampler, CategorySampler, UniformSampler, GaussianSampler, LognormalSampler, PoissonSampler, BernoulliSampler, ReferenceSampler)


def _params(value: Any, ctx: RowContext, count: int) -> Any:
    """A sampler parameter for every row: the constant itself, or the
    latent group's value gathered by each row's group index."""
    if not is_ref(value):
        return value
    factor, name = parse_ref(value)
    per_group = np.asarray([group[name] for group in ctx.latent[factor].values])
    return per_group[ctx.group_ids[factor][:count]]


def _rounded(values: np.ndarray, decimals: int | None) -> list[Any]:
    return (np.round(values, decimals) if decimals is not None else values).tolist()


def sample_vector(sampler: Any, count: int, ctx: RowContext) -> list[Any]:
    """A whole column of a simple sampler from the shard's numpy stream."""
    rng = ctx.streams.rng
    if isinstance(sampler, ConstantSampler):
        return [sampler.value] * count
    if isinstance(sampler, CategorySampler):
        weights = None
        if sampler.weights is not None:
            weights = np.asarray(sampler.weights, dtype=np.float64)
            weights = weights / weights.sum()
        index = rng.choice(len(sampler.values), size=count, p=weights)
        return [sampler.values[i] for i in index.tolist()]
    if isinstance(sampler, ReferenceSampler):
        values = _params(sampler.ref, ctx, count)
        return values.tolist() if isinstance(values, np.ndarray) else [values] * count
    if isinstance(sampler, UniformSampler):
        low, high = _params(sampler.low, ctx, count), _params(sampler.high, ctx, count)
        if sampler.integer:
            return rng.integers(np.asarray(low, dtype=np.int64), np.asarray(high, dtype=np.int64) + 1, size=count).tolist()
        return _rounded(rng.uniform(low, high, size=count), sampler.decimals)
    if isinstance(sampler, GaussianSampler):
        values = rng.normal(_params(sampler.mean, ctx, count), _params(sampler.sd, ctx, count), size=count)
        if sampler.low is not None or sampler.high is not None:
            values = np.clip(values, sampler.low, sampler.high)
        if sampler.integer:
            return values.astype(np.int64).tolist()
        return _rounded(values, sampler.decimals)
    if isinstance(sampler, LognormalSampler):
        values = rng.lognormal(_params(sampler.mu, ctx, count), _params(sampler.sigma, ctx, count), size=count)
        return _rounded(values, sampler.decimals)
    if isinstance(sampler, PoissonSampler):
        return rng.poisson(_params(sampler.lam, ctx, count), size=count).astype(int).tolist()
    if isinstance(sampler, BernoulliSampler):
        return (rng.random(count) < _params(sampler.p, ctx, count)).tolist()
    raise TypeError(f"no vectorised form for {type(sampler).__name__}")


def sample_column(sampler: Any, count: int, ctx: RowContext) -> list[Any]:
    """A whole column.

    The simple samplers and the Faker providers with a vectorised form are
    drawn in one go from the shard's numpy stream. Expressions, mixtures,
    subcategories and foreign keys, which look at other columns or per-group
    lists, go row by row.
    """
    if isinstance(sampler, _VECTOR):
        return sample_vector(sampler, count, ctx)
    if isinstance(sampler, ForeignKeySampler):
        groups = ctx.group_ids[sampler.same_group][:count] if sampler.same_group is not None else None
        return _foreign_keys(sampler, ctx, groups, count)
    if isinstance(sampler, FakerSampler) and ctx.streams.fast.supports(sampler.provider, sampler.kwargs):
        values = ctx.streams.fast.draw(sampler.provider, sampler.kwargs, count, ctx.streams.rng)
        return [_finish_faker(sampler, value) for value in values]
    return [draw(sampler, ctx, row) for row in range(count)]


# ------------------------------------------------------------------ latent


def sample_latent(schema: GraphSchema, streams: Streams) -> dict[str, GroupParams]:
    """Draw every latent factor's per-group parameters.

    Each factor gets its own child stream so adding a parameter to one factor
    does not change the groups of another.
    """
    result: dict[str, GroupParams] = {}
    children = streams.spawn(len(schema.latent))
    for factor, child in zip(schema.latent, children):
        groups = factor.groups if factor.groups is not None else _default_groups(schema)
        ctx = RowContext(streams=child)
        values: list[dict[str, Any]] = [{} for _ in range(groups)]
        for name, sampler in factor.params.items():
            column = sample_column(sampler, groups, ctx)
            ctx.columns[name] = column
            for group, value in enumerate(column):
                values[group][name] = value
        result[factor.name] = GroupParams(factor=factor.name, values=values)
    return result


def _default_groups(schema: GraphSchema) -> int:
    return max(2, min(12, schema.total_nodes // 25 or 2))


# ------------------------------------------------------------------- nodes


@dataclass
class FKIndex:
    """The members of a node type by latent group, compact enough to hand to
    every shard and every worker process.

    Generated ids are ``prefix_position``, so the index stores int32 row
    positions per group and rebuilds the id on the way out: seven million
    customers are 28 MB, not a list of seven million strings. A table whose
    ids do not follow the convention (one a caller supplied) keeps its ids as
    an array and the positions index into it.
    """

    prefix: str | None
    members: dict[Any, np.ndarray]
    ids: np.ndarray | None = None

    @classmethod
    def build(cls, frame: pl.DataFrame, factor: str | None) -> FKIndex:
        ids = frame[ID]
        prefix = None
        if frame.height:
            head, _, tail = ids[0].rpartition("_")
            if tail == "0":
                expected = (head + "_") + pl.Series(np.arange(frame.height)).cast(pl.String)
                if bool((ids == expected).all()):
                    prefix = head
        positions = np.arange(frame.height, dtype=np.int32)
        if factor is None:
            members = {None: positions}
        else:
            groups = frame[factor].to_numpy()
            order = np.argsort(groups, kind="stable")
            keys, starts = np.unique(groups[order], return_index=True)
            bounds = [*starts.tolist(), len(order)]
            members = {key.item() if hasattr(key, "item") else key: positions[order[a:b]] for key, a, b in zip(keys, bounds[:-1], bounds[1:])}
        return cls(prefix=prefix, members=members, ids=None if prefix is not None else ids.to_numpy().astype(object))

    def pick(self, group: Any, rng: np.random.Generator, k: int) -> list[str]:
        """``k`` members of ``group`` (``None`` for the whole table), with
        replacement; an empty group yields empty strings."""
        pool = self.members.get(group)
        if pool is None or len(pool) == 0:
            return [""] * k
        chosen = pool[rng.integers(len(pool), size=k)]
        if self.prefix is not None:
            return [f"{self.prefix}_{i}" for i in chosen.tolist()]
        return self.ids[chosen].tolist()


def fk_index(tables: dict[str, pl.DataFrame], node_type: str, factor: str | None) -> FKIndex:
    return FKIndex.build(tables[node_type], factor)


def _foreign_keys(sampler: ForeignKeySampler, ctx: RowContext, groups: np.ndarray | None, count: int) -> list[str]:
    """A column of foreign keys: same-group members where the group has any,
    the whole table otherwise."""
    rng = ctx.streams.rng
    everything = ctx.fk_index[(sampler.node_type, None)]
    if sampler.same_group is None or groups is None:
        return everything.pick(None, rng, count)
    local = ctx.fk_index[(sampler.node_type, sampler.same_group)]
    out: list[str] = [""] * count
    for group in np.unique(groups).tolist():
        rows = np.flatnonzero(groups == group)
        index = local if len(local.members.get(group, ())) else everything
        for row, value in zip(rows.tolist(), index.pick(group if index is local else None, rng, len(rows))):
            out[row] = value
    return out


def shard_bounds(count: int, shard_size: int) -> list[tuple[int, int]]:
    """``(start, length)`` per shard; the last one takes the remainder."""
    if count <= 0:
        return []
    return [(start, min(shard_size, count - start)) for start in range(0, count, shard_size)]


def fk_indexes(node: NodeType, tables: dict[str, pl.DataFrame]) -> dict[tuple[str, str | None], FKIndex]:
    """Every foreign-key lookup a node type needs, built once per type so
    shards (and worker processes) receive the index, not the whole table."""
    indexes: dict[tuple[str, str | None], FKIndex] = {}
    for sampler in node.attributes.values():
        if isinstance(sampler, ForeignKeySampler):
            key = (sampler.node_type, sampler.same_group)
            indexes.setdefault(key, fk_index(tables, *key))
            indexes.setdefault((sampler.node_type, None), fk_index(tables, sampler.node_type, None))
    return indexes


#: Rows a node type needs before its shards go to the worker pool. Below
#: this the pool's start-up (5 to 7 s of imports per worker on Windows, 2 to
#: 3 s on macOS) costs more than the parallelism saves; measured on a 2019
#: laptop, a type this size takes about 5 s single-process.
PARALLEL_MIN_ROWS = 250_000

#: Foreign-key indexes a worker process has loaded, by the file they came
#: from. One node type is in flight at a time, so only the latest is kept.
_WORKER_INDEXES: dict[str, dict[tuple[str, str | None], FKIndex]] = {}


def _indexes_from(path: str) -> dict[tuple[str, str | None], FKIndex]:
    """The indexes pickled to ``path``, loaded once per worker process.

    The parent writes a node type's indexes to a temp file and puts the path
    in every shard job; a worker loads the file the first time it sees the
    path and reuses it for every later shard of that type. A path is a few
    bytes, so jobs stay small, and one pool serves the whole run: no pool
    per node type, no initialiser arguments. (Large ``initargs`` are what to
    avoid on Windows: the parent writes them into a pipe the child reads
    only after importing its main module, so every worker start waits for
    the previous one's import.)
    """
    if path not in _WORKER_INDEXES:
        with open(path, "rb") as handle:
            loaded = pickle.load(handle)
        _WORKER_INDEXES.clear()
        _WORKER_INDEXES[path] = loaded
    return _WORKER_INDEXES[path]


@contextlib.contextmanager
def _index_file(indexes: dict[tuple[str, str | None], FKIndex]) -> Iterator[str | None]:
    """``indexes`` pickled to a temp file for the workers, or ``None`` when
    there are none; the file lives as long as the block."""
    if not indexes:
        yield None
        return
    handle = tempfile.NamedTemporaryFile(prefix="graphfaker-fk-", suffix=".pkl", delete=False)
    try:
        pickle.dump(indexes, handle, protocol=pickle.HIGHEST_PROTOCOL)
        handle.close()
        yield handle.name
    finally:
        handle.close()
        with contextlib.suppress(OSError):
            os.unlink(handle.name)


def sample_node_shard(
    node: NodeType,
    factors: list[LatentFactor],
    latent: dict[str, GroupParams],
    indexes: dict[tuple[str, str | None], FKIndex] | str | None,
    sequence: np.random.SeedSequence,
    start: int,
    count: int,
) -> pl.DataFrame:
    """One shard of a node type. Takes a seed sequence rather than streams so
    it can run in another process; every argument is picklable. ``indexes``
    is the foreign-key index mapping itself in-process, or the path of the
    file holding it in a worker (see :func:`_indexes_from`)."""
    if isinstance(indexes, str):
        indexes = _indexes_from(indexes)
    streams = Streams.from_sequence(sequence)
    ctx = RowContext(streams=streams, latent=latent, fk_index=indexes or {})
    for factor in factors:
        groups = latent[factor.name].groups
        ctx.group_ids[factor.name] = streams.rng.integers(0, groups, size=count)

    data: dict[str, list[Any]] = {ID: [f"{node.prefix}_{start + i}" for i in range(count)]}
    for name, sampler in node.attributes.items():
        column = sample_column(sampler, count, ctx)
        ctx.columns[name] = column
        data[name] = column
    for factor in factors:
        data[factor.name] = ctx.group_ids[factor.name].tolist()
    return pl.DataFrame(data, strict=False)


def _run_shard(job: tuple) -> pl.DataFrame:
    return sample_node_shard(*job)


def sample_nodes(
    node: NodeType,
    schema: GraphSchema,
    latent: dict[str, GroupParams],
    tables: dict[str, pl.DataFrame],
    streams: Streams,
    shard_size: int = DEFAULT_SHARD_SIZE,
    workers: int = 1,
    executor: Executor | None = None,
) -> pl.DataFrame:
    """All rows of one node type, sharded, each shard on its own stream.

    ``workers > 1`` runs shards in a process pool: ``executor`` when the
    caller passes one (one pool serves the whole run), otherwise a pool of
    this call's own. A node type below :data:`PARALLEL_MIN_ROWS` stays in
    process whatever ``workers`` says, because the pool's start-up would
    cost more than it saves. Foreign-key indexes reach the workers through a
    temp file each loads once. The result depends on none of this: shard
    seeds come from the spawn tree, not from which process drew them.
    """
    bounds = shard_bounds(node.count, shard_size)
    if not bounds:
        columns = [ID, *node.attributes, *(factor.name for factor in schema.latent)]
        return pl.DataFrame({column: [] for column in columns})
    children = streams.sequence.spawn(len(bounds))
    indexes = fk_indexes(node, tables)
    parallel = len(bounds) > 1 and node.count >= PARALLEL_MIN_ROWS and (executor is not None or workers > 1)
    if not parallel:
        frames = [sample_node_shard(node, schema.latent, latent, indexes, child, start, count) for (start, count), child in zip(bounds, children)]
    else:
        with _index_file(indexes) as path:
            jobs = [(node, schema.latent, latent, path, child, start, count) for (start, count), child in zip(bounds, children)]
            if executor is not None:
                frames = list(executor.map(_run_shard, jobs, chunksize=4))
            else:
                with ProcessPoolExecutor(max_workers=min(workers, len(bounds))) as pool:
                    frames = list(pool.map(_run_shard, jobs, chunksize=4))
    return pl.concat(frames, how="vertical_relaxed") if len(frames) > 1 else frames[0]
