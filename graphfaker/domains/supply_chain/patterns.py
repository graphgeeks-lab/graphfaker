"""Injected, labelled patterns, and the legitimate structures that imitate them.

Each shape is something a procurement team investigates, and each one is
paired with a perfectly innocent thing that leaves the same trace. That
pairing is the whole design:

===========================  ====================  ==================================
shape                        twin                  what both look like
===========================  ====================  ==================================
``phantom_supplier``         ``disruption_cascade``  invoices with no goods behind them
``invoice_kiting``           ``consignment_loop``    value going round a circle of suppliers
``split_orders``             ``blanket_calloffs``    many purchases just under the limit
``counterfeit_injection``    ``quality_incident``    a lane priced below what the part costs
===========================  ====================  ==================================

A rule that catches the left column catches the right one too, and the right
column is labelled innocent, so precision is measurable rather than assumed.
Hardness decides how much of each signature survives: ``amount_blend`` draws
the amounts from the legitimate distribution instead of round numbers,
``timing_spread_days`` stretches a burst into a background, ``size_scale``
shrinks the ring, and ``threshold_margin`` decides how close to the approval
limit a split order dares to sit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count

import numpy as np
import polars as pl

from graphfaker.domains.supply_chain.config import CATALOG, HardnessProfile, SupplyChainConfig
from graphfaker.domains.supply_chain.process import (
    EVENT_COLUMNS,
    INTERCOMPANY,
    INVOICES,
    ORDERS,
    ROUND_PRICING_RATE,
    ROUND_PRICING_STEP,
    SHIPS,
    Contracts,
    Population,
)
from graphfaker.engine.injection import (
    InjectionContext,
    round_robin_decoys,
    run_catalog,
    stripped_members,
)
from graphfaker.engine.injection import Pattern as BasePattern


@dataclass
class Pattern(BasePattern):
    """An injected procurement structure, in the pack's own words."""

    @property
    def play(self) -> str:
        return self.name

    @property
    def is_fraud(self) -> bool:
        return self.labelled

    @property
    def n_events(self) -> int:
        return self.events


@dataclass
class Injection:
    """Everything the plays produced, to be merged into the run."""

    patterns: list[Pattern]
    events: dict[str, pl.DataFrame]
    #: supplier index -> a new ``onboarded_at``, for shells opened to order.
    fresh_suppliers: dict[int, np.datetime64] = field(default_factory=dict)
    #: supplier indices whose ordinary activity is removed, so that a
    #: single-purpose entity is a thing the data can contain.
    stripped_suppliers: set[int] = field(default_factory=set)


