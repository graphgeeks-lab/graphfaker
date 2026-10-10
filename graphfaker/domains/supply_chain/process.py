"""The legitimate event process: orders, shipments, invoices, deliveries.

Everything an injected pattern has to hide in is made here, and the whole
design follows from one observation: in procurement data almost nothing is
suspicious on its own. An invoice is a number, a late shipment is weather, a
small order is a small order. What makes fraud findable is how those events
sit against the ordinary rhythm of the network, so the ordinary rhythm has to
be real.

What that means in practice:

* **Replenishment is scheduled.** A contract reorders weekly, fortnightly or
  monthly, and keeps to it. Regular intervals are most of the volume, which
  is why an order that arrives off-schedule means something.
* **Ad hoc demand is seasonal.** Quarter ends spike, because budgets do, and
  weekends are quiet. A pattern that ignores the calendar stands out, and one
  that follows it does not.
* **Lead times have a tail.** A lane's delivery time is lognormal around what
  the contract promised, stretched by the region's exposure to delay. Late is
  relative to a promise, not to a constant.
* **Amounts follow the product and the lane.** Quantity times unit cost times
  the region's cost index, with noise, so the amount on an invoice is a
  statement about what was bought rather than a free number.

Events are produced a block of contracts at a time so the arrays in flight
stay the same size whatever the scale, which is what lets a full-size network
generate without holding every event at once.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator

import numpy as np
import polars as pl

from graphfaker.backends.tables import ID, SOURCE, TARGET
from graphfaker.domains.supply_chain.config import SupplyChainConfig

ORDERS = "ORDERS"
SHIPS = "SHIPS"
INVOICES = "INVOICES"
INTERCOMPANY = "INTERCOMPANY"
DELIVERS = "DELIVERS"

CHANNELS = (ORDERS, SHIPS, INVOICES, INTERCOMPANY, DELIVERS)

#: Columns every event channel carries, in this order.
EVENT_COLUMNS = (SOURCE, TARGET, "event_id", "timestamp", "amount", "quantity", "reference", "scheduled")

#: Share of the event budget that is purchase orders. Each order produces a
#: shipment (sometimes two, when a delivery is partial) and an invoice, so
#: this number times about three is the procurement half of the budget.
ORDER_SHARE = 0.28
#: Outbound deliveries to customers, the other half of the network.
DELIVERY_SHARE = 0.12
#: Movements between suppliers that subcontract to each other. Routine in
#: any group, and the reason neither a consignment loop nor a circle of
#: invoices between related parties is suspicious on its own: ordinary
#: suppliers have trading partners too.
INTERCOMPANY_SHARE = 0.06

#: Reorder intervals in days, and how common each one is.
SCHEDULES = np.array([7, 14, 30])
SCHEDULE_WEIGHTS = np.array([0.34, 0.28, 0.38])

#: Share of orders that come from a schedule rather than ad hoc demand.
SCHEDULED_SHARE = 0.62

#: Probability a shipment arrives in two parts instead of one.
PARTIAL_DELIVERY_RATE = 0.12

#: Share of orders placed at a negotiated round figure rather than at
#: quantity times unit cost. Procurement does this constantly: a lot price, a
#: budget line, a quote rounded to the nearest five hundred. It also means a
#: round number is not evidence of anything, which is the point: an invented
#: invoice is round because somebody typed it, and so are thousands of real
#: ones.
ROUND_PRICING_RATE = 0.22
ROUND_PRICING_STEP = 500.0

#: Share of invoices raised with no shipment behind them, for the ordinary
#: reasons: tooling, services, freight charges, goods invoiced on acceptance
#: and delivered after the period. Without this, an invoice with no goods
#: behind it would be proof of fraud rather than the weak signal it is in a
#: real ledger, and the phantom supplier play would be a tutorial.
SERVICE_INVOICE_RATE = 0.07
#: Share of shipments nobody has billed yet when the period ends.
UNBILLED_SHIPMENT_RATE = 0.05

#: Share of inter-company movements that run back up the chain: returns,
#: rework, stock rebalancing. Both directions are ordinary between related
#: companies, which is what stops a reversed movement being a signal.
REVERSE_MOVEMENT_RATE = 0.38

#: Working-day profile. Procurement happens on weekdays, and the last days of
#: a quarter are when the budget gets spent.
WEEKDAY_WEIGHTS = np.array([1.15, 1.2, 1.15, 1.1, 0.95, 0.25, 0.15])
QUARTER_END_BOOST = 2.4
QUARTER_END_DAYS = 6

#: Rows per block of events. The arrays in flight are a few hundred megabytes
#: at any scale, the same approach the fraud pack uses.
BLOCK_EVENTS = 2_000_000


class Population:
    """The network as integer arrays, which is how the process reads it.

    Ids are kept as the node tables' own Series and only materialised when a
    frame is built, so a run at full scale never holds a Python string per
    entity.
    """

    def __init__(self, tables: dict[str, pl.DataFrame], config: SupplyChainConfig):
        self.config = config
        self.ids = {name: frame[ID] for name, frame in tables.items()}
        self.n_suppliers = tables["Supplier"].height
        self.n_plants = tables["Plant"].height
        self.n_products = tables["Product"].height
        self.period_start = np.datetime64(config.period_start, "s")
        self.period_days = config.period_days
        self.period_end = self.period_start + np.timedelta64(config.period_days * 86_400, "s")

        self.supplier_tier = tables["Supplier"]["tier"].to_numpy()
        self.supplier_on_time = tables["Supplier"]["on_time_rate"].to_numpy().astype(np.float64)
        self.supplier_quality = tables["Supplier"]["quality_score"].to_numpy().astype(np.float64)
        self.supplier_region = tables["Supplier"]["region"].to_numpy()
        self.product_cost = tables["Product"]["unit_cost"].to_numpy().astype(np.float64)
        self.customer_spend = tables["Customer"]["annual_spend"].to_numpy().astype(np.float64)
        self.onboarded = (
            tables["Supplier"]["onboarded_at"].to_numpy().astype("datetime64[s]")
            if "onboarded_at" in tables["Supplier"].columns
            else np.full(self.n_suppliers, self.period_start - np.timedelta64(365 * 86_400, "s"))
        )

        # Region parameters, indexed by the region each entity belongs to.
        self.region_delay = _region_param(tables, "delay_bias")
        self.region_cost = _region_param(tables, "cost_index", default=1.0)

    def index(self, label: str, ids: pl.Series) -> np.ndarray:
        """Positions of ``ids`` in ``label``'s table."""
        lookup = {value: i for i, value in enumerate(self.ids[label].to_list())}
        return np.fromiter((lookup[v] for v in ids.to_list()), dtype=np.int64, count=len(ids))


