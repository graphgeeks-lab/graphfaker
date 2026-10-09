"""Assembly: run the steps in order and return a :class:`GraphRun`.

The order matters and is part of what a seed reproduces. Entities, then the
network, then the patterns (so they can recruit before the ordinary traffic
exists), then the legitimate events filtered by what the patterns did, then
one merge that numbers every event in time order and builds the truth.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from graphfaker import __version__
from graphfaker.backends.tables import ID, SOURCE, GraphTables
from graphfaker.domains.supply_chain import entities, patterns, process
from graphfaker.domains.supply_chain.config import SupplyChainConfig
from graphfaker.engine.run import DEFAULT_SHARD_SIZE, GraphRun, Manifest
from graphfaker.engine.seeding import Streams
from graphfaker.logger import logger

#: Events to hold back from the legitimate budget for each pattern, so that
#: a run lands near the budget rather than over it.
EVENTS_PER_PATTERN = 24

#: Camouflage, in this pack, is displacement. A supplier is a company with a
#: business, so hiding a scheme does not mean doing nothing else: it means
#: doing the scheme *instead of* part of the ordinary work, so that the
#: total stays where it was. At ``low`` the pattern's events are added on
#: top and the supplier is a volume outlier; at ``high`` an equivalent share
#: of its ordinary traffic goes away while the pattern runs and the total
#: says nothing.
#:
#: This replaced stripping the member's activity outright, which was
#: borrowed from the fraud pack and is wrong here. A mule account that only
#: ever forwards money is a real thing; a supplier that stops trading
#: entirely is not hiding, it is just a different kind of odd, and it made
#: ``low`` hardness *harder* than ``high`` for the structural plays.
MAX_DISPLACEMENT = 0.9


def generate(
    config: SupplyChainConfig | None = None,
    *,
    seed: int | None = None,
    shard_size: int = DEFAULT_SHARD_SIZE,
    workers: int = 1,
    **overrides,
) -> GraphRun:
    """A supply chain network with labelled procurement patterns in it."""
    config = SupplyChainConfig(
        **{**(config.model_dump() if config else {}), **overrides}
    )
    root = Streams.root(seed)
    # Seven children, with addresses, names and people last: a stream of
    # their own means adding them left every other draw in the run where it
    # was. Children are taken in order, so asking for one more does not move
    # the earlier ones.
    (
        node_streams,
        structure_streams,
        pattern_streams,
        event_streams,
        address_streams,
        name_streams,
        people_streams,
    ) = root.spawn(7)

    # 1. Entities.
    tables, latent = entities.build_nodes(config, node_streams, shard_size, workers)
    rng = structure_streams.rng
    tables = entities.add_addresses(tables, address_streams)
    tables = entities.add_names(tables, name_streams)
    tables, person_edges = entities.add_people(tables, people_streams)
    tables["Supplier"] = entities.add_onboarding_dates(tables["Supplier"], config, rng)
    logger.info(
        "supply_chain: %d suppliers, %d plants, %d warehouses, %d customers, %d people",
        tables["Supplier"].height,
        tables["Plant"].height,
        tables["Warehouse"].height,
        tables["Customer"].height,
        tables["Person"].height if "Person" in tables else 0,
    )

    # 2. The network: contracts, tiers, lanes.
    structure = entities.structural_edges(config, tables, rng)
    pop = process.Population(tables, config)
    contracts = process.Contracts(pop, structure["SUPPLIES"], structure["PRODUCES"], rng)

    # 3. Patterns first, so they recruit from the whole supplier base rather
    #    than from whoever happens to be busy.
    networked, neighbours = _group_structure(pop, structure.get("SUBCONTRACTS"))
    injection = patterns.inject(
        pattern_streams.rng, pop, contracts, config, networked, neighbours
    )
    logger.info(
        "supply_chain: %d patterns (%d fraudulent, %d legitimate), %d events",
        len(injection.patterns),
        sum(p.is_fraud for p in injection.patterns),
        sum(not p.is_fraud for p in injection.patterns),
        sum(f.height for f in injection.events.values()),
    )

    # 4. What the patterns did to the entities themselves.
    tables["Supplier"] = _apply_fresh_suppliers(tables["Supplier"], pop, injection)
    # Only a shell opened to order has no ordinary business; everyone else
    # keeps theirs and has it displaced instead.
    stripped = {
        str(pop.ids["Supplier"][i])
        for i in injection.stripped_suppliers
        if i in injection.fresh_suppliers
    }
    busy = _pattern_windows(injection, pop)
    displacement = MAX_DISPLACEMENT * config.profile.activity_camouflage

    def finish(part: pl.DataFrame) -> pl.DataFrame:
        """What a pattern's members were not doing while they ran it.

        A shell supplier opened to receive invoices has no ordinary
        business at all. Everyone else keeps theirs, thinned while the
        pattern runs in proportion to ``activity_camouflage``: at ``low``
        nothing is displaced and a member is a volume outlier, at ``high``
        almost all of it is and the total says nothing.
        """
        if stripped:
            part = part.filter(
                ~pl.col(SOURCE).is_in(list(stripped)) & ~pl.col("target").is_in(list(stripped))
            )
        if busy is None or part.height == 0:
            return part
        marked = part.join(busy, left_on=SOURCE, right_on="supplier_id", how="left")
        inside = (
            pl.col("window_start").is_not_null()
            & (pl.col("timestamp") >= pl.col("window_start"))
            & (pl.col("timestamp") <= pl.col("window_end"))
        )
        keep = pl.Series("keep", event_streams.rng.random(marked.height) >= displacement)
        return marked.filter(~inside | keep).drop(["window_start", "window_end"])

    # 5. The ordinary traffic, a block at a time, filtered as it is produced.
    reserve = len(injection.patterns) * EVENTS_PER_PATTERN
    budget = max(1_000, config.event_budget - reserve)
    legitimate = process.legitimate_events(
        event_streams.rng, pop, contracts, structure, budget, finish=finish, as_parts=True
    )
    logger.info(
        "supply_chain: %d legitimate events",
        sum(part.height for parts in legitimate.values() for part in parts),
    )

    # 6. Merge and number in time order.
    event_edges, event_truth = _merge_events(legitimate, injection)
    edges = {**structure, **person_edges, **event_edges}

    truth = {
        "patterns": _patterns_frame(injection, pop),
        "suppliers": _members_frame(injection, pop),
        "events": event_truth,
        "region": _latent_frame(latent, entities.REGION),
        "category": _latent_frame(latent, entities.CATEGORY),
    }

    node_schema = entities.schema(config)
    order = (
        "SUPPLIES", "SUBCONTRACTS", "PRODUCES", "STOCKS", "SERVES", "HAULS",
        "CONTACT_AT", "EXECUTIVE_AT",
        process.ORDERS, process.SHIPS, process.INVOICES, process.INTERCOMPANY, process.DELIVERS,
    )
    graph = GraphTables(nodes=tables, edges={k: edges[k] for k in order if k in edges})
    manifest = Manifest(
        schema_name="supply_chain",
        schema_digest=node_schema.digest(),
        seed=seed,
        shard_size=shard_size,
        graphfaker_version=__version__,
        created_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        node_counts={k: f.height for k, f in graph.nodes.items()},
        edge_counts={k: f.height for k, f in graph.edges.items()},
        extra={"supply_chain": config.model_dump(mode="json")},
    )
    return GraphRun(schema=node_schema, tables=graph, manifest=manifest, truth=truth)


# ------------------------------------------------------------------ helpers


def _pattern_windows(
    injection: patterns.Injection, pop: process.Population
) -> pl.DataFrame | None:
    """One row per member: when its pattern was running.

    Members in several patterns take the widest span, which is the
    conservative reading: they were busy for all of it.
    """
    rows: dict[str, tuple] = {}
    for pattern in injection.patterns:
        if pattern.start is None or pattern.end is None:
            continue
        for index in pattern.roles:
            supplier = str(pop.ids["Supplier"][index])
            start, end = pattern.start, pattern.end
            if supplier in rows:
                previous = rows[supplier]
                start, end = min(start, previous[1]), max(end, previous[2])
            rows[supplier] = (supplier, start, end)
    if not rows:
        return None
    values = list(rows.values())
    return pl.DataFrame(
        {
            "supplier_id": [v[0] for v in values],
            "window_start": np.array([v[1] for v in values]).astype("datetime64[us]"),
            "window_end": np.array([v[2] for v in values]).astype("datetime64[us]"),
        }
    )


def _group_structure(
    pop: process.Population, subcontracts: pl.DataFrame | None
) -> tuple[np.ndarray, dict[int, list[int]]]:
    """Who trades with whom, as a member list and an adjacency map.

    A circle of invoices between parties with no trading relationship is not
    a kiting ring, it is a data error, and it is also trivially detectable by
    counting partners. Both the ring and the consignment loop that imitates
    it are drawn along relationships that already exist.
    """
    if subcontracts is None or subcontracts.height == 0:
        return np.empty(0, dtype=np.int64), {}
    sources = pop.index("Supplier", subcontracts[SOURCE])
    targets = pop.index("Supplier", subcontracts["target"])
    neighbours: dict[int, list[int]] = {}
    for source, target in zip(sources.tolist(), targets.tolist(), strict=False):
        neighbours.setdefault(int(source), []).append(int(target))
        neighbours.setdefault(int(target), []).append(int(source))
    members = np.unique(np.concatenate([sources, targets])) if len(sources) else np.empty(0, dtype=np.int64)
    return members, neighbours


def _apply_fresh_suppliers(
    suppliers: pl.DataFrame, pop: process.Population, injection: patterns.Injection
) -> pl.DataFrame:
    """A shell supplier appears on the books just before it starts invoicing."""
    if not injection.fresh_suppliers:
        return suppliers
    order = sorted(injection.fresh_suppliers)
    fresh = pl.DataFrame(
        {
            ID: [str(pop.ids["Supplier"][i]) for i in order],
            "onboarded": [process.to_date(injection.fresh_suppliers[i]) for i in order],
        }
    ).with_columns(pl.col("onboarded").cast(pl.Date))
    return (
        suppliers.join(fresh, on=ID, how="left")
        .with_columns(pl.coalesce(["onboarded", "onboarded_at"]).alias("onboarded_at"))
        .drop("onboarded")
    )


def _latent_frame(latent: dict, name: str) -> pl.DataFrame:
    return pl.DataFrame([{"group": g, **v} for g, v in enumerate(latent[name].values)])


def _merge_events(
    legitimate: dict[str, list[pl.DataFrame]], injection: patterns.Injection
) -> tuple[dict[str, pl.DataFrame], pl.DataFrame]:
    """Legitimate plus injected events per channel, numbered in time order.

    ``event_id`` carries the rank in time across every channel, so rows stay
    in generation order and the global ordering is still recoverable without
    sorting the whole stream. The same convention as the other two packs.
    """
    columns = list(process.EVENT_COLUMNS)
    per_channel: dict[str, list[pl.DataFrame]] = {}
    for channel in process.CHANNELS:
        parts = [part for part in legitimate.pop(channel, []) if part.height]
        injected = injection.events.get(channel)
        if injected is not None and injected.height:
            parts.append(injected)
        if parts:
            per_channel[channel] = parts
    legitimate.clear()

    empty_truth = pl.DataFrame(
        {
            "event_id": pl.Series([], dtype=pl.String),
            "pattern_id": pl.Series([], dtype=pl.String),
            "play": pl.Series([], dtype=pl.String),
            "is_fraud": pl.Series([], dtype=pl.Boolean),
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
                part["timestamp"].cast(pl.Datetime("us")).to_numpy().astype("datetime64[us]").view(np.int64)
            )
            at += part.height
    order = np.argsort(stamps, kind="stable")
    del stamps
    ranks = np.empty(total, dtype=np.int64)
    ranks[order] = np.arange(total)
    del order

    by_pattern = {p.pattern_id: p for p in injection.patterns}
    edges: dict[str, pl.DataFrame] = {}
    truth_parts = []
    offset = 0
    for channel in list(per_channel):
        parts = per_channel.pop(channel)
        finished = []
        for i in range(len(parts)):
            part, parts[i] = parts[i], None  # release the original as we go
            ids = pl.Series("event_id", ranks[offset : offset + part.height])
            offset += part.height
            added = [("evt_" + ids.cast(pl.String)).alias("event_id")]
            if "pattern_id" not in part.columns:
                added.append(pl.lit(None, dtype=pl.String).alias("pattern_id"))
            part = part.with_columns(added)
            truth_parts.append(
                part.filter(pl.col("pattern_id").is_not_null()).select(["event_id", "pattern_id"])
            )
            finished.append(part.select(columns))
        edges[channel] = pl.concat(finished, rechunk=False) if len(finished) > 1 else finished[0]
    del ranks

    labelled = pl.concat(truth_parts) if truth_parts else empty_truth.select(["event_id", "pattern_id"])
    if labelled.height == 0:
        return edges, empty_truth
    event_truth = labelled.with_columns(
        pl.col("pattern_id").map_elements(lambda p: by_pattern[p].play, return_dtype=pl.String).alias("play"),
        pl.col("pattern_id").map_elements(lambda p: by_pattern[p].is_fraud, return_dtype=pl.Boolean).alias("is_fraud"),
    )
    return edges, event_truth


def _patterns_frame(injection: patterns.Injection, pop: process.Population) -> pl.DataFrame:
    rows = [
        {
            "pattern_id": p.pattern_id,
            "play": p.play,
            "is_fraud": p.is_fraud,
            "n_suppliers": len(p.roles),
            "n_events": p.n_events,
            "plant": str(pop.ids["Plant"][p.extra["plant"]]) if "plant" in p.extra else None,
            "start": p.start.astype("datetime64[us]").item() if p.start is not None else None,
            "end": p.end.astype("datetime64[us]").item() if p.end is not None else None,
            "suppliers": [str(pop.ids["Supplier"][i]) for i in p.roles],
            "roles": [p.roles[i] for i in p.roles],
        }
        for p in injection.patterns
    ]
    if not rows:
        return pl.DataFrame(
            {
                "pattern_id": pl.Series([], dtype=pl.String),
                "play": pl.Series([], dtype=pl.String),
                "is_fraud": pl.Series([], dtype=pl.Boolean),
                "n_suppliers": pl.Series([], dtype=pl.Int64),
                "n_events": pl.Series([], dtype=pl.Int64),
                "plant": pl.Series([], dtype=pl.String),
                "start": pl.Series([], dtype=pl.Datetime("us")),
                "end": pl.Series([], dtype=pl.Datetime("us")),
                "suppliers": pl.Series([], dtype=pl.List(pl.String)),
                "roles": pl.Series([], dtype=pl.List(pl.String)),
            }
        )
    return pl.DataFrame(rows).with_columns(
        pl.col("start").cast(pl.Datetime("us")), pl.col("end").cast(pl.Datetime("us"))
    )


def _members_frame(injection: patterns.Injection, pop: process.Population) -> pl.DataFrame:
    rows = [
        {
            "supplier_id": str(pop.ids["Supplier"][index]),
            "pattern_id": p.pattern_id,
            "play": p.play,
            "role": role,
            "is_fraud": p.is_fraud,
        }
        for p in injection.patterns
        for index, role in p.roles.items()
    ]
    if not rows:
        return pl.DataFrame(
            {
                "supplier_id": pl.Series([], dtype=pl.String),
                "pattern_id": pl.Series([], dtype=pl.String),
                "play": pl.Series([], dtype=pl.String),
                "role": pl.Series([], dtype=pl.String),
                "is_fraud": pl.Series([], dtype=pl.Boolean),
            }
        )
    return pl.DataFrame(rows)