class SupplyChainContext(InjectionContext):
    """The pack's own drawing on top of the shared pattern bookkeeping.

    Recruitment is uniform over suppliers of the tier a play needs. Weighting
    it towards the busiest suppliers was tried in the fraud pack and measured
    to make patterns *easier* to find, because hubs are outliers already;
    ``size_scale`` is the lever that keeps a ring from standing out by degree.
    """

    def __init__(
        self,
        rng: np.random.Generator,
        pop: Population,
        contracts: Contracts,
        config: SupplyChainConfig,
        networked: np.ndarray | None = None,
        neighbours: dict[int, list[int]] | None = None,
    ):
        super().__init__(rng, config.profile, CATALOG, pop.period_start, pop.period_days)
        self.pop = pop
        self.contracts = contracts
        self.config = config
        self.profile: HardnessProfile = config.profile
        self.rows: dict[str, list[tuple]] = {ORDERS: [], SHIPS: [], INVOICES: [], INTERCOMPANY: []}
        self.fresh_suppliers: dict[int, np.datetime64] = {}
        self.by_tier = {
            tier: np.flatnonzero(pop.supplier_tier == tier) for tier in (1, 2, 3)
        }
        # Who can plausibly be used for what. A supplier with no contract
        # cannot invoice a plant without the invoice itself being the
        # anomaly, and a supplier with no group around it cannot move stock
        # in a circle. Recruiting from the right pool is what makes a play
        # hide in ordinary traffic instead of appearing out of nothing.
        self.contracted = np.unique(contracts.supplier) if contracts.size else np.empty(0, dtype=np.int64)
        self.plant_of: dict[int, int] = {}
        self.contract_of: dict[int, int] = {}
        for index, (supplier, plant) in enumerate(
            zip(contracts.supplier.tolist(), contracts.plant.tolist(), strict=False)
        ):
            self.plant_of.setdefault(int(supplier), int(plant))
            self.contract_of.setdefault(int(supplier), index)
        self.networked = (
            networked if networked is not None and len(networked) else self.contracted
        )
        #: Who already trades with whom, so a circle can be drawn through
        #: existing relationships rather than invented ones.
        self.neighbours: dict[int, list[int]] = neighbours or {}
        self.ids = count(0)

    # ---------------------------------------------------------------- picks

    def pick(self, k: int, pool: np.ndarray | None = None) -> np.ndarray:
        """``k`` distinct suppliers, reusing ones already in a pattern with
        probability ``overlap`` and avoiding them otherwise."""
        pool = np.arange(self.pop.n_suppliers) if pool is None else pool
        if len(pool) == 0:
            return np.empty(0, dtype=np.int64)
        chosen: dict[int, None] = {}
        attempts = 0
        while len(chosen) < k and attempts < k * 40:
            attempts += 1
            if self.allow_overlap and self.used and self.rng.random() < self.profile.overlap:
                candidate = int(self.rng.choice(list(self.used)))
            else:
                candidate = int(pool[self.rng.integers(len(pool))])
                if not self.allow_overlap and candidate in self.used:
                    continue
            chosen[candidate] = None
        members = np.fromiter(chosen, dtype=np.int64, count=len(chosen))
        self.claim(members)
        return members

    def tier(self, level: int, k: int, within: np.ndarray | None = None) -> np.ndarray:
        pool = self.by_tier.get(level, np.empty(0, dtype=np.int64))
        if within is not None and len(within):
            narrowed = np.intersect1d(pool, within)
            pool = narrowed if len(narrowed) else pool
        return self.pick(k, pool if len(pool) else None)

    def trading(self, k: int) -> np.ndarray:
        """``k`` suppliers that already sell to a plant, so that the events a
        play adds land on top of a history rather than instead of one."""
        return self.pick(k, self.contracted if len(self.contracted) else None)

    def in_group(self, k: int) -> np.ndarray:
        """``k`` suppliers that already move stock between each other."""
        return self.pick(k, self.networked if len(self.networked) else None)

    def ring_size(self, base: int, spread: int = 0) -> int:
        """How many hops a circle has.

        The size dial runs backwards for cycles, and the fraud pack found
        the same thing: a three-hop ring is what a three-hop query finds, so
        hiding a circle means making it longer, not shorter. ``size_scale``
        shrinks a fan-in and stretches a cycle, and both are the same
        instruction: look less like the thing a rule was written for.
        """
        drawn = base + (int(self.rng.integers(0, spread + 1)) if spread else 0)
        return max(3, round(drawn / max(self.profile.size_scale, 0.1)))

    def ring(self, k: int) -> np.ndarray:
        """``k`` suppliers forming a circle along relationships they already
        have, where the network allows it.

        A circle drawn through strangers gives every member a trading
        partner it did not have, and partner count is the one feature
        camouflage cannot touch. Walking the subcontracting graph instead
        means the ring adds no degree to anybody and has to be found as a
        cycle, which is the detection problem worth posing.
        """
        if not self.neighbours:
            return self.in_group(k)
        starts = list(self.neighbours)
        start = int(starts[self.rng.integers(len(starts))])
        walk = [start]
        attempts = 0
        while len(walk) < k and attempts < k * 20:
            attempts += 1
            options = [n for n in self.neighbours.get(walk[-1], []) if n not in walk]
            # Prefer suppliers not already in a ring. A supplier in two rings
            # has twice the trading partners of its neighbours, and partner
            # count is the one feature no amount of blending hides.
            unused = [n for n in options if n not in self.used]
            if unused and (not self.allow_overlap or self.rng.random() >= self.profile.overlap):
                options = unused
            if not options:
                break
            candidate = int(options[self.rng.integers(len(options))])
            # A walk on a graph visits a supplier in proportion to how many
            # partners it has, so a ring built by walking is a ring of hubs,
            # and a hub has more of everything: more movements, more
            # partners, more volume. Measured, ring members had twice the
            # subcontracting degree of everyone else, which made the ring
            # findable by the hubs it was built from rather than by being a
            # ring. The Metropolis-Hastings correction (accept a busier
            # neighbour only sometimes) flattens that back to uniform.
            here = len(self.neighbours.get(walk[-1], ()))
            there = len(self.neighbours.get(candidate, ())) or 1
            if there > here and self.rng.random() > here / there:
                continue
            walk.append(candidate)
        if len(walk) < 3:
            return self.in_group(k)
        members = np.array(walk, dtype=np.int64)
        self.claim(members)
        return members

    def a_plant(self, supplier: int | None = None) -> int:
        """A plant to act against: the one the supplier actually sells to,
        where there is one, because an order to a plant with no contract is
        an anomaly all by itself."""
        if supplier is not None and supplier in self.plant_of:
            return self.plant_of[supplier]
        return int(self.rng.integers(0, max(1, self.pop.n_plants)))

    def a_product(self, supplier: int | None = None) -> int:
        """The product a play is about.

        The supplier's own, where it has a contract: a play whose invoices
        are for a part the supplier has never sold is visible by the part
        rather than by the scheme.
        """
        contract = self.contract_of.get(supplier) if supplier is not None else None
        if contract is not None and self.contracts.size:
            return int(self.contracts.product[contract])
        return int(self.rng.integers(0, max(1, self.pop.n_products)))

    def contract(self, supplier: int) -> int | None:
        return self.contract_of.get(supplier)

    def supplier_behind(self, supplier: int) -> int | None:
        """Somebody this supplier already subcontracts to, if anyone.

        Used by the substitution play so that the part comes up through an
        existing relationship rather than appearing between two companies
        that have never traded.
        """
        options = [n for n in self.neighbours.get(int(supplier), []) if n != supplier]
        if not options:
            return None
        preferred = [n for n in options if self.pop.supplier_tier[n] >= 2] or options
        chosen = int(preferred[self.rng.integers(len(preferred))])
        self.claim([chosen])
        return chosen

    def size(self, base: int, spread: int = 0) -> int:
        drawn = base + (int(self.rng.integers(0, spread + 1)) if spread else 0)
        return self.scaled(drawn)

    # --------------------------------------------------------------- timing

    def window(self, play: str) -> tuple[np.datetime64, int]:
        """Start and width in seconds of a pattern's activity window."""
        days = self.catalog.span_days(play) * self.profile.timing_spread_days
        days = min(days, max(1.0, self.pop.period_days - 1))
        span = max(3_600, int(days * 86_400))
        latest = self.pop.period_days * 86_400 - span
        offset = int(self.rng.integers(0, max(1, latest)))
        return self.period_start + np.timedelta64(offset, "s"), span

    def stamps(self, start: np.datetime64, span: int, n: int, ordered: bool = True) -> np.ndarray:
        if n <= 0:
            return np.empty(0, dtype="datetime64[s]")
        offsets = self.rng.integers(0, max(1, span), size=n)
        if ordered:
            offsets = np.sort(offsets)
        return start + offsets.astype("timedelta64[s]")

    # -------------------------------------------------------------- amounts

    def quantity(self, contract: int | None = None) -> int:
        """An order quantity, from the same model the process uses.

        Patterns used to draw quantities from a hand-picked range, which is a
        different distribution from the ordinary traffic even where the
        ranges overlap, and a different distribution is a signal. Drawing
        from the process's own model means a pattern's quantities say
        nothing, which is the point.
        """
        if contract is not None and self.contracts.size:
            base = float(self.contracts.quantity[contract % self.contracts.size])
            return int(max(1, round(base * self.rng.lognormal(0.0, 0.35))))
        return int(max(1, round(self.rng.lognormal(5.0, 0.9))))

    def legit_amount(self, product: int, quantity: int) -> float:
        """What this quantity of this product would ordinarily cost."""
        unit = float(self.pop.product_cost[product]) if self.pop.n_products else 100.0
        return float(quantity * unit * self.rng.lognormal(0.0, 0.25))

    def amount(self, product: int, quantity: int) -> float:
        """What a pattern's invoice says.

        The same distribution as an ordinary invoice, with one difference:
        an invented one is more likely to be a round figure, because somebody
        typed it. ``amount_blend`` takes that away, and at ``high`` the
        number is drawn exactly as the legitimate process draws it, so the
        amount carries nothing and only the missing goods behind it do.

        Roundness is deliberately *not* unique to fraud: about a fifth of
        ordinary orders are negotiated round figures, which is what makes
        this a weak signal rather than a label.
        """
        amount = self.legit_amount(product, quantity)
        round_rate = ROUND_PRICING_RATE + (1.0 - ROUND_PRICING_RATE) * (1.0 - self.profile.amount_blend)
        if self.rng.random() < round_rate:
            return float(max(ROUND_PRICING_STEP, round(amount / ROUND_PRICING_STEP) * ROUND_PRICING_STEP))
        return amount

    def discount(self) -> float:
        """How far below its normal unit price a lane runs.

        Used by the substitution play and by the rework credit that imitates
        it, from the same distribution, because a decoy drawn from a
        different range is separable by the range rather than by anything
        anyone would notice in the data.
        """
        if self.rng.random() < self.profile.amount_blend:
            return float(self.rng.uniform(0.85, 0.97))
        return float(self.rng.uniform(0.45, 0.72))

    def under_threshold(self) -> float:
        """An order sized to stay below the approval limit."""
        ceiling = self.config.approval_threshold * self.profile.threshold_margin
        return float(ceiling * self.rng.uniform(0.86, 0.999))

    # ----------------------------------------------------------------- rows

    def emit(
        self,
        channel: str,
        source: str,
        target: str,
        amount: float,
        quantity: int,
        stamp: np.datetime64,
        pattern: Pattern,
        reference: str = "",
        scheduled: bool = False,
    ) -> str:
        event_id = f"evt_p{next(self.ids)}"
        # A play places its events from its own window, and a lead time
        # counted backwards can fall outside the period. Clamp rather than
        # drop: the event happened, it is the date that is out of range.
        stamp = min(max(stamp, self.period_start), self.period_end)
        self.rows[channel].append(
            (source, target, event_id, stamp, round(float(amount), 2), int(quantity), reference, scheduled, pattern.pattern_id)
        )
        pattern.touch(stamp)
        return event_id

    def supplier_id(self, index: int) -> str:
        return self.pop.ids["Supplier"][int(index)]

    def plant_id(self, index: int) -> str:
        return self.pop.ids["Plant"][int(index)]

    def product_id(self, index: int) -> str:
        return self.pop.ids["Product"][int(index)] if self.pop.n_products else ""

    def frames(self) -> dict[str, pl.DataFrame]:
        out: dict[str, pl.DataFrame] = {}
        for channel, rows in self.rows.items():
            if not rows:
                continue
            source, target, ids, stamps, amount, quantity, reference, scheduled, pattern_id = zip(*rows, strict=False)
            out[channel] = pl.DataFrame(
                {
                    EVENT_COLUMNS[0]: list(source),
                    EVENT_COLUMNS[1]: list(target),
                    "event_id": list(ids),
                    "timestamp": np.array(stamps).astype("datetime64[us]"),
                    "amount": list(amount),
                    "quantity": list(quantity),
                    "reference": list(reference),
                    "scheduled": list(scheduled),
                    "pattern_id": list(pattern_id),
                }
            )
        return out