def _region_param(tables: dict[str, pl.DataFrame], param: str, default: float = 0.0) -> dict[int, float]:
    """Latent region parameters are written onto the nodes, not kept apart,
    so read them back off whichever table has them."""
    frame = tables.get("Supplier")
    if frame is None or param not in frame.columns:
        return {}
    pairs = frame.select(["region", param]).unique(subset=["region"])
    return dict(zip(pairs["region"].to_list(), pairs[param].to_list(), strict=False)) if pairs.height else {default: default}


class Contracts:
    """The supply relationships events happen on, as arrays.

    One row per ``SUPPLIES`` edge: who sells to whom, what the contract
    promises, which product the orders are for, and how often the plant
    reorders. Every order in the run points at one of these, which is what
    makes an order from a supplier nobody has a contract with visible.
    """

    def __init__(
        self,
        pop: Population,
        supplies: pl.DataFrame,
        produces: pl.DataFrame,
        rng: np.random.Generator,
    ):
        self.supplier = pop.index("Supplier", supplies[SOURCE])
        self.plant = pop.index("Plant", supplies[TARGET])
        self.lead_days = supplies["contracted_lead_days"].to_numpy().astype(np.float64)
        self.price_index = supplies["unit_price_index"].to_numpy().astype(np.float64)
        self.size = len(self.supplier)

        # Which product a contract is for: one of the ones the plant makes,
        # so an order is for something the plant could actually use.
        by_plant: dict[int, list[int]] = {}
        if produces.height:
            plants = pop.index("Plant", produces[SOURCE])
            products = pop.index("Product", produces[TARGET])
            for plant, product in zip(plants.tolist(), products.tolist(), strict=False):
                by_plant.setdefault(plant, []).append(product)
        fallback = np.arange(pop.n_products)
        self.product = np.array(
            [
                rng.choice(by_plant.get(int(plant), fallback)) if pop.n_products else 0
                for plant in self.plant
            ],
            dtype=np.int64,
        )
        self.schedule = rng.choice(SCHEDULES, size=self.size, p=SCHEDULE_WEIGHTS)
        # Order size in units, per contract rather than per order: a plant
        # that orders a thousand fasteners orders a thousand every time.
        self.quantity = np.maximum(1, np.rint(rng.lognormal(5.0, 0.9, size=self.size))).astype(np.int64)


