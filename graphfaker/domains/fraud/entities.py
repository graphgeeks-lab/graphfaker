"""Entities of the fraud pack: customers, accounts, merchants, devices,
counterparties, and the structural edges between them (OWNS, USES).

Attributes are declared as schema node types and drawn by the generic
engine, so the same samplers, latent factors and sharding apply. The
structural edges are built vectorised on top, including the innocent
device-sharing collisions (households) that guilty sharing has to hide in.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from graphfaker.backends.tables import ID
from graphfaker.domains.fraud.config import FraudConfig
from graphfaker.engine.run import node_pool
from graphfaker.engine.sampling import sample_latent, sample_nodes
from graphfaker.engine.seeding import Streams
from graphfaker.schema import (
    CategorySampler,
    ConstantSampler,
    FakerSampler,
    ForeignKeySampler,
    GaussianSampler,
    GraphSchema,
    LatentFactor,
    LognormalSampler,
    NodeType,
    UniformTopology,
)

REGION = "region"

#: Merchant categories with the log-normal amount profile of a purchase there
#: and whether a customer tends to pay them on a schedule.
MERCHANT_CATEGORIES: dict[str, tuple[float, float, bool]] = {
    # category: (mu, sigma, recurring)
    "grocery": (4.0, 0.5, False),
    "restaurant": (3.6, 0.6, False),
    "fuel": (3.9, 0.35, False),
    "retail": (4.2, 0.9, False),
    "electronics": (5.8, 0.7, False),
    "travel": (6.0, 0.8, False),
    "healthcare": (4.8, 0.9, False),
    "entertainment": (3.5, 0.6, False),
    "utilities": (4.9, 0.4, True),
    "rent": (7.2, 0.3, True),
    "subscription": (2.5, 0.4, True),
}
MERCHANT_WEIGHTS = [12, 14, 6, 14, 3, 3, 5, 6, 8, 10, 12]

ACCOUNT_TYPES = ["checking", "savings", "business", "credit"]
ACCOUNT_TYPE_WEIGHTS = [55, 25, 8, 12]
#: Relative transaction rate by account type.
ACCOUNT_ACTIVITY = {"checking": 1.0, "savings": 0.15, "business": 2.5, "credit": 0.8}
STATUS_ACTIVITY = {"active": 1.0, "dormant": 0.05, "closed": 0.0}

#: Share of devices used by a second customer in the same region: households,
#: shared family tablets. This is the innocent collision rate guilty sharing
#: must be distinguishable from.
HOUSEHOLD_SHARE_RATE = 0.06


def schema(config: FraudConfig) -> GraphSchema:
    """Node types of the fraud graph. Edges are built by the pack, not by the
    generic topology models, so the schema declares none."""
    region = LatentFactor(
        name=REGION,
        groups=config.regions,
        params={
            "log_income": GaussianSampler(mean=10.8, sd=0.3),
            "log_balance": GaussianSampler(mean=8.0, sd=0.4),
        },
    )
    customer = NodeType(
        name="Customer",
        count=config.num_customers,
        id_prefix="cust",
        attributes={
            "name": FakerSampler(provider="name"),
            "date_of_birth": FakerSampler(
                provider="date_of_birth", kwargs={"minimum_age": 18, "maximum_age": 85}
            ),
            "email": FakerSampler(provider="email"),
            "phone": FakerSampler(provider="phone_number"),
            "street": FakerSampler(provider="street_address"),
            "city": FakerSampler(provider="city"),
            "segment": CategorySampler(values=["retail", "affluent", "business"], weights=[80, 15, 5]),
            "kyc_tier": CategorySampler(values=[1, 2, 3], weights=[20, 60, 20]),
            "log_income": GaussianSampler(mean="@region.log_income", sd=0.45, decimals=3),
            # Relative propensity to transact; heavy-tailed like real activity.
            "activity": LognormalSampler(mu=0.0, sigma=0.7, decimals=4),
        },
    )
    account = NodeType(
        name="Account",
        count=config.num_accounts,
        id_prefix="acc",
        attributes={
            "customer": ForeignKeySampler(node_type="Customer", same_group=REGION),
            "account_type": CategorySampler(values=ACCOUNT_TYPES, weights=ACCOUNT_TYPE_WEIGHTS),
            "status": CategorySampler(values=["active", "dormant", "closed"], weights=[90, 8, 2]),
            "currency": ConstantSampler(value=config.currency),
            "balance": LognormalSampler(mu="@region.log_balance", sigma=1.2, decimals=2),
        },
    )
    merchant = NodeType(
        name="Merchant",
        count=config.num_merchants,
        id_prefix="mer",
        attributes={
            "name": FakerSampler(provider="company"),
            "category": CategorySampler(values=list(MERCHANT_CATEGORIES), weights=MERCHANT_WEIGHTS),
            "city": FakerSampler(provider="city"),
            # Popularity: a handful of merchants take most of the traffic.
            "prominence": LognormalSampler(mu=0.0, sigma=1.2, decimals=4),
        },
    )
    device = NodeType(
        name="Device",
        count=config.num_devices,
        id_prefix="dev",
        attributes={
            "fingerprint": FakerSampler(provider="uuid4"),
            "device_type": CategorySampler(values=["mobile", "desktop", "tablet"], weights=[65, 25, 10]),
            "os": CategorySampler(values=["android", "ios", "windows", "macos", "linux"], weights=[40, 30, 18, 10, 2]),
        },
    )
    counterparty = NodeType(
        name="Counterparty",
        count=config.num_counterparties,
        id_prefix="cp",
        attributes={
            "name": FakerSampler(provider="company"),
            "country": FakerSampler(provider="country_code"),
            "iban": FakerSampler(provider="iban"),
        },
    )
    return GraphSchema(
        name="fraud",
        latent=[region],
        nodes=[customer, account, merchant, device, counterparty],
        topology=UniformTopology(),
    )


def build_nodes(config: FraudConfig, streams: Streams, shard_size: int, workers: int = 1) -> tuple[dict[str, pl.DataFrame], dict]:
    """Sample every node table and return them with the latent group params."""
    node_schema = schema(config)
    latent = sample_latent(node_schema, streams)
    children = dict(zip((n.name for n in node_schema.nodes), streams.spawn(len(node_schema.nodes))))
    tables: dict[str, pl.DataFrame] = {}
    with node_pool(workers) as executor:
        for node in node_schema.generation_order():
            tables[node.name] = sample_nodes(
                node, node_schema, latent, tables, children[node.name], shard_size, workers=workers, executor=executor
            )
    ordered = {node.name: tables[node.name] for node in node_schema.nodes}
    return ordered, latent


def add_account_dates(accounts: pl.DataFrame, config: FraudConfig, rng: np.random.Generator) -> pl.DataFrame:
    """``opened_at`` strictly before the period so every transaction an
    account makes post-dates its opening. Ages are exponential: most
    accounts are a few years old, a few are very old."""
    age_days = rng.exponential(scale=900.0, size=accounts.height).astype(np.int64) + 1
    opened = [config.period_start - dt.timedelta(days=int(d)) for d in age_days]
    return accounts.with_columns(pl.Series("opened_at", opened, dtype=pl.Date))


def owns_edges(accounts: pl.DataFrame) -> pl.DataFrame:
    return accounts.select(
        pl.col("customer").alias("source"),
        pl.col(ID).alias("target"),
        pl.col("opened_at").alias("since"),
    )


def uses_edges(
    customers: pl.DataFrame,
    devices: pl.DataFrame,
    config: FraudConfig,
    rng: np.random.Generator,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Customer -USES-> Device.

    Every customer gets a primary device; the surplus devices go to random
    customers as second devices; then a share of devices is also used by
    another customer of the same region (a household). Returns the edges and
    the device table with ``first_seen`` filled.
    """
    n_cust, n_dev = customers.height, devices.height
    cust_ids = customers[ID].to_numpy()
    dev_ids = devices[ID].to_numpy()
    cust_region = customers[REGION].to_numpy()

    src = [np.arange(min(n_cust, n_dev))]
    dst = [np.arange(min(n_cust, n_dev))]
    if n_dev > n_cust:
        extra = n_dev - n_cust
        src.append(rng.integers(0, n_cust, size=extra))
        dst.append(np.arange(n_cust, n_dev))
    src_idx = np.concatenate(src)
    dst_idx = np.concatenate(dst)

    # Household sharing: pick devices, then a second customer from the same
    # region as the primary user.
    by_region: dict[int, np.ndarray] = {
        int(r): np.flatnonzero(cust_region == r) for r in np.unique(cust_region)
    }
    n_shared = int(n_dev * HOUSEHOLD_SHARE_RATE)
    shared_dev = rng.choice(n_dev, size=n_shared, replace=False) if n_shared else np.array([], dtype=int)
    primary_of = {int(d): int(s) for s, d in zip(src_idx, dst_idx)}
    extra_src, extra_dst = [], []
    for d in shared_dev:
        owner = primary_of[int(d)]
        pool = by_region[int(cust_region[owner])]
        if len(pool) < 2:
            continue
        other = int(rng.choice(pool))
        if other == owner:
            continue
        extra_src.append(other)
        extra_dst.append(int(d))
    src_idx = np.concatenate([src_idx, np.array(extra_src, dtype=int)])
    dst_idx = np.concatenate([dst_idx, np.array(extra_dst, dtype=int)])

    days_before = rng.exponential(scale=400.0, size=len(src_idx)).astype(np.int64) + 1
    first_seen = [config.period_start - dt.timedelta(days=int(d)) for d in days_before]
    edges = pl.DataFrame(
        {
            "source": cust_ids[src_idx],
            "target": dev_ids[dst_idx],
            "first_seen": pl.Series(first_seen, dtype=pl.Date),
        }
    )
    device_first = edges.group_by("target").agg(pl.col("first_seen").min()).rename({"target": ID})
    devices = devices.join(device_first, on=ID, how="left")
    return edges, devices