# --------------------------------------------------------------- the plays


def phantom_supplier(ctx: SupplyChainContext, pattern: Pattern) -> None:
    """Invoices with nothing behind them.

    A supplier is onboarded, raises invoices against a plant, gets paid, and
    never ships anything. The tells are the missing shipments, the fresh
    onboarding date and the round numbers; hardness removes the last two and
    leaves the first, which needs a join to see.
    """
    shell = ctx.trading(1)
    if not len(shell):
        return
    shell_index = int(shell[0])
    plant = ctx.a_plant(shell_index)
    product = ctx.a_product(shell_index)
    contract = ctx.contract(shell_index)
    pattern.roles[shell_index] = "shell"
    pattern.extra["plant"] = plant

    start, span = ctx.window(pattern.name)
    n_invoices = ctx.size(6, 5)
    stamps = ctx.stamps(start, span, n_invoices)
    # The supplier appears days before it starts invoicing, unless
    # camouflage says it has been on the books all along.
    if ctx.rng.random() >= ctx.profile.activity_camouflage:
        ctx.fresh_suppliers[shell_index] = start - np.timedelta64(int(ctx.rng.integers(3, 30)) * 86_400, "s")

    for stamp in stamps:
        quantity = ctx.quantity(contract)
        amount = ctx.amount(product, quantity)
        # A purchase order is raised to make the invoice look ordinary; at
        # low hardness the invoice arrives on its own, which is louder.
        reference = ""
        if ctx.rng.random() < ctx.profile.activity_camouflage:
            reference = ctx.emit(
                ORDERS, ctx.plant_id(plant), ctx.supplier_id(shell_index), amount, quantity,
                stamp - np.timedelta64(int(ctx.rng.integers(2, 20)) * 86_400, "s"), pattern,
                ctx.product_id(product),
            )
        ctx.emit(
            INVOICES, ctx.supplier_id(shell_index), ctx.plant_id(plant), amount, quantity, stamp,
            pattern, reference,
        )


