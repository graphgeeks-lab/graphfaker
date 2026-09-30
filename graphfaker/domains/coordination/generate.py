"""Assemble a coordination graph: entities, follow graph, injected campaigns,
organic activity, truth, manifest.

The order matters and is the same as the fraud pack's: campaigns run *before*
the organic process, because they decide which accounts were created late and
which have no cover activity, and the organic process has to respect both as it
goes. Generating organic activity first and filtering afterwards costs a second
copy of the event stream, and leaves accounts posting before they existed if the
filter is ever missed.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from graphfaker.backends.tables import ID, GraphTables
from graphfaker.domains.coordination import entities, playbooks, process
from graphfaker.domains.coordination.config import CoordinationConfig
from graphfaker.engine.run import GraphRun, Manifest
from graphfaker.engine.sampling import DEFAULT_SHARD_SIZE
from graphfaker.engine.seeding import Streams
from graphfaker.logger import logger

#: Rough events per campaign, used to reserve budget before injection so the
#: total lands near ``num_events``.
EVENTS_PER_CAMPAIGN = 40

POST_COLUMNS = ["source", "target", "event_id", "timestamp", "template_id"]
INTERACTION_COLUMNS = ["source", "target", "event_id", "timestamp", "topic"]


def generate(
    config: CoordinationConfig | None = None,
    seed: int | None = None,
    shard_size: int = DEFAULT_SHARD_SIZE,
    workers: int = 1,
    **overrides,
) -> GraphRun:
    """Generate the coordination graph.

    Args:
        config: A :class:`CoordinationConfig`; keyword overrides build one.
        seed: Reproducible for a given seed and shard size.
        workers: Processes for entity sampling; does not affect the result.
    """
    from graphfaker import __version__

    config = CoordinationConfig(
        **{**(config.model_dump() if config else {}), **overrides}
    )
    root = Streams.root(seed)
    node_streams, structure_streams, campaign_streams = root.spawn(3)

    # 1. Entities
    tables, latent = entities.build_nodes(config, node_streams, shard_size, workers)
    rng = structure_streams.rng
    tables["Account"] = entities.add_account_dates(tables["Account"], config, rng)

    # 2. Topics belong to interest communities, which is what makes an
    #    account's topic choice local and a campaign's topic push plausible.
    pop = process.Population(tables, config)
    topic_community = pop.assign_topic_communities(rng)
    tables["Topic"] = tables["Topic"].with_columns(
        pl.Series(entities.COMMUNITY, topic_community)
    )

    # 3. The organic follow graph.
    follows, _ = entities.follows_edges(tables["Account"], config, rng)
    uses = entities.uses_edges(tables["Account"], tables["Device"], config, rng)
    logger.info(
        "coordination: %d accounts, %d topics, %d follows",
        tables["Account"].height,
        tables["Topic"].height,
        follows.height,
    )

    # 4. Campaigns, before the organic stream.
    injection = playbooks.inject(
        campaign_streams.rng, pop, config, tables["Device"].height
    )
    logger.info(
        "coordination: %d campaigns (%d coordinated, %d organic), %d events",
        len(injection.campaigns),
        sum(1 for c in injection.campaigns if c.is_coordinated),
        sum(1 for c in injection.campaigns if not c.is_coordinated),
        sum(f.height for f in injection.events.values()),
    )

    # 5. Side effects of campaigns on entities.
    tables["Account"] = _apply_fresh_accounts(tables["Account"], pop, injection)
    follows = _apply_extra_follows(follows, pop, injection, config)
    uses = _apply_shared_devices(uses, pop, tables["Device"], injection, config)

    # The Population was built before created_at moved, so refresh the view the
    # organic process reads.
    pop = process.Population(tables, config)
    pop.topic_community = topic_community
    pop.topics_by_community = {
        int(g): (
            np.flatnonzero(topic_community == g)
            if int((topic_community == g).sum())
            else np.arange(pop.n_topics)
        )
        for g in np.unique(pop.account_community)
    }

    stripped = (
        set(pop.account_ids.gather(sorted(injection.stripped_accounts)).to_list())
        if injection.stripped_accounts
        else set()
    )
    fresh = None
    if injection.fresh_accounts:
        fresh = pl.DataFrame(
            {
                "account": [
                    str(pop.account_ids[i]) for i in sorted(injection.fresh_accounts)
                ],
                "created": [
                    process.to_date(injection.fresh_accounts[i])
                    for i in sorted(injection.fresh_accounts)
                ],
            }
        ).with_columns(pl.col("created").cast(pl.Date))

    dormant = None
    if injection.dormant_until:
        order = sorted(injection.dormant_until)
        dormant = pl.DataFrame(
            {
                "account": [str(pop.account_ids[i]) for i in order],
                "awake": [
                    injection.dormant_until[i].astype("datetime64[us]").item()
                    for i in order
                ],
            }
        ).with_columns(pl.col("awake").cast(pl.Datetime("us")))

    def finish(part: pl.DataFrame) -> pl.DataFrame:
        if stripped:
            part = part.filter(~pl.col("source").is_in(stripped))
        if fresh is not None:
            part = process.drop_before_creation(part, fresh)
        if dormant is not None:
            part = process.drop_while_dormant(part, dormant)
        return part

    # 6. Organic activity, a block at a time, filtered as it is produced.
    audience = process.build_audience(pop, follows)
    reserve = len(injection.campaigns) * EVENTS_PER_CAMPAIGN
    budget = max(0, config.num_events - reserve)
    organic = process.organic_events(
        structure_streams.rng, pop, budget, audience, finish=finish
    )
    logger.info(
        "coordination: %d organic events",
        sum(part.height for parts in organic.values() for part in parts),
    )

    # 7. Merge and number in time order.
    edges, event_truth = _merge_events(organic, injection)
    edges["FOLLOWS"] = follows
    edges["USES"] = uses

    # 8. An account cannot act before it existed. Campaign events are placed
    #    from the campaign's own window rather than filtered like the organic
    #    stream, so enforce the invariant once, globally, against the merged
    #    result. This is a clamp and not a filter: the account demonstrably
    #    existed, so it is the date that is wrong, not the event.
    tables["Account"] = _clamp_creation_to_first_event(tables["Account"], edges)
    # 9. follower_count is derived from the final follow graph, campaign edges
    #    included: a farm's members really do have those followers.
    tables["Account"] = _add_follower_count(tables["Account"], follows)

    truth = {
        "campaigns": _campaigns_frame(injection, pop),
        "accounts": _accounts_frame(injection, pop),
        "events": event_truth,
        "community": pl.DataFrame(
            [{"group": g, **v} for g, v in enumerate(latent[entities.COMMUNITY].values)]
        ),
    }

    node_schema = entities.schema(config)
    order = ("FOLLOWS", "USES", process.POSTED, process.RESHARED, process.REPLIED)
    ordered_edges = {k: edges[k] for k in order if k in edges}
    graph = GraphTables(nodes=tables, edges=ordered_edges)
    manifest = Manifest(
        schema_name="coordination",
        schema_digest=node_schema.digest(),
        seed=seed,
        shard_size=shard_size,
        graphfaker_version=__version__,
        created_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        node_counts={k: f.height for k, f in graph.nodes.items()},
        edge_counts={k: f.height for k, f in graph.edges.items()},
        extra={"coordination": config.model_dump(mode="json")},
    )
    return GraphRun(schema=node_schema, tables=graph, manifest=manifest, truth=truth)


# ------------------------------------------------------------------ helpers


def _apply_fresh_accounts(accounts, pop, injection) -> pl.DataFrame:
    if not injection.fresh_accounts:
        return accounts
    created = accounts["created_at"].to_list()
    for idx, day in injection.fresh_accounts.items():
        created[idx] = process.to_date(day)
    return accounts.with_columns(pl.Series("created_at", created, dtype=pl.Date))


def _apply_extra_follows(follows, pop, injection, config) -> pl.DataFrame:
    """Add the follow edges farms and communities created, without duplicates."""
    if not injection.extra_follows:
        return follows
    ids = pop.account_ids
    rows = pl.DataFrame(
        {
            "source": ids.gather([a for a, _ in injection.extra_follows]),
            "target": ids.gather([b for _, b in injection.extra_follows]),
            "since": pl.Series(
                [config.period_start - dt.timedelta(days=3)]
                * len(injection.extra_follows),
                dtype=pl.Date,
            ),
        }
    )
    return pl.concat([follows, rows.select(follows.columns)]).unique(
        subset=["source", "target"], keep="first", maintain_order=True
    )


def _apply_shared_devices(uses, pop, devices, injection, config) -> pl.DataFrame:
    if not injection.shared_devices:
        return uses
    account_ids = pop.account_ids
    device_ids = devices[ID]
    existing = set(zip(uses["source"].to_list(), uses["target"].to_list()))
    rows = []
    for account_idx, device_idx in injection.shared_devices:
        pair = (str(account_ids[account_idx]), str(device_ids[device_idx]))
        if pair in existing:
            continue
        existing.add(pair)
        rows.append(
            {
                "source": pair[0],
                "target": pair[1],
                "first_seen": config.period_start - dt.timedelta(days=1),
            }
        )
    if not rows:
        return uses
    extra = pl.DataFrame(rows).with_columns(pl.col("first_seen").cast(pl.Date))
    return pl.concat([uses, extra.select(uses.columns)])


def _clamp_creation_to_first_event(
    accounts: pl.DataFrame, edges: dict[str, pl.DataFrame]
) -> pl.DataFrame:
    """Move ``created_at`` back where an account acts before it.

    Only ever moves a date earlier, so the fresh-signup signal that low
    tradecraft relies on survives wherever it is consistent.
    """
    firsts = [
        frame.select(
            pl.col("source").alias(ID), pl.col("timestamp").dt.date().alias("first_event")
        )
        for name, frame in edges.items()
        if "event_id" in frame.columns and frame.height
    ]
    if not firsts:
        return accounts
    earliest = pl.concat(firsts).group_by(ID).agg(pl.col("first_event").min())
    return (
        accounts.join(earliest, on=ID, how="left")
        .with_columns(
            pl.min_horizontal("created_at", pl.col("first_event").fill_null(pl.col("created_at")))
            .alias("created_at")
        )
        .drop("first_event")
    )


def _add_follower_count(accounts: pl.DataFrame, follows: pl.DataFrame) -> pl.DataFrame:
    if follows.height == 0:
        return accounts.with_columns(pl.lit(0, dtype=pl.Int64).alias("follower_count"))
    counts = follows.group_by("target").len().rename({"target": ID, "len": "follower_count"})
    return accounts.join(counts, on=ID, how="left").with_columns(
        pl.col("follower_count").fill_null(0).cast(pl.Int64)
    )


def _merge_events(
    organic: dict[str, list[pl.DataFrame]],
    injection: playbooks.Injection,
) -> tuple[dict[str, pl.DataFrame], pl.DataFrame]:
    """Organic plus injected events per channel, numbered in time order across
    channels, with the truth's event labels.

    ``event_id`` carries the rank in time across every channel, so rows stay in
    generation order and a global ordering is still recoverable without sorting
    the whole stream. This is the same convention the fraud pack uses for
    ``tx_id``.
    """
    per_channel: dict[str, list[pl.DataFrame]] = {}
    for channel in process.CHANNELS:
        columns = (
            playbooks.POST_COLUMNS if channel == process.POSTED else playbooks.INTERACTION_COLUMNS
        )
        parts = [part.select(columns) for part in organic.pop(channel, []) if part.height]
        injected = injection.events.get(channel)
        if injected is not None and injected.height:
            parts.append(injected.select(columns))
        if parts:
            per_channel[channel] = parts
    organic.clear()

    empty_truth = pl.DataFrame(
        {
            "event_id": pl.Series([], dtype=pl.String),
            "campaign_id": pl.Series([], dtype=pl.String),
            "playbook": pl.Series([], dtype=pl.String),
            "is_coordinated": pl.Series([], dtype=pl.Boolean),
        }
    )
    if not per_channel:
        return {}, empty_truth

    total = sum(part.height for parts in per_channel.values() for part in parts)
    stamps = np.empty(total, dtype=np.int64)
    at = 0
    for parts in per_channel.values():
        for part in parts:
            stamps[at : at + part.height] = (
                part["timestamp"]
                .cast(pl.Datetime("us"))
                .to_numpy()
                .astype("datetime64[us]")
                .view(np.int64)
            )
            at += part.height
    order = np.argsort(stamps, kind="stable")
    del stamps
    ranks = np.empty(total, dtype=np.int64)
    ranks[order] = np.arange(total)
    del order

    by_campaign = {c.campaign_id: c for c in injection.campaigns}
    edges: dict[str, pl.DataFrame] = {}
    truth_parts = []
    offset = 0
    for channel in list(per_channel):
        parts = per_channel.pop(channel)
        finished = []
        columns = POST_COLUMNS if channel == process.POSTED else INTERACTION_COLUMNS
        for i in range(len(parts)):
            part, parts[i] = parts[i], None  # release the original as we go
            ids = pl.Series("event_id", ranks[offset : offset + part.height])
            offset += part.height
            part = part.with_columns(("ev_" + ids.cast(pl.String)).alias("event_id"))
            truth_parts.append(
                part.filter(pl.col("campaign_id").is_not_null()).select(
                    ["event_id", "campaign_id"]
                )
            )
            finished.append(part.select(columns))
        edges[channel] = pl.concat(finished, rechunk=False) if len(finished) > 1 else finished[0]
    del ranks

    labelled = pl.concat(truth_parts) if truth_parts else empty_truth.select(["event_id", "campaign_id"])
    if labelled.height == 0:
        return edges, empty_truth
    event_truth = labelled.with_columns(
        pl.col("campaign_id")
        .map_elements(lambda c: by_campaign[c].playbook, return_dtype=pl.String)
        .alias("playbook"),
        pl.col("campaign_id")
        .map_elements(lambda c: by_campaign[c].is_coordinated, return_dtype=pl.Boolean)
        .alias("is_coordinated"),
    )
    return edges, event_truth


def _campaigns_frame(injection: playbooks.Injection, pop: process.Population) -> pl.DataFrame:
    rows = [
        {
            "campaign_id": c.campaign_id,
            "playbook": c.playbook,
            "is_coordinated": c.is_coordinated,
            "n_accounts": len(c.roles),
            "n_events": c.n_events,
            "topic": str(pop.topic_ids[c.topic]) if c.topic is not None else None,
            "start": c.start.astype("datetime64[us]").item() if c.start is not None else None,
            "end": c.end.astype("datetime64[us]").item() if c.end is not None else None,
            "accounts": [str(pop.account_ids[i]) for i in c.roles],
            "roles": list(c.roles.values()),
        }
        for c in injection.campaigns
    ]
    return (
        pl.DataFrame(rows)
        if rows
        else pl.DataFrame(
            {
                "campaign_id": pl.Series([], dtype=pl.String),
                "playbook": pl.Series([], dtype=pl.String),
                "is_coordinated": pl.Series([], dtype=pl.Boolean),
            }
        )
    )


def _accounts_frame(injection: playbooks.Injection, pop: process.Population) -> pl.DataFrame:
    rows = [
        {
            "account_id": str(pop.account_ids[idx]),
            "campaign_id": c.campaign_id,
            "playbook": c.playbook,
            "role": role,
            "is_coordinated": c.is_coordinated,
        }
        for c in injection.campaigns
        for idx, role in c.roles.items()
    ]
    return (
        pl.DataFrame(rows)
        if rows
        else pl.DataFrame(
            {
                "account_id": pl.Series([], dtype=pl.String),
                "campaign_id": pl.Series([], dtype=pl.String),
                "playbook": pl.Series([], dtype=pl.String),
                "role": pl.Series([], dtype=pl.String),
                "is_coordinated": pl.Series([], dtype=pl.Boolean),
            }
        )
    )