def day_weights(pop: Population) -> np.ndarray:
    """Relative order volume for each day of the period.

    Weekdays over weekends, and the last few days of a calendar quarter over
    everything, which is the rhythm a split-order pattern has to blend into:
    a cluster of purchases at quarter end is normal, and that is exactly why
    it is a good place to hide one.
    """
    days = np.arange(pop.period_days)
    dates = pop.period_start.astype("datetime64[D]") + days
    weekday = (dates.astype("datetime64[D]").astype(int) + 3) % 7  # 1970-01-01 was a Thursday
    weights = WEEKDAY_WEIGHTS[weekday]
    months = dates.astype("datetime64[M]").astype(int) % 12
    day_of_month = (dates - dates.astype("datetime64[M]")).astype(int)
    month_length = (
        (dates.astype("datetime64[M]") + np.timedelta64(1, "M")).astype("datetime64[D]")
        - dates.astype("datetime64[M]").astype("datetime64[D]")
    ).astype(int)
    quarter_end_month = np.isin(months, [2, 5, 8, 11])
    near_end = day_of_month >= (month_length - QUARTER_END_DAYS)
    weights = weights * np.where(quarter_end_month & near_end, QUARTER_END_BOOST, 1.0)
    return weights / weights.sum()


def _stamps(rng: np.random.Generator, pop: Population, weights: np.ndarray, size: int) -> np.ndarray:
    """``size`` timestamps inside the period, following the day profile."""
    if size <= 0:
        return np.empty(0, dtype="datetime64[s]")
    days = rng.choice(len(weights), size=size, p=weights)
    # Office hours, so that a 3am order is a thing a detector could notice.
    seconds = (8 * 3600 + rng.integers(0, 10 * 3600, size=size)).astype(np.int64)
    return pop.period_start + (days.astype(np.int64) * 86_400 + seconds).astype("timedelta64[s]")


def _frame(
    source: pl.Series,
    target: pl.Series,
    event_ids: list[str],
    stamps: np.ndarray,
    amount: np.ndarray,
    quantity: np.ndarray,
    reference: list[str] | pl.Series,
    scheduled: np.ndarray,
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            SOURCE: source,
            TARGET: target,
            "event_id": event_ids,
            "timestamp": stamps.astype("datetime64[us]"),
            "amount": np.round(amount, 2),
            "quantity": quantity.astype(np.int64),
            "reference": reference,
            "scheduled": scheduled,
        }
    )


def _blocks(total: int, per_block: int) -> Iterator[tuple[int, int]]:
    start = 0
    while start < total:
        stop = min(total, start + per_block)
        yield start, stop
        start = stop


def legitimate_events(
    rng: np.random.Generator,
    pop: Population,
    contracts: Contracts,
    structure: dict[str, pl.DataFrame],
    budget: int,
    *,
    finish: Callable[[pl.DataFrame], pl.DataFrame] | None = None,
    as_parts: bool = False,
) -> dict[str, pl.DataFrame] | dict[str, list[pl.DataFrame]]:
    """Every legitimate event, per channel, within ``budget``.

    ``finish`` is applied to each part as it is produced, which is how the
    patterns strip the ordinary activity of the entities they recruit without
    the whole channel ever existing twice.
    """
    parts: dict[str, list[pl.DataFrame]] = {channel: [] for channel in CHANNELS}
    weights = day_weights(pop)
    counter = _Counter()

    n_orders = max(1, int(budget * ORDER_SHARE))
    for start, stop in _blocks(n_orders, BLOCK_EVENTS):
        block = _order_block(rng, pop, contracts, weights, stop - start, counter)
        for channel, frame in block.items():
            kept = finish(frame) if finish is not None else frame
            if kept.height:
                parts[channel].append(kept)

    deliveries = _delivery_block(rng, pop, structure, weights, int(budget * DELIVERY_SHARE), counter)
    if deliveries is not None:
        kept = finish(deliveries) if finish is not None else deliveries
        if kept.height:
            parts[DELIVERS].append(kept)

    moves = _intercompany_block(rng, pop, structure, weights, int(budget * INTERCOMPANY_SHARE), counter)
    if moves is not None:
        kept = finish(moves) if finish is not None else moves
        if kept.height:
            parts[INTERCOMPANY].append(kept)

    if as_parts:
        return parts
    return {
        channel: pl.concat(frames, rechunk=False) if frames else _empty()
        for channel, frames in parts.items()
    }


def _empty() -> pl.DataFrame:
    return _frame(
        pl.Series([], dtype=pl.String),
        pl.Series([], dtype=pl.String),
        [],
        np.array([], dtype="datetime64[s]"),
        np.array([], dtype=np.float64),
        np.array([], dtype=np.int64),
        [],
        np.array([], dtype=bool),
    )


