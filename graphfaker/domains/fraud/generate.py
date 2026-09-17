"""Assemble a fraud graph: entities, legitimate process, injected patterns,
truth, manifest."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from graphfaker.backends.tables import ID, GraphTables
from graphfaker.domains.fraud import entities, process, typologies
from graphfaker.domains.fraud.config import FraudConfig
from graphfaker.engine.run import GraphRun, Manifest
from graphfaker.engine.sampling import DEFAULT_SHARD_SIZE
from graphfaker.engine.seeding import Streams
from graphfaker.logger import logger

#: Rough transactions per pattern, used to reserve budget before injection.
TRANSACTIONS_PER_PATTERN = 18

TX_COLUMNS = ["source", "target", "tx_id", "timestamp", "amount", "memo", "recurring"]


def generate(
    config: FraudConfig | None = None,
    seed: int | None = None,
    shard_size: int = DEFAULT_SHARD_SIZE,
    workers: int = 1,
    **overrides,
) -> GraphRun:
    """Generate the fraud graph.

    Args:
        config: A :class:`FraudConfig`; keyword overrides build one.
        seed: Reproducible for a given seed and shard size.
        workers: Processes for entity sampling; does not affect the result.
    """
    from graphfaker import __version__

    config = FraudConfig(**{**(config.model_dump() if config else {}), **overrides})
    root = Streams.root(seed)
    node_streams, process_streams, pattern_streams = root.spawn(3)

    # 1. Entities
    tables, latent = entities.build_nodes(config, node_streams, shard_size, workers)
    rng = process_streams.rng
    tables["Account"] = entities.add_account_dates(tables["Account"], config, rng)
    uses, tables["Device"] = entities.uses_edges(tables["Customer"], tables["Device"], config, rng)
    logger.info("fraud: %d customers, %d accounts, %d merchants", tables["Customer"].height, tables["Account"].height, tables["Merchant"].height)

    # 2. Patterns first: they decide which accounts are stripped or opened
    #    late, and the legitimate process applies that as it goes.
    pop = process.population(tables, config)
    merchants = process.MerchantIndex(pop)
    contacts = process.build_contacts(rng, pop, pop.account_weight + 1e-9)
    injection = typologies.inject(
        pattern_streams.rng, pop, config, merchants, tables["Customer"].height, tables["Device"].height
    )
    logger.info("fraud: %d patterns, %d transactions", len(injection.patterns), sum(f.height for f in injection.transactions.values()))

    # 3. Side effects of patterns on entities
    tables["Account"] = _apply_fresh_accounts(tables["Account"], pop, injection)
    tables["Customer"] = _apply_identity_overrides(tables["Customer"], injection)
    uses = _apply_shared_devices(uses, tables, pop, injection, config)
    stripped = set(pop.account_ids.gather(list(injection.stripped_accounts)).to_list()) if injection.stripped_accounts else set()
    fresh = None
    if injection.fresh_accounts:
        # A freshly opened mule account cannot have been transacting before it
        # existed; drop the camouflage that predates its opening.
        fresh = pl.DataFrame(
            {
                "account": [str(pop.account_ids[i]) for i in injection.fresh_accounts],
                "opened": [d.astype("datetime64[D]").astype(dt.date) for d in injection.fresh_accounts.values()],
            }
        ).with_columns(pl.col("opened").cast(pl.Date))

    def finish(part: pl.DataFrame) -> pl.DataFrame:
        if stripped:
            part = part.filter(~pl.col("source").is_in(stripped) & ~pl.col("target").is_in(stripped))
        if fresh is not None:
            part = _drop_before_opening(part, fresh)
        return part

    # 4. Legitimate process, a block of accounts at a time, filtered as it goes.
    reserve = config.num_patterns * TRANSACTIONS_PER_PATTERN
    budget = max(0, config.num_transactions - reserve)
    legit, _contacts, _merchants = process.legitimate_transactions(
        rng, pop, budget, contacts=contacts, merchants=merchants, finish=finish, as_parts=True
    )
    logger.info("fraud: %d legitimate transactions", sum(part.height for parts in legit.values() for part in parts))

    # 5. Merge and assign ids in time order. ``legit`` is consumed channel by
    # channel and nothing is sorted or concatenated into a second copy.
    edges, tx_truth = _merge_transactions(legit, injection)
    edges["OWNS"] = entities.owns_edges(tables["Account"])
    edges["USES"] = uses

    # 6. Truth
    truth = {
        "patterns": _patterns_frame(injection, pop),
        "accounts": _accounts_frame(injection, pop),
        "transactions": tx_truth,
        "region": pl.DataFrame([{"group": g, **v} for g, v in enumerate(latent[entities.REGION].values)]),
    }

    node_schema = entities.schema(config)
    ordered_edges = {k: edges[k] for k in ("OWNS", "USES", "PAYS", "TRANSFERS", "WIRES") if k in edges}
    graph = GraphTables(nodes=tables, edges=ordered_edges)
    manifest = Manifest(
        schema_name="fraud",
        schema_digest=node_schema.digest(),
        seed=seed,
        shard_size=shard_size,
        graphfaker_version=__version__,
        created_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        node_counts={k: f.height for k, f in graph.nodes.items()},
        edge_counts={k: f.height for k, f in graph.edges.items()},
        extra={"fraud": config.model_dump(mode="json")},
    )
    return GraphRun(schema=node_schema, tables=graph, manifest=manifest, truth=truth)


# ------------------------------------------------------------------ helpers


def _drop_before_opening(frame: pl.DataFrame, fresh: pl.DataFrame) -> pl.DataFrame:
    if frame.height == 0:
        return frame
    out = frame
    for endpoint in ("source", "target"):
        out = (
            out.join(fresh.rename({"account": endpoint}), on=endpoint, how="left")
            .filter(pl.col("opened").is_null() | (pl.col("timestamp").dt.date() >= pl.col("opened")))
            .drop("opened")
        )
    return out


def _apply_fresh_accounts(accounts: pl.DataFrame, pop: process.Population, injection: typologies.Injection) -> pl.DataFrame:
    if not injection.fresh_accounts:
        return accounts
    opened = accounts["opened_at"].to_list()
    for idx, day in injection.fresh_accounts.items():
        opened[idx] = day.astype("datetime64[D]").astype(dt.date)
    return accounts.with_columns(pl.Series("opened_at", opened, dtype=pl.Date))


def _apply_identity_overrides(customers: pl.DataFrame, injection: typologies.Injection) -> pl.DataFrame:
    """Synthetic identities copy phone, street and city from their template."""
    if not injection.customer_overrides:
        return customers
    columns = {c: customers[c].to_list() for c in ("phone", "street", "city")}
    for idx, override in injection.customer_overrides.items():
        template = override["template"]
        for values in columns.values():
            values[idx] = values[template]
    return customers.with_columns([pl.Series(c, v) for c, v in columns.items()])


def _apply_shared_devices(uses: pl.DataFrame, tables, pop, injection: typologies.Injection, config: FraudConfig) -> pl.DataFrame:
    if not injection.shared_devices:
        return uses
    cust_ids = tables["Customer"][ID].to_numpy()
    dev_ids = tables["Device"][ID].to_numpy()
    existing = set(zip(uses["source"].to_list(), uses["target"].to_list()))
    rows = []
    for cust_idx, dev_idx in injection.shared_devices:
        pair = (cust_ids[cust_idx], dev_ids[dev_idx])
        if pair in existing:
            continue
        existing.add(pair)
        rows.append({"source": pair[0], "target": pair[1], "first_seen": config.period_start - dt.timedelta(days=1)})
    if not rows:
        return uses
    extra = pl.DataFrame(rows).with_columns(pl.col("first_seen").cast(pl.Date))
    return pl.concat([uses, extra.select(uses.columns)])


def _merge_transactions(
    legit: dict[str, list[pl.DataFrame]],
    injection: typologies.Injection,
) -> tuple[dict[str, pl.DataFrame], pl.DataFrame]:
    """Legitimate plus injected transactions per channel, numbered in time
    order across channels, with the truth's transaction labels.

    Memory is the constraint at scale (90M rows), so nothing here holds a
    second copy of a channel. The process hands over each channel as the
    list of parts it produced (a block of accounts, or a month of a
    recurring flow); the injected rows are one more part. The global time
    order is one ``argsort`` over an int64 timestamp array, ``tx_id`` is
    attached one part at a time (adding a column copies the part, and the
    original is dropped as soon as it has been used), and the parts are
    concatenated without a copy. Rows stay in generation order with
    ``tx_id`` carrying the time rank; sort by ``timestamp`` for time order.
    """
    columns = ["source", "target", "amount", "timestamp", "memo", "recurring", "pattern_id"]
    per_channel: dict[str, list[pl.DataFrame]] = {}
    for channel in process.CHANNELS:
        parts = [part for part in legit.pop(channel, []) if part.height]
        injected = injection.transactions.get(channel)
        if injected is not None and injected.height:
            parts.append(injected.select(columns))
        if parts:
            per_channel[channel] = parts
    legit.clear()
    if not per_channel:
        empty = pl.DataFrame({c: [] for c in TX_COLUMNS})
        return {ch: empty for ch in process.CHANNELS}, pl.DataFrame({"tx_id": [], "pattern_id": [], "typology": [], "is_fraud": []})

    # Time rank across channels: ties keep channel order, then part order,
    # then row order, which is what a stable sort of the concatenated
    # timestamps gives.
    total = sum(part.height for parts in per_channel.values() for part in parts)
    stamps = np.empty(total, dtype=np.int64)
    at = 0
    for parts in per_channel.values():
        for part in parts:
            stamps[at : at + part.height] = part["timestamp"].cast(pl.Datetime("us")).to_numpy().astype("datetime64[us]").view(np.int64)
            at += part.height
    order = np.argsort(stamps, kind="stable")
    del stamps
    ranks = np.empty(total, dtype=np.int32 if total < 2**31 else np.int64)
    ranks[order] = np.arange(total, dtype=ranks.dtype)
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
            ids = pl.Series("tx_id", ranks[offset : offset + part.height])
            offset += part.height
            added = [("tx_" + ids.cast(pl.String)).alias("tx_id")]
            if "pattern_id" not in part.columns:
                added.append(pl.lit(None, dtype=pl.String).alias("pattern_id"))
            part = part.with_columns(added)
            truth_parts.append(part.filter(pl.col("pattern_id").is_not_null()).select(["tx_id", "pattern_id"]))
            finished.append(part.select(TX_COLUMNS))
        edges[channel] = pl.concat(finished, rechunk=False) if len(finished) > 1 else finished[0]
    del ranks
    labelled = pl.concat(truth_parts)
    tx_truth = labelled.with_columns(
        pl.col("pattern_id").map_elements(lambda p: by_pattern[p].typology, return_dtype=pl.String).alias("typology"),
        pl.col("pattern_id").map_elements(lambda p: by_pattern[p].is_fraud, return_dtype=pl.Boolean).alias("is_fraud"),
    )
    for channel in process.CHANNELS:
        if channel not in edges:
            edges[channel] = pl.DataFrame({c: [] for c in TX_COLUMNS})
    return edges, tx_truth


def _patterns_frame(injection: typologies.Injection, pop: process.Population) -> pl.DataFrame:
    rows = []
    for p in injection.patterns:
        rows.append(
            {
                "pattern_id": p.pattern_id,
                "typology": p.typology,
                "is_fraud": p.is_fraud,
                "n_accounts": len(p.roles),
                "n_transactions": p.n_transactions,
                "start": p.start.astype("datetime64[us]").item() if p.start is not None else None,
                "end": p.end.astype("datetime64[us]").item() if p.end is not None else None,
                "accounts": [str(pop.account_ids[i]) for i in p.roles],
                "roles": list(p.roles.values()),
            }
        )
    return pl.DataFrame(rows)


def _accounts_frame(injection: typologies.Injection, pop: process.Population) -> pl.DataFrame:
    rows = [
        {
            "account_id": str(pop.account_ids[idx]),
            "pattern_id": p.pattern_id,
            "typology": p.typology,
            "role": role,
            "is_fraud": p.is_fraud,
        }
        for p in injection.patterns
        for idx, role in p.roles.items()
    ]
    return pl.DataFrame(rows) if rows else pl.DataFrame({"account_id": [], "pattern_id": [], "typology": [], "role": [], "is_fraud": []})
