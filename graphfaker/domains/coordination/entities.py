"""Entities of the coordination pack: accounts, topics, devices, and the
structural edges between them (USES, FOLLOWS).

Attributes are declared as schema node types and drawn by the generic engine,
so the same samplers, latent factors and sharding apply. The follow graph is
built vectorised on top, because a follow graph is the part of a social
platform that has to be right: its in-degree is heavy-tailed over several
orders of magnitude, its communities are recoverable, and a meaningful share of
its edges are reciprocal. Uniform attachment gets none of those.

**No attribute on any node reveals campaign membership.** There is no
``is_bot`` and no ``suspicious`` column. Everything a detector could legitimately
use is here; everything else lives in ``run.truth``, which ``--blind`` leaves
out entirely. A label leaked into a feature makes the whole dataset worthless
as a benchmark, and it is the easiest mistake to make.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from graphfaker.backends.tables import ID
from graphfaker.domains.coordination.config import CoordinationConfig
from graphfaker.engine.run import node_pool
from graphfaker.engine.sampling import sample_latent, sample_nodes
from graphfaker.engine.seeding import Streams
from graphfaker.schema import (
    BernoulliSampler,
    CategorySampler,
    FakerSampler,
    GaussianSampler,
    GraphSchema,
    LatentFactor,
    LognormalSampler,
    NodeType,
    UniformTopology,
)

COMMUNITY = "community"

TOPIC_CATEGORIES = [
    "politics",
    "sport",
    "music",
    "technology",
    "finance",
    "health",
    "gaming",
    "film",
    "local_news",
    "science",
]
TOPIC_CATEGORY_WEIGHTS = [14, 14, 12, 11, 8, 8, 11, 9, 7, 6]

#: Share of follows that are followed back. Reciprocity on real platforms sits
#: in this range and is the property a follow-farm exaggerates to ~1.0, so the
#: organic value has to be substantial or the farm is trivially detectable.
RECIPROCITY = 0.30

#: Share of follow targets drawn from the follower's own interest community.
SAME_COMMUNITY_FOLLOW_RATE = 0.72

#: Probability a follow ignores popularity and picks uniformly. Keeps the tail
#: from running away and stops low-degree accounts being unreachable.
UNIFORM_FOLLOW_RATE = 0.15

#: How much an account's accumulated followers count towards attracting the
#: next one, relative to its intrinsic activity. Above 1 because otherwise the
#: lognormal activity draw dominates and the follower distribution never
#: develops a tail: measured follower Gini went from 0.36 to 0.6+ at 3.0, and
#: real platforms are higher still.
FOLLOWER_ATTACHMENT = 3.0

#: Share of devices that legitimately serve many accounts, and how many they
#: serve. Shared family tablets, internet cafés, corporate NAT, and plain
#: browser-fingerprint collisions all put unrelated accounts behind one
#: identifier. Without this tail a sockpuppet cluster of seven accounts on one
#: device is separable from every organic device by a single feature, and it
#: measured AUC 1.000 at *every* tradecraft level, which made the dial look
#: broken for that playbook.
BUSY_DEVICE_SHARE = 0.02
BUSY_DEVICE_ACCOUNTS = (3, 12)


def schema(config: CoordinationConfig) -> GraphSchema:
    """Node types of the coordination graph.

    Edges are built by the pack rather than by the generic topology models, so
    the schema declares none: a follow graph needs directed preferential
    attachment with reciprocity, which is not one of the schema's topology
    models.
    """
    community = LatentFactor(
        name=COMMUNITY,
        groups=config.communities,
        params={
            # How chatty a community is, and how much it reshares rather than
            # posts. Both feed the organic process, and both are what a
            # campaign's activity has to look ordinary against.
            "log_activity": GaussianSampler(mean=0.0, sd=0.35),
            "reshare_bias": GaussianSampler(mean=0.5, sd=0.12),
        },
    )
    account = NodeType(
        name="Account",
        count=config.num_accounts,
        id_prefix="acc",
        attributes={
            "handle": FakerSampler(provider="user_name"),
            "display_name": FakerSampler(provider="name"),
            # Posting propensity, heavy-tailed like real activity: most
            # accounts are near-silent and a few never stop.
            "activity": LognormalSampler(mu="@community.log_activity", sigma=0.9, decimals=4),
            "reshare_rate": GaussianSampler(
                mean="@community.reshare_bias", sd=0.15, low=0.0, high=1.0, decimals=3
            ),
            "verified": BernoulliSampler(p=0.004),
            "profile_complete": BernoulliSampler(p=0.72),
            "language": CategorySampler(
                values=["en", "es", "pt", "fr", "de", "id"], weights=[55, 12, 10, 8, 8, 7]
            ),
        },
    )
    topic = NodeType(
        name="Topic",
        count=config.num_topics,
        id_prefix="top",
        attributes={
            "name": FakerSampler(provider="word"),
            "category": CategorySampler(
                values=TOPIC_CATEGORIES, weights=TOPIC_CATEGORY_WEIGHTS
            ),
            # A handful of topics take most of the traffic.
            "prominence": LognormalSampler(mu=0.0, sigma=1.3, decimals=4),
        },
    )
    device = NodeType(
        name="Device",
        count=config.num_devices,
        id_prefix="dev",
        attributes={
            "fingerprint": FakerSampler(provider="uuid4"),
            "device_type": CategorySampler(
                values=["mobile", "desktop", "tablet"], weights=[72, 22, 6]
            ),
            "os": CategorySampler(
                values=["android", "ios", "windows", "macos", "linux"],
                weights=[44, 30, 15, 9, 2],
            ),
        },
    )
    return GraphSchema(
        name="coordination",
        latent=[community],
        nodes=[account, topic, device],
        topology=UniformTopology(),
    )


def build_nodes(
    config: CoordinationConfig, streams: Streams, shard_size: int, workers: int = 1
) -> tuple[dict[str, pl.DataFrame], dict]:
    """Sample every node table and return them with the latent group params."""
    node_schema = schema(config)
    latent = sample_latent(node_schema, streams)
    children = dict(zip((n.name for n in node_schema.nodes), streams.spawn(len(node_schema.nodes))))
    tables: dict[str, pl.DataFrame] = {}
    with node_pool(workers) as executor:
        for node in node_schema.generation_order():
            tables[node.name] = sample_nodes(
                node,
                node_schema,
                latent,
                tables,
                children[node.name],
                shard_size,
                workers=workers,
                executor=executor,
            )
    return {node.name: tables[node.name] for node in node_schema.nodes}, latent


def add_account_dates(
    accounts: pl.DataFrame, config: CoordinationConfig, rng: np.random.Generator
) -> pl.DataFrame:
    """``created_at`` strictly before the period.

    Ages are exponential with a long mean: most accounts are a year or two old
    and a few date back a decade. Campaign accounts overwrite this when
    ``account_age_blend`` is low, which is what makes a bloc of same-day
    signups visible; the organic distribution is what that bloc stands out
    against.
    """
    age_days = rng.exponential(scale=700.0, size=accounts.height).astype(np.int64) + 1
    created = [config.period_start - dt.timedelta(days=int(d)) for d in age_days]
    return accounts.with_columns(pl.Series("created_at", created, dtype=pl.Date))


def uses_edges(
    accounts: pl.DataFrame,
    devices: pl.DataFrame,
    config: CoordinationConfig,
    rng: np.random.Generator,
) -> pl.DataFrame:
    """Account -USES-> Device.

    Fewer devices than accounts, so sharing happens organically: people use a
    family tablet, and two accounts run by the same person share a phone.
    That innocent collision rate is what a sockpuppet cluster's shared
    fingerprint has to be distinguishable from.
    """
    n_acc, n_dev = accounts.height, devices.height
    acc_ids = accounts[ID].to_numpy()
    dev_ids = devices[ID].to_numpy()

    primary = np.empty(n_acc, dtype=np.int64)
    # Give the first min(n_acc, n_dev) accounts a device of their own, then
    # share the remainder out. Shuffled so device index does not correlate
    # with account index (and therefore with community).
    order = rng.permutation(n_acc)
    direct = min(n_acc, n_dev)
    primary[order[:direct]] = np.arange(direct)
    if n_acc > direct:
        primary[order[direct:]] = rng.integers(0, n_dev, size=n_acc - direct)

    # A few devices legitimately serve a crowd. Reassigning some accounts onto
    # them (rather than adding extra edges) keeps one device per account, so
    # ``device_shared_with`` still means "how many accounts share my device".
    n_busy = max(1, int(n_dev * BUSY_DEVICE_SHARE)) if n_dev else 0
    if n_busy and n_acc > n_busy:
        busy = rng.choice(n_dev, size=n_busy, replace=False)
        low, high = BUSY_DEVICE_ACCOUNTS
        for device in busy:
            crowd = int(rng.integers(low, high + 1))
            movers = rng.choice(n_acc, size=min(crowd, n_acc), replace=False)
            primary[movers] = device

    days_before = rng.exponential(scale=350.0, size=n_acc).astype(np.int64) + 1
    first_seen = [config.period_start - dt.timedelta(days=int(d)) for d in days_before]
    return pl.DataFrame(
        {
            "source": acc_ids,
            "target": dev_ids[primary],
            "first_seen": pl.Series(first_seen, dtype=pl.Date),
        }
    )


def follows_edges(
    accounts: pl.DataFrame,
    config: CoordinationConfig,
    rng: np.random.Generator,
) -> tuple[pl.DataFrame, np.ndarray]:
    """Account -FOLLOWS-> Account, plus each account's follower count.

    Three mechanisms, each supplying a property a uniform random follow graph
    lacks:

    * **Heavy-tailed out-degree.** Who does the following is drawn by
      ``activity`` too, so some accounts follow hundreds and most follow a
      handful. Drawing sources uniformly gave every account the same
      ``following`` count, which made out-degree useless as a feature and
      flattered any detector that keyed on it.
    * **Preferential attachment on the target.** Follower counts on a real
      platform span orders of magnitude. Targets are drawn in proportion to
      ``activity`` and to the followers they already have, which produces that
      tail; without it every account has the same audience and the notion of
      an amplification target is meaningless.
    * **Community homophily.** Most follows stay inside the follower's
      interest community, so communities are recoverable and a campaign
      clustered in one is not automatically anomalous.
    * **Reciprocity.** A third of follows are followed back. A follow farm
      pushes reciprocity to ~1.0 inside its cluster, so the organic rate has
      to be substantial or the farm is found by one ratio.

    Returns the edge frame and the in-degree array, which the account table
    exposes as ``follower_count``.
    """
    n = accounts.height
    community = accounts[COMMUNITY].to_numpy()
    activity = accounts["activity"].to_numpy().astype(np.float64)
    activity = np.where(activity > 0, activity, 1e-9)

    by_community: dict[int, np.ndarray] = {
        int(c): np.flatnonzero(community == c) for c in np.unique(community)
    }

    target_budget = max(0, config.num_follows)
    if target_budget == 0 or n < 2:
        empty = pl.DataFrame({"source": [], "target": [], "since": []}).with_columns(
            pl.col("since").cast(pl.Date)
        )
        return empty, np.zeros(n, dtype=np.int64)

    # Followers accumulate, so attachment is to (followers + activity): the
    # repeated-entry trick would need a growing list per community, and a
    # running weight array is simpler and vectorises per block.
    followers = np.zeros(n, dtype=np.float64)
    src_parts: list[np.ndarray] = []
    dst_parts: list[np.ndarray] = []

    # Work in blocks so weights are refreshed as the graph grows without
    # recomputing them for every single edge.
    block = max(1024, target_budget // 32)
    produced = 0
    while produced < target_budget:
        take = min(block, target_budget - produced)
        sources = rng.choice(n, size=take, p=activity / activity.sum())
        targets = np.empty(take, dtype=np.int64)

        weight = activity + FOLLOWER_ATTACHMENT * followers
        local_choice = rng.random(take) < SAME_COMMUNITY_FOLLOW_RATE
        uniform_choice = rng.random(take) < UNIFORM_FOLLOW_RATE

        for group, members in by_community.items():
            mask = local_choice & (community[sources] == group)
            count = int(mask.sum())
            if not count or len(members) == 0:
                continue
            w = weight[members]
            total = w.sum()
            if total <= 0:
                targets[mask] = rng.choice(members, size=count)
            else:
                targets[mask] = rng.choice(members, size=count, p=w / total)

        rest = ~local_choice
        count = int(rest.sum())
        if count:
            total = weight.sum()
            targets[rest] = (
                rng.choice(n, size=count, p=weight / total)
                if total > 0
                else rng.integers(0, n, size=count)
            )
        # The uniform share overwrites whatever was chosen, popularity and all.
        count = int(uniform_choice.sum())
        if count:
            targets[uniform_choice] = rng.integers(0, n, size=count)

        keep = sources != targets
        sources, targets = sources[keep], targets[keep]
        np.add.at(followers, targets, 1.0)
        src_parts.append(sources)
        dst_parts.append(targets)
        produced += take

    src = np.concatenate(src_parts)
    dst = np.concatenate(dst_parts)

    # Reciprocate a share, then drop duplicate pairs once. Held in
    # temporaries: reassigning src before reading it again works only because
    # the original happens to be a prefix of the new array, which is exactly
    # the kind of accident that breaks on the next edit.
    recip = rng.random(len(src)) < RECIPROCITY
    back_src, back_dst = dst[recip], src[recip]
    src = np.concatenate([src, back_src])
    dst = np.concatenate([dst, back_dst])

    acc_ids = accounts[ID].to_numpy()
    days_before = rng.exponential(scale=250.0, size=len(src)).astype(np.int64) + 1
    since = np.array(
        [config.period_start - dt.timedelta(days=int(d)) for d in days_before], dtype=object
    )
    edges = (
        pl.DataFrame(
            {
                "source": acc_ids[src],
                "target": acc_ids[dst],
                "since": pl.Series(since.tolist(), dtype=pl.Date),
            }
        )
        # maintain_order, or "first" is whichever row the hash table
        # happened to visit first and the run is not reproducible.
        .unique(subset=["source", "target"], keep="first", maintain_order=True)
    )

    counts = (
        edges.group_by("target")
        .len()
        .join(accounts.select(pl.col(ID).alias("target")), on="target", how="right")
        .fill_null(0)
    )
    lookup = dict(zip(counts["target"].to_list(), counts["len"].to_list()))
    follower_count = np.array([lookup.get(a, 0) for a in acc_ids], dtype=np.int64)
    return edges, follower_count