class _Counter:
    """Event ids are unique across channels and assigned in generation order.

    They are not the time order: that is what the ``tx_id`` equivalent in the
    other packs carries, and here the assembly renumbers everything at the
    end. Until then an id only has to be unique.
    """

    def __init__(self) -> None:
        self.value = 0

    def take(self, n: int) -> range:
        start = self.value
        self.value += n
        return range(start, self.value)


def _order_block(
    rng: np.random.Generator,
    pop: Population,
    contracts: Contracts,
    weights: np.ndarray,
    size: int,
    counter: _Counter,
) -> dict[str, pl.DataFrame]:
    """One block of orders, and the shipments and invoices they cause.

    The three are produced together because they are one story: an order is
    placed, goods arrive against it late or on time, and an invoice follows
    the goods. Keeping them together is what makes an invoice with no
    shipment behind it a thing that can be looked for.
    """
    if contracts.size == 0 or size <= 0:
        return {}
    # Which contract each order belongs to. Scheduled orders are spread over
    # the contracts that have a short cycle, ad hoc ones over all of them.
    scheduled_count = int(size * SCHEDULED_SHARE)
    cycle_weight = 1.0 / contracts.schedule.astype(np.float64)
    scheduled_contract = rng.choice(contracts.size, size=scheduled_count, p=cycle_weight / cycle_weight.sum())
    adhoc_contract = rng.integers(0, contracts.size, size=size - scheduled_count)
    contract = np.concatenate([scheduled_contract, adhoc_contract])
    scheduled = np.concatenate([np.ones(scheduled_count, bool), np.zeros(size - scheduled_count, bool)])

    # Scheduled orders land on the contract's cycle; ad hoc ones follow the
    # calendar's own rhythm.
    offsets = rng.integers(0, max(1, pop.period_days), size=size)
    cycle = contracts.schedule[contract]
    on_cycle = (offsets // cycle) * cycle + rng.integers(0, 2, size=size)
    day = np.where(scheduled, np.clip(on_cycle, 0, pop.period_days - 1), rng.choice(len(weights), size=size, p=weights))
    seconds = (8 * 3600 + rng.integers(0, 10 * 3600, size=size)).astype(np.int64)
    order_ts = pop.period_start + (day.astype(np.int64) * 86_400 + seconds).astype("timedelta64[s]")

    supplier = contracts.supplier[contract]
    plant = contracts.plant[contract]
    product = contracts.product[contract]
    quantity = np.maximum(
        1, np.rint(contracts.quantity[contract] * rng.lognormal(0.0, 0.35, size=size))
    ).astype(np.int64)
    unit_cost = pop.product_cost[product] if pop.n_products else np.ones(size)
    cost_index = np.array([pop.region_cost.get(int(r), 1.0) for r in pop.supplier_region[supplier]])
    amount = quantity * unit_cost * contracts.price_index[contract] * cost_index
    rounded = rng.random(size) < ROUND_PRICING_RATE
    amount = np.where(
        rounded,
        np.maximum(ROUND_PRICING_STEP, np.rint(amount / ROUND_PRICING_STEP) * ROUND_PRICING_STEP),
        amount,
    )

    order_ids = [f"evt_{i}" for i in counter.take(size)]
    orders = _frame(
        pop.ids["Plant"].gather(plant),
        pop.ids["Supplier"].gather(supplier),
        order_ids,
        order_ts,
        amount,
        quantity,
        pop.ids["Product"].gather(product) if pop.n_products else [""] * size,
        scheduled,
    )

    # Shipments: the contract promises a lead time and the lane delivers
    # around it, worse where the region is exposed. A late shipment is late
    # against its own promise.
    delay_bias = np.array([pop.region_delay.get(int(r), 0.0) for r in pop.supplier_region[supplier]])
    punctuality = pop.supplier_on_time[supplier]
    lead = contracts.lead_days[contract] * rng.lognormal(
        mean=0.05 + delay_bias * 0.2 + (1.0 - punctuality) * 0.6, sigma=0.35, size=size
    )
    lead = np.clip(lead, 1.0, 365.0)
    ship_ts = order_ts + (lead * 86_400).astype(np.int64).astype("timedelta64[s]")

    # Some deliveries arrive in two parts, which is ordinary and is also what
    # a split pattern looks like if you only count rows.
    partial = rng.random(size) < PARTIAL_DELIVERY_RATE
    ship_quantity = np.where(partial, np.maximum(1, quantity // 2), quantity)
    ship_ids = [f"evt_{i}" for i in counter.take(size)]
    ships = _frame(
        pop.ids["Supplier"].gather(supplier),
        pop.ids["Plant"].gather(plant),
        ship_ids,
        ship_ts,
        amount * (ship_quantity / np.maximum(quantity, 1)),
        ship_quantity,
        order_ids,
        scheduled,
    )

    # Invoices follow the goods, a few days behind.
    invoice_ts = ship_ts + (rng.integers(1, 6, size=size) * 86_400).astype("timedelta64[s]")
    invoice_ids = [f"evt_{i}" for i in counter.take(size)]
    # An invoice bills what was agreed, so a negotiated round figure stays
    # round all the way through. Anything else would make roundness on an
    # invoice mean fraud by construction.
    invoice_amount = np.where(rounded, amount, amount * rng.normal(1.0, 0.01, size=size).clip(0.95, 1.05))
    invoices = _frame(
        pop.ids["Supplier"].gather(supplier),
        pop.ids["Plant"].gather(plant),
        invoice_ids,
        invoice_ts,
        invoice_amount,
        quantity,
        ship_ids,
        scheduled,
    )

    # Not every order produces both a shipment and an invoice inside the
    # window, which is what keeps "invoice with no shipment" a weak signal.
    billed = rng.random(size) >= UNBILLED_SHIPMENT_RATE
    shipped = rng.random(size) >= SERVICE_INVOICE_RATE
    return {
        ORDERS: orders.filter(order_ts <= pop.period_end),
        SHIPS: ships.filter((ship_ts <= pop.period_end) & shipped),
        INVOICES: invoices.filter((invoice_ts <= pop.period_end) & billed),
    }


def _delivery_block(
    rng: np.random.Generator,
    pop: Population,
    structure: dict[str, pl.DataFrame],
    weights: np.ndarray,
    size: int,
    counter: _Counter,
) -> pl.DataFrame | None:
    """Outbound deliveries, warehouse to customer, in proportion to spend."""
    serves = structure.get("SERVES")
    if serves is None or serves.height == 0 or size <= 0:
        return None
    spend = pop.customer_spend
    probability = spend / spend.sum() if spend.sum() else None
    chosen = rng.choice(serves.height, size=size, p=probability if probability is not None and len(probability) == serves.height else None)
    stamps = _stamps(rng, pop, weights, size)
    units = np.maximum(1, np.rint(rng.lognormal(2.4, 0.9, size=size))).astype(np.int64)
    amount = units * rng.lognormal(3.0, 0.6, size=size)
    return _frame(
        serves[SOURCE].gather(chosen),
        serves[TARGET].gather(chosen),
        [f"evt_{i}" for i in counter.take(size)],
        stamps,
        amount,
        units,
        [""] * size,
        np.zeros(size, bool),
    )


def _intercompany_block(
    rng: np.random.Generator,
    pop: Population,
    structure: dict[str, pl.DataFrame],
    weights: np.ndarray,
    size: int,
    counter: _Counter,
) -> pl.DataFrame | None:
    """Movements between suppliers that subcontract to each other.

    Rare, legitimate, and the reason a loop of goods between related parties
    is not automatically a kiting ring.
    """
    subcontracts = structure.get("SUBCONTRACTS")
    if subcontracts is None or subcontracts.height == 0 or size <= 0:
        return None
    chosen = rng.integers(0, subcontracts.height, size=size)
    stamps = _stamps(rng, pop, weights, size)
    quantity = np.maximum(1, np.rint(rng.lognormal(3.6, 1.0, size=size))).astype(np.int64)
    amount = quantity * rng.lognormal(3.2, 0.7, size=size)
    # Goods go down the chain and returns, rework and rebalancing come back
    # up it, so a pair of related companies ships both ways. Without this,
    # any movement against the subcontracting direction would be proof of
    # something, and a circle of goods would be found by looking for one
    # reversed edge rather than by finding the circle.
    upstream = rng.random(size) < REVERSE_MOVEMENT_RATE
    source = np.where(upstream, subcontracts[TARGET].gather(chosen).to_numpy(), subcontracts[SOURCE].gather(chosen).to_numpy())
    target = np.where(upstream, subcontracts[SOURCE].gather(chosen).to_numpy(), subcontracts[TARGET].gather(chosen).to_numpy())
    return _frame(
        pl.Series(SOURCE, source),
        pl.Series(TARGET, target),
        [f"evt_{i}" for i in counter.take(size)],
        stamps,
        amount,
        quantity,
        [""] * size,
        np.zeros(size, bool),
    )


def to_date(value: np.datetime64) -> dt.date:
    return value.astype("datetime64[D]").astype(dt.date)