def invoice_kiting(ctx: SupplyChainContext, pattern: Pattern) -> None:
    """Value going round a circle of related suppliers.

    Each hop is an ordinary inter-company movement. The circle is the thing,
    and it closes, which a cycle query can find if it looks far enough: the
    rings here are four to seven hops long for the same reason the fraud
    pack's are, because three-hop queries are what people write.
    """
    # Longer at higher hardness: see ``ring_size``.
    members = ctx.ring(ctx.ring_size(4, 2))
    if len(members) < 3:
        return
    for position, member in enumerate(members):
        pattern.roles[int(member)] = "hop" if position else "originator"

    start, span = ctx.window(pattern.name)
    rounds = max(1, ctx.size(3, 2) // 2)
    product = ctx.a_product()
    for _ in range(rounds):
        quantity = ctx.quantity()
        amount = ctx.amount(product, quantity)
        stamps = ctx.stamps(start, span, len(members))
        for position, stamp in enumerate(stamps):
            source = int(members[position])
            target = int(members[(position + 1) % len(members)])
            # Each hop keeps a sliver, which is what the circle is for.
            hop_amount = amount * float(ctx.rng.uniform(0.94, 1.0))
            ctx.emit(
                INTERCOMPANY, ctx.supplier_id(source), ctx.supplier_id(target), hop_amount, quantity,
                stamp, pattern,
            )


def split_orders(ctx: SupplyChainContext, pattern: Pattern) -> None:
    """Purchases broken up to stay under the approval limit.

    One supplier, one plant, several orders in a short window, each just
    below the signature threshold. The shipments and invoices follow
    normally, because the point of the scheme is that everything downstream
    looks right.
    """
    supplier = ctx.trading(1)
    if not len(supplier):
        return
    index = int(supplier[0])
    plant = ctx.a_plant(index)
    product = ctx.a_product(index)
    pattern.roles[index] = "supplier"
    pattern.extra["plant"] = plant

    start, span = ctx.window(pattern.name)
    n_orders = max(3, ctx.size(6, 4))
    stamps = ctx.stamps(start, span, n_orders)
    for stamp in stamps:
        amount = ctx.under_threshold()
        unit = float(ctx.pop.product_cost[product]) if ctx.pop.n_products else 100.0
        quantity = int(max(1, round(amount / max(unit, 1.0))))
        order = ctx.emit(
            ORDERS, ctx.plant_id(plant), ctx.supplier_id(index), amount, quantity, stamp, pattern,
            ctx.product_id(product),
        )
        ship_stamp = stamp + np.timedelta64(int(ctx.rng.integers(2, 20)) * 86_400, "s")
        ship = ctx.emit(
            SHIPS, ctx.supplier_id(index), ctx.plant_id(plant), amount, quantity, ship_stamp, pattern, order
        )
        ctx.emit(
            INVOICES, ctx.supplier_id(index), ctx.plant_id(plant), amount, quantity,
            ship_stamp + np.timedelta64(int(ctx.rng.integers(1, 6)) * 86_400, "s"), pattern, ship,
        )


def counterfeit_injection(ctx: SupplyChainContext, pattern: Pattern) -> None:
    """A substituted part coming up through the tiers.

    A tier-3 supplier sells a cheaper part to its tier-1 customer, which
    ships it on to the plant as the real thing. Nobody sees the substitution;
    what the data shows is a lane priced below what the part costs, for a
    while, and then back to normal.
    """
    front = ctx.tier(1, 1, ctx.contracted)
    if not len(front):
        return
    front_index = int(front[0])
    # The part comes up through somebody the conduit already buys from,
    # where it can: a substitution inside an existing relationship adds no
    # new edge to the graph, so it cannot be found by counting partners.
    deep = ctx.supplier_behind(front_index)
    if deep is None:
        picked = ctx.tier(3, 1, ctx.networked)
        if not len(picked):
            return
        deep = int(picked[0])
    deep_index = int(deep)
    plant = ctx.a_plant(front_index)
    product = ctx.a_product(front_index)
    contract = ctx.contract(front_index)
    pattern.roles[deep_index] = "substituter"
    pattern.roles[front_index] = "conduit"
    pattern.extra["plant"] = plant

    start, span = ctx.window(pattern.name)
    n_shipments = max(3, ctx.size(8, 6))
    stamps = ctx.stamps(start, span, n_shipments)
    unit = float(ctx.pop.product_cost[product]) if ctx.pop.n_products else 100.0
    for stamp in stamps:
        quantity = ctx.quantity(contract)
        amount = quantity * unit * ctx.discount()
        ctx.emit(
            INTERCOMPANY, ctx.supplier_id(deep_index), ctx.supplier_id(front_index), amount * 0.8,
            quantity, stamp, pattern,
        )
        ctx.emit(
            SHIPS, ctx.supplier_id(front_index), ctx.plant_id(plant), amount, quantity,
            stamp + np.timedelta64(int(ctx.rng.integers(1, 10)) * 86_400, "s"), pattern,
            ctx.product_id(product),
        )


# ------------------------------------------------------------- the twins


def disruption_cascade(ctx: SupplyChainContext, pattern: Pattern) -> None:
    """A port closes, or a region has a bad month.

    Orders go out and invoices come in for goods already in transit, and the
    shipments do not arrive in the window. The evidence is the same as a
    phantom supplier's and the cause is weather, so this is labelled
    innocent and counted as a false positive when a detector flags it.
    """
    region = int(ctx.rng.integers(0, max(1, len(set(ctx.pop.supplier_region.tolist())))))
    in_region = np.intersect1d(np.flatnonzero(ctx.pop.supplier_region == region), ctx.contracted)
    members = ctx.pick(max(2, ctx.size(5, 3)), in_region if len(in_region) else ctx.contracted)
    if not len(members):
        return
    plant = ctx.a_plant(int(members[0]))
    product = ctx.a_product(int(members[0]))
    pattern.extra["region"] = region
    for member in members:
        pattern.roles[int(member)] = "stranded"

    start, span = ctx.window(pattern.name)
    for member in members:
        for stamp in ctx.stamps(start, span, max(1, ctx.size(3, 2))):
            quantity = ctx.quantity()
            amount = ctx.amount(product, quantity)
            order = ctx.emit(
                ORDERS, ctx.plant_id(plant), ctx.supplier_id(int(member)), amount, quantity,
                stamp - np.timedelta64(int(ctx.rng.integers(5, 30)) * 86_400, "s"), pattern,
                ctx.product_id(product),
            )
            ctx.emit(
                INVOICES, ctx.supplier_id(int(member)), ctx.plant_id(plant), amount, quantity,
                stamp, pattern, order,
            )


def consignment_loop(ctx: SupplyChainContext, pattern: Pattern) -> None:
    """Stock moving round a group's own sites.

    The same circle as kiting, for the ordinary reason that inventory gets
    rebalanced between related companies. Slower, with quantities that match
    the money, and entirely legitimate.
    """
    members = ctx.ring(ctx.ring_size(4, 2))
    if len(members) < 3:
        return
    for member in members:
        pattern.roles[int(member)] = "site"
    start, span = ctx.window(pattern.name)
    product = ctx.a_product()
    for position in range(len(members)):
        source = int(members[position])
        target = int(members[(position + 1) % len(members)])
        for stamp in ctx.stamps(start, span, max(1, ctx.size(2, 2))):
            quantity = ctx.quantity()
            ctx.emit(
                INTERCOMPANY, ctx.supplier_id(source), ctx.supplier_id(target),
                ctx.amount(product, quantity), quantity, stamp, pattern,
            )


def blanket_calloffs(ctx: SupplyChainContext, pattern: Pattern) -> None:
    """Call-offs against a framework agreement.

    A plant has a standing contract and draws down against it in small
    amounts, every one of them under the approval threshold, because that is
    what the agreement is for. Structurally identical to split orders and
    completely routine, which is why a threshold rule needs this in the data
    before anyone trusts its precision.
    """
    supplier = ctx.trading(1)
    if not len(supplier):
        return
    index = int(supplier[0])
    plant = ctx.a_plant(index)
    product = ctx.a_product()
    pattern.roles[index] = "supplier"
    pattern.extra["plant"] = plant

    start, span = ctx.window(pattern.name)
    n_orders = max(3, ctx.size(7, 5))
    for stamp in ctx.stamps(start, span, n_orders):
        amount = (
            ctx.under_threshold()
            if ctx.rng.random() < 0.5
            else ctx.config.approval_threshold * float(ctx.rng.uniform(0.2, 0.95))
        )
        unit = float(ctx.pop.product_cost[product]) if ctx.pop.n_products else 100.0
        quantity = int(max(1, round(amount / max(unit, 1.0))))
        order = ctx.emit(
            ORDERS, ctx.plant_id(plant), ctx.supplier_id(index), amount, quantity, stamp, pattern,
            ctx.product_id(product), scheduled=True,
        )
        ship_stamp = stamp + np.timedelta64(int(ctx.rng.integers(2, 25)) * 86_400, "s")
        ship = ctx.emit(
            SHIPS, ctx.supplier_id(index), ctx.plant_id(plant), amount, quantity, ship_stamp, pattern, order
        )
        ctx.emit(
            INVOICES, ctx.supplier_id(index), ctx.plant_id(plant), amount, quantity,
            ship_stamp + np.timedelta64(int(ctx.rng.integers(1, 6)) * 86_400, "s"), pattern, ship,
        )


def quality_incident(ctx: SupplyChainContext, pattern: Pattern) -> None:
    """A lane priced below cost, for a reason.

    A process change goes wrong, the supplier credits the plant for the
    rework, and the lane runs under its normal unit price for a while. That
    is the counterfeit signature exactly, arrived at honestly.
    """
    front = ctx.tier(1, 1, ctx.contracted)
    if not len(front):
        return
    index = int(front[0])
    plant = ctx.a_plant(index)
    product = ctx.a_product(index)
    contract = ctx.contract(index)
    pattern.roles[index] = "supplier"
    pattern.extra["plant"] = plant

    start, span = ctx.window(pattern.name)
    unit = float(ctx.pop.product_cost[product]) if ctx.pop.n_products else 100.0
    for stamp in ctx.stamps(start, span, max(3, ctx.size(8, 6))):
        quantity = ctx.quantity(contract)
        ctx.emit(
            SHIPS, ctx.supplier_id(index), ctx.plant_id(plant), quantity * unit * ctx.discount(),
            quantity, stamp, pattern, ctx.product_id(product),
        )


PATTERN_FUNCTIONS = {
    "phantom_supplier": phantom_supplier,
    "invoice_kiting": invoice_kiting,
    "split_orders": split_orders,
    "counterfeit_injection": counterfeit_injection,
    "disruption_cascade": disruption_cascade,
    "consignment_loop": consignment_loop,
    "blanket_calloffs": blanket_calloffs,
    "quality_incident": quality_incident,
}


def inject(
    rng: np.random.Generator,
    pop: Population,
    contracts: Contracts,
    config: SupplyChainConfig,
    networked: np.ndarray | None = None,
    neighbours: dict[int, list[int]] | None = None,
) -> Injection:
    """Run every pattern the budget asks for, then the decoys."""
    ctx = SupplyChainContext(rng, pop, contracts, config, networked, neighbours)
    counter = count(0)

    def make(name: str, _index: int, labelled: bool) -> Pattern:
        # Numbered in injection order, and a decoy reports the play it
        # imitates: the truth says what the structure looks like, and
        # ``is_fraud`` says whether it is one.
        return Pattern(
            pattern_id=f"pat_{next(counter)}",
            name=CATALOG.twins.get(name, name),
            labelled=labelled,
        )

    patterns = run_catalog(
        ctx,
        config.pattern_counts,
        PATTERN_FUNCTIONS,
        make,
        round_robin_decoys(CATALOG, round(config.profile.decoy_ratio * config.num_patterns)),
        drop_empty=True,
    )
    stripped = stripped_members(rng, patterns, config.profile.activity_camouflage)
    return Injection(
        patterns=patterns,
        events=ctx.frames(),
        fresh_suppliers=ctx.fresh_suppliers,
        stripped_suppliers=stripped,
    )
