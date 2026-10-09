"""Entities of the supply chain pack, and the structure between them.

Suppliers, plants, warehouses, customers, products and carriers are declared
as schema node types and drawn by the generic engine, so sharding, latent
factors and reproducibility come for free. The structure on top is built
here, because a supply network is not a social graph: it is tiered, nearly
bipartite between levels, and the thing that makes it interesting is that a
few suppliers sit under many plants and a few carriers move most of the
freight.

**No attribute on any node says whether an entity is in a pattern.** There is
no ``is_shell`` and no ``suspicious`` column. A supplier created by the
phantom supplier play is drawn exactly like the others, and the only thing
that distinguishes it is what it does, which is the point of the dataset.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from graphfaker.backends.tables import ID, SOURCE, TARGET
from graphfaker.domains.supply_chain.config import TIER_MIX, SupplyChainConfig
from graphfaker.engine.addresses import RETAIL, shared_addresses
from graphfaker.engine.names import company_names
from graphfaker.engine.people import attach
from graphfaker.engine.run import node_pool
from graphfaker.engine.sampling import sample_latent, sample_nodes
from graphfaker.engine.seeding import Streams
from graphfaker.schema import (
    CategorySampler,
    FakerSampler,
    GaussianSampler,
    GraphSchema,
    LatentFactor,
    LognormalSampler,
    NodeType,
    UniformSampler,
    UniformTopology,
)

REGION = "region"
CATEGORY = "category"

COUNTRIES = ["US", "MX", "CN", "VN", "DE", "PL", "IN", "TR", "BR", "MY"]
COUNTRY_WEIGHTS = [16, 9, 22, 11, 9, 6, 11, 5, 5, 6]

PRODUCT_FAMILIES = [
    "fasteners",
    "castings",
    "electronics",
    "polymers",
    "textiles",
    "packaging",
    "chemicals",
    "bearings",
]
SEGMENTS = ["grocery", "electronics retail", "industrial", "online", "wholesale"]
SEGMENT_WEIGHTS = [26, 18, 20, 22, 14]
MODES = ["road", "sea", "air", "rail"]
MODE_WEIGHTS = [52, 28, 8, 12]
#: Transit days by mode, before the lane's own variation.
MODE_TRANSIT = {"road": 3.0, "sea": 28.0, "air": 2.0, "rail": 9.0}

#: How many plants a tier-1 supplier serves, and how many suppliers of the
#: tier below a supplier subcontracts to. Both are heavy-tailed: most sell to
#: one or two, a few are everywhere, and the ones that are everywhere are
#: what makes a single failure a cascade.
SUPPLIES_PER_SUPPLIER = 1.8
SUBCONTRACTS_PER_SUPPLIER = 1.6
PRODUCTS_PER_PLANT = 6.0
PRODUCTS_PER_WAREHOUSE = 12.0
CUSTOMERS_PER_WAREHOUSE_SHARE = 1.0
#: Co-manufacturing relationships between suppliers at the same level, as a
#: share of that level's size, and sub-assemblies sold back up the chain.
#: Together they are what give the subcontracting graph its cycles.
LATERAL_RATE = 0.22
UPWARD_RATE = 0.10
LANES_PER_CARRIER = 3.0


def schema(config: SupplyChainConfig) -> GraphSchema:
    """Node types of the supply chain graph.

    Edges are built by the pack rather than by a topology model: the network
    is tiered and the degree distribution of each level is the thing being
    modelled, which no general topology model expresses.
    """
    # Both factors are written onto every node type, which is what the
    # engine does and what makes attributes agree across the network: a
    # supplier's ``category`` is the family it mainly sells, a product's
    # ``region`` is where it is made, and both feed the samplers below.
    region = LatentFactor(
        name=REGION,
        groups=config.regions,
        params={
            # How much a region buys, what it costs to buy there, and how
            # exposed its lanes are to delay. A disruption cascade is a
            # region having a bad month, so this is what it has to look
            # ordinary against.
            "log_demand": GaussianSampler(mean=0.0, sd=0.4),
            "cost_index": GaussianSampler(mean=1.0, sd=0.18, low=0.5, high=1.8),
            "delay_bias": GaussianSampler(mean=0.0, sd=0.35),
        },
    )
    category = LatentFactor(
        name=CATEGORY,
        groups=config.categories,
        params={
            # Unit costs sit either side of fifty: a fastener is cents, a
            # casting is hundreds. Together with the order quantities in
            # ``process`` this is what decides where a purchase order lands
            # against the approval threshold, which is the whole subject of
            # the split-order play.
            "log_unit_cost": GaussianSampler(mean=4.0, sd=1.1),
            "base_lead_days": GaussianSampler(mean=21.0, sd=9.0, low=2.0, high=90.0),
        },
    )
    supplier = NodeType(
        name="Supplier",
        count=config.num_suppliers,
        id_prefix="sup",
        attributes={
            "name": FakerSampler(provider="company"),
            "tier": CategorySampler(values=[1, 2, 3], weights=list(TIER_MIX)),
            "country": CategorySampler(values=COUNTRIES, weights=COUNTRY_WEIGHTS),
            # Capacity is heavy-tailed: a handful of suppliers could take the
            # whole programme and most could not take a tenth of it.
            "capacity_units": LognormalSampler(mu=8.5, sigma=1.2, decimals=0),
            # What share of this supplier's shipments arrive by the date it
            # promised, before anything the lane or the period does to it.
            "on_time_rate": GaussianSampler(mean=0.88, sd=0.09, low=0.3, high=1.0, decimals=3),
            "quality_score": GaussianSampler(mean=0.95, sd=0.04, low=0.5, high=1.0, decimals=3),
            "payment_terms_days": CategorySampler(values=[30, 45, 60, 90], weights=[42, 26, 22, 10]),
        },
    )
    plant = NodeType(
        name="Plant",
        count=config.num_plants,
        id_prefix="plt",
        attributes={
            "name": FakerSampler(provider="company"),
            "country": CategorySampler(values=COUNTRIES, weights=COUNTRY_WEIGHTS),
            "capacity_units": LognormalSampler(mu="@region.log_demand", sigma=0.7, decimals=0),
            "shifts": CategorySampler(values=[1, 2, 3], weights=[18, 46, 36]),
        },
    )
    warehouse = NodeType(
        name="Warehouse",
        count=config.num_warehouses,
        id_prefix="whs",
        attributes={
            "name": FakerSampler(provider="city"),
            "country": CategorySampler(values=COUNTRIES, weights=COUNTRY_WEIGHTS),
            "capacity_pallets": LognormalSampler(mu=9.0, sigma=0.8, decimals=0),
            "automated": CategorySampler(values=[True, False], weights=[34, 66]),
        },
    )
    customer = NodeType(
        name="Customer",
        count=config.num_customers,
        id_prefix="cst",
        attributes={
            "name": FakerSampler(provider="company"),
            "segment": CategorySampler(values=SEGMENTS, weights=SEGMENT_WEIGHTS),
            # Spend drives how often a customer is delivered to, and it is
            # heavy-tailed the way real account bases are.
            "annual_spend": LognormalSampler(mu="@region.log_demand", sigma=1.4, decimals=0),
        },
    )
    product = NodeType(
        name="Product",
        count=config.num_products,
        id_prefix="prd",
        attributes={
            "name": FakerSampler(provider="word"),
            "family": CategorySampler(values=PRODUCT_FAMILIES),
            "unit_cost": LognormalSampler(mu="@category.log_unit_cost", sigma=0.5, decimals=2),
            "lead_time_class": CategorySampler(
                values=["short", "medium", "long"], weights=[38, 42, 20]
            ),
            "hazardous": CategorySampler(values=[True, False], weights=[7, 93]),
        },
    )
    carrier = NodeType(
        name="Carrier",
        count=config.num_carriers,
        id_prefix="car",
        attributes={
            "name": FakerSampler(provider="company"),
            "mode": CategorySampler(values=MODES, weights=MODE_WEIGHTS),
            "on_time_rate": GaussianSampler(mean=0.9, sd=0.07, low=0.4, high=1.0, decimals=3),
            "cost_per_km": UniformSampler(low=0.4, high=3.5, decimals=2),
        },
    )
    return GraphSchema(
        name="supply_chain",
        latent=[region, category],
        nodes=[supplier, plant, warehouse, customer, product, carrier],
        topology=UniformTopology(),
    )


def build_nodes(
    config: SupplyChainConfig, streams: Streams, shard_size: int, workers: int = 1
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


def add_addresses(
    tables: dict[str, pl.DataFrame], streams: Streams
) -> dict[str, pl.DataFrame]:
    """Street addresses on the organisations, shared the way a register
    shares them.

    Suppliers, plants and warehouses draw from one pool, so a supplier can
    sit at the same address as another supplier and occasionally at a
    plant's. That reuse is the point: measured on a real register the median
    address holds one organisation and the top 1% hold over half of them,
    which is what stops address being an identifier.
    Customers get their own pool, because a retail base is not registered
    through agents.
    """
    business = [label for label in ("Supplier", "Plant", "Warehouse") if label in tables]
    rows = sum(tables[label].height for label in business)
    columns = shared_addresses(streams, rows)
    at = 0
    for label in business:
        height = tables[label].height
        tables[label] = tables[label].with_columns(
            [pl.Series(name, values[at : at + height]) for name, values in columns.items()]
        )
        at += height
    if "Customer" in tables:
        retail = shared_addresses(streams, tables["Customer"].height, RETAIL)
        tables["Customer"] = tables["Customer"].with_columns(
            [pl.Series(name, values) for name, values in retail.items()]
        )
    return tables


#: The node types that are companies, and so are named like companies. A
#: warehouse is a site rather than a legal entity and keeps its place name.
COMPANY_TYPES = ("Supplier", "Plant", "Customer", "Carrier")


def add_names(tables: dict[str, pl.DataFrame], streams: Streams) -> dict[str, pl.DataFrame]:
    """Company names with a register's legal forms and a register's reuse.

    Faker's company provider is a thousand surnames with ``PLC`` and ``and
    Sons`` on the end: at this pack's scale 81% of the names had no legal form
    at all and 5.6% were LLCs, against a measured 11.9% and 53.9%, and at
    200,000 rows one stem carried 1,451 companies.
    :func:`graphfaker.engine.names.company_names` is the measured version.
    """
    for label in COMPANY_TYPES:
        if label in tables:
            tables[label] = tables[label].with_columns(
                pl.Series("name", company_names(streams, tables[label].height))
            )
    return tables


#: Relationship per register role. The same vocabulary the loader produces
#: for a real register, so a generated graph and a loaded one can be queried
#: the same way.
PERSON_RELATIONSHIPS = {"Contact": "CONTACT_AT", "Executive": "EXECUTIVE_AT"}


def add_people(
    tables: dict[str, pl.DataFrame], streams: Streams
) -> tuple[dict[str, pl.DataFrame], dict[str, pl.DataFrame]]:
    """Officers and contacts, on the companies that have any.

    Most companies have nobody: 4.5% of a register's companies have a single
    person attached, and the ones that do have a median of one and a 99th
    percentile of 151. A generated dataset that gives every company a board
    gets the common case wrong and the tail wrong at the same time; see
    :mod:`graphfaker.engine.people`.

    People get a home address from the retail pool, because a director lives
    somewhere rather than being registered through an agent, and sometimes
    that address is the company's.
    """
    companies = [
        str(value)
        for label in COMPANY_TYPES
        if label in tables
        for value in tables[label]["id"].to_list()
    ]
    people, links = attach(streams, companies)
    if not people:
        return tables, {}

    # An explicit schema, because the first rows are the ones with no name
    # parts recorded: inferring from them types the columns as null and the
    # first person who does have a surname fails to append.
    frame = pl.DataFrame(
        people,
        schema={
            "id": pl.String, "name": pl.String,
            "first": pl.String, "middle": pl.String, "last": pl.String,
        },
    )
    addresses = shared_addresses(streams, frame.height, RETAIL)
    tables["Person"] = frame.with_columns(
        [pl.Series(name, values) for name, values in addresses.items()]
    )

    edges: dict[str, pl.DataFrame] = {}
    for role, relationship in PERSON_RELATIONSHIPS.items():
        rows = [link for link in links if link["role"] == role]
        if rows:
            edges[relationship] = pl.DataFrame(
                {
                    SOURCE: [str(link["person"]) for link in rows],
                    TARGET: [str(link["company"]) for link in rows],
                    "role": [role] * len(rows),
                }
            )
    return tables, edges


def add_onboarding_dates(
    suppliers: pl.DataFrame, config: SupplyChainConfig, rng: np.random.Generator
) -> pl.DataFrame:
    """``onboarded_at``, strictly before the period.

    Most suppliers have been on the books for years; a few are new every
    month. A phantom supplier is onboarded days before its first invoice,
    which is the loudest single signal in real procurement data and is left
    in deliberately: this distribution is what it stands out against.
    """
    age_days = rng.exponential(scale=900.0, size=suppliers.height).astype(np.int64) + 7
    onboarded = [config.period_start - dt.timedelta(days=int(d)) for d in age_days]
    return suppliers.with_columns(pl.Series("onboarded_at", onboarded, dtype=pl.Date))


def _heavy_tailed_counts(
    rng: np.random.Generator, size: int, mean: float, minimum: int = 1
) -> np.ndarray:
    """Per-node out-degrees with a long tail, averaging about ``mean``."""
    draws = rng.lognormal(mean=np.log(max(mean, 0.3)) - 0.5, sigma=1.0, size=size)
    return np.maximum(minimum, np.rint(draws)).astype(np.int64)


def _weighted_targets(
    rng: np.random.Generator, counts: np.ndarray, weights: np.ndarray
) -> np.ndarray:
    """One target per edge, drawn in proportion to ``weights``.

    Preference, not uniformity: big plants buy from more suppliers, popular
    warehouses stock more products. Drawn in one call for the whole column.
    """
    total = int(counts.sum())
    if total == 0 or len(weights) == 0:
        return np.empty(0, dtype=np.int64)
    probabilities = weights / weights.sum()
    return rng.choice(len(weights), size=total, p=probabilities)


def _edges(sources: np.ndarray, targets: np.ndarray, counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return np.repeat(sources, counts), targets


def structural_edges(
    config: SupplyChainConfig,
    nodes: dict[str, pl.DataFrame],
    rng: np.random.Generator,
) -> dict[str, pl.DataFrame]:
    """The network itself: who can supply whom, who makes and stocks what,
    who serves which customers, and which carrier runs which lane.

    Every relationship here is a contract rather than an event. The events
    happen on top of them in :mod:`graphfaker.domains.supply_chain.process`,
    which is what makes an order plausible: you cannot order from a supplier
    you have no contract with.
    """
    suppliers, plants = nodes["Supplier"], nodes["Plant"]
    warehouses, customers = nodes["Warehouse"], nodes["Customer"]
    products, carriers = nodes["Product"], nodes["Carrier"]

    tier = suppliers["tier"].to_numpy()
    tier1 = np.flatnonzero(tier == 1)
    plant_weight = plants["capacity_units"].to_numpy().astype(np.float64) + 1.0

    # SUPPLIES: tier-1 suppliers to plants, with the lead time the contract
    # promises. Everything downstream compares against this number, so late
    # is a property of the relationship and not of the calendar.
    counts = _heavy_tailed_counts(rng, len(tier1), SUPPLIES_PER_SUPPLIER)
    targets = _weighted_targets(rng, counts, plant_weight)
    src, dst = _edges(tier1, targets, counts)
    contracted = np.rint(rng.lognormal(mean=2.6, sigma=0.5, size=len(src))).astype(np.int64)
    supplies = pl.DataFrame(
        {
            SOURCE: suppliers[ID].gather(src),
            TARGET: plants[ID].gather(dst),
            "contracted_lead_days": np.clip(contracted, 1, 120),
            "unit_price_index": np.round(rng.normal(1.0, 0.12, size=len(src)).clip(0.6, 1.8), 3),
        }
    ).unique(subset=[SOURCE, TARGET], keep="first", maintain_order=True)

    # SUBCONTRACTS: tier 1 buys from tier 2, tier 2 from tier 3. This is the
    # part of a real network nobody can see past, and where a counterfeit
    # substitution lives.
    #
    # It is not a clean hierarchy, and it matters that it is not. Suppliers
    # at the same level co-manufacture, and parts come back up the chain as
    # sub-assemblies, so the graph contains loops. Without them every closed
    # circle of goods would be anomalous by construction and a kiting ring
    # would be found by looking for the only cycle in the data rather than
    # by telling it from the legitimate ones.
    subcontract_src: list[np.ndarray] = []
    subcontract_dst: list[np.ndarray] = []
    for upper, lower in ((1, 2), (2, 3)):
        above, below = np.flatnonzero(tier == upper), np.flatnonzero(tier == lower)
        if not len(above) or not len(below):
            continue
        counts = _heavy_tailed_counts(rng, len(above), SUBCONTRACTS_PER_SUPPLIER)
        capacity = suppliers["capacity_units"].to_numpy().astype(np.float64)[below] + 1.0
        chosen = below[_weighted_targets(rng, counts, capacity)]
        subcontract_src.append(np.repeat(above, counts))
        subcontract_dst.append(chosen)
    # Lateral and upward relationships: co-manufacturing between peers and
    # sub-assemblies going back up. These are what put cycles in the graph.
    for tier_level in (1, 2, 3):
        peers = np.flatnonzero(tier == tier_level)
        if len(peers) < 2:
            continue
        count = max(1, int(len(peers) * LATERAL_RATE))
        subcontract_src.append(rng.choice(peers, size=count))
        subcontract_dst.append(rng.choice(peers, size=count))
    lower_tiers, upper_tiers = np.flatnonzero(tier >= 2), np.flatnonzero(tier == 1)
    if len(lower_tiers) and len(upper_tiers):
        count = max(1, int(len(lower_tiers) * UPWARD_RATE))
        subcontract_src.append(rng.choice(lower_tiers, size=count))
        subcontract_dst.append(rng.choice(upper_tiers, size=count))

    if subcontract_src:
        src = np.concatenate(subcontract_src)
        dst = np.concatenate(subcontract_dst)
    else:
        src = dst = np.empty(0, dtype=np.int64)
    subcontracts = pl.DataFrame(
        {
            SOURCE: suppliers[ID].gather(src),
            TARGET: suppliers[ID].gather(dst),
            "share": np.round(rng.uniform(0.05, 0.9, size=len(src)), 3),
        }
    ).filter(pl.col(SOURCE) != pl.col(TARGET)).unique(subset=[SOURCE, TARGET], keep="first", maintain_order=True)

    # PRODUCES and STOCKS: which plant makes what, which warehouse holds it.
    counts = _heavy_tailed_counts(rng, plants.height, PRODUCTS_PER_PLANT, minimum=1)
    product_weight = np.ones(products.height)
    dst = _weighted_targets(rng, counts, product_weight)
    src = np.repeat(np.arange(plants.height), counts)
    produces = pl.DataFrame(
        {
            SOURCE: plants[ID].gather(src),
            TARGET: products[ID].gather(dst),
            "units_per_day": np.rint(rng.lognormal(5.5, 0.9, size=len(src))).astype(np.int64),
        }
    ).unique(subset=[SOURCE, TARGET], keep="first", maintain_order=True)

    counts = _heavy_tailed_counts(rng, warehouses.height, PRODUCTS_PER_WAREHOUSE, minimum=2)
    dst = _weighted_targets(rng, counts, product_weight)
    src = np.repeat(np.arange(warehouses.height), counts)
    stocks = pl.DataFrame(
        {
            SOURCE: warehouses[ID].gather(src),
            TARGET: products[ID].gather(dst),
            "reorder_point": np.rint(rng.lognormal(4.2, 0.8, size=len(src))).astype(np.int64),
            "safety_stock": np.rint(rng.lognormal(3.6, 0.9, size=len(src))).astype(np.int64),
        }
    ).unique(subset=[SOURCE, TARGET], keep="first", maintain_order=True)

    # SERVES: every customer is served by one warehouse, preferring its own
    # region, because that is how distribution networks are drawn up.
    customer_region = customers[REGION].to_numpy()
    warehouse_region = warehouses[REGION].to_numpy()
    serving = np.empty(customers.height, dtype=np.int64)
    for region in np.unique(customer_region):
        local = np.flatnonzero(warehouse_region == region)
        members = np.flatnonzero(customer_region == region)
        pool = local if len(local) else np.arange(warehouses.height)
        serving[members] = pool[rng.integers(0, len(pool), size=len(members))]
    serves = pl.DataFrame(
        {
            SOURCE: warehouses[ID].gather(serving),
            TARGET: customers[ID],
            "service_days": np.clip(np.rint(rng.lognormal(0.9, 0.6, size=customers.height)), 1, 21).astype(np.int64),
        }
    )

    # HAULS: carriers run lanes into warehouses. A few carriers move most of
    # the freight, which is why one carrier's bad month is visible.
    counts = _heavy_tailed_counts(rng, carriers.height, LANES_PER_CARRIER, minimum=1)
    warehouse_weight = warehouses["capacity_pallets"].to_numpy().astype(np.float64) + 1.0
    dst = _weighted_targets(rng, counts, warehouse_weight)
    src = np.repeat(np.arange(carriers.height), counts)
    modes = carriers["mode"].to_numpy()
    transit = np.array([MODE_TRANSIT[m] for m in modes[src]], dtype=np.float64)
    hauls = pl.DataFrame(
        {
            SOURCE: carriers[ID].gather(src),
            TARGET: warehouses[ID].gather(dst),
            "transit_days": np.round(transit * rng.lognormal(0.0, 0.25, size=len(src)), 1),
        }
    ).unique(subset=[SOURCE, TARGET], keep="first", maintain_order=True)

    return {
        "SUPPLIES": supplies,
        "SUBCONTRACTS": subcontracts,
        "PRODUCES": produces,
        "STOCKS": stocks,
        "SERVES": serves,
        "HAULS": hauls,
    }
