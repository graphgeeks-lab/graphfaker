"""Turn samplers into columns.

Node generation is embarrassingly parallel once latent-group parameters are
fixed: each shard of a node type draws its rows from its own seeded stream
and never looks at another shard. The result is a function of the seed and
the shard size only — both are recorded in the manifest — so how many
workers execute the shards is a performance knob and not a semantic one.

Values are drawn row by row. Faker providers dominate the cost, so a
vectorised numeric path would not change the picture; sharding across
processes will, and it is what the shard boundary here is for.
"""

from __future__ import annotations

import math
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
    fk_index: dict[tuple[str, str | None], dict[Any, list[str]]] = field(default_factory=dict)

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
        value = getattr(ctx.streams.fake, sampler.provider)(**sampler.kwargs)
        if sampler.join is not None:
            value = sampler.join.join(str(item) for item in value)
        if sampler.transform is not None:
            value = getattr(str(value), sampler.transform)()
        if sampler.as_type is not None:
            value = {"str": str, "float": float, "int": int}[sampler.as_type](value)
        return value
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
        index = ctx.fk_index[(sampler.node_type, sampler.same_group)]
        if sampler.same_group is not None:
            group = int(ctx.group_ids[sampler.same_group][row])
            local = index.get(group)
            if local:
                return rand.choice(local)
        everything = ctx.fk_index[(sampler.node_type, None)].get(None, [])
        return rand.choice(everything) if everything else ""
    raise TypeError(f"unsupported sampler {type(sampler).__name__}")


_SAFE_BUILTINS = {
    name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
    for name in ("abs", "int", "float", "round", "min", "max", "len", "str", "bool", "sum")
}


def sample_column(sampler: Any, count: int, ctx: RowContext) -> list[Any]:
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


def fk_index(
    tables: dict[str, pl.DataFrame], node_type: str, factor: str | None
) -> dict[Any, list[str]]:
    frame = tables[node_type]
    if factor is None:
        return {None: frame[ID].to_list()}
    grouped: dict[Any, list[str]] = {}
    for node_id, group in zip(frame[ID].to_list(), frame[factor].to_list()):
        grouped.setdefault(group, []).append(node_id)
    return grouped


def shard_bounds(count: int, shard_size: int) -> list[tuple[int, int]]:
    """``(start, length)`` per shard; the last one takes the remainder."""
    if count <= 0:
        return []
    return [(start, min(shard_size, count - start)) for start in range(0, count, shard_size)]


def fk_indexes(node: NodeType, tables: dict[str, pl.DataFrame]) -> dict[tuple[str, str | None], dict[Any, list[str]]]:
    """Every foreign-key lookup a node type needs, built once per type so
    shards (and worker processes) receive the index, not the whole table."""
    indexes: dict[tuple[str, str | None], dict[Any, list[str]]] = {}
    for sampler in node.attributes.values():
        if isinstance(sampler, ForeignKeySampler):
            key = (sampler.node_type, sampler.same_group)
            indexes.setdefault(key, fk_index(tables, *key))
            indexes.setdefault((sampler.node_type, None), fk_index(tables, sampler.node_type, None))
    return indexes


def sample_node_shard(
    node: NodeType,
    factors: list[LatentFactor],
    latent: dict[str, GroupParams],
    indexes: dict[tuple[str, str | None], dict[Any, list[str]]],
    sequence: np.random.SeedSequence,
    start: int,
    count: int,
) -> pl.DataFrame:
    """One shard of a node type. Takes a seed sequence rather than streams so
    it can run in another process; every argument is picklable."""
    streams = Streams.from_sequence(sequence)
    ctx = RowContext(streams=streams, latent=latent, fk_index=indexes)
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

    ``workers > 1`` runs shards in a process pool (``executor`` reuses one
    across node types — spawning a pool per type costs more than it saves).
    The result does not depend on either: shard seeds come from the spawn
    tree, not from which process happened to draw them.
    """
    bounds = shard_bounds(node.count, shard_size)
    if not bounds:
        columns = [ID, *node.attributes, *(factor.name for factor in schema.latent)]
        return pl.DataFrame({column: [] for column in columns})
    children = streams.sequence.spawn(len(bounds))
    indexes = fk_indexes(node, tables)
    jobs = [
        (node, schema.latent, latent, indexes, child, start, count)
        for (start, count), child in zip(bounds, children)
    ]
    if executor is not None and len(jobs) > 1:
        frames = list(executor.map(_run_shard, jobs))
    elif workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
            frames = list(pool.map(_run_shard, jobs))
    else:
        frames = [_run_shard(job) for job in jobs]
    return pl.concat(frames, how="vertical_relaxed") if len(frames) > 1 else frames[0]
