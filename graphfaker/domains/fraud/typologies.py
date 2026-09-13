"""Injected, labelled patterns.

Each typology has a *signature*: the thing a rule-based detector was written
to catch — round amounts, near-identical splits, a burst inside an hour, a
fresh account that only ever forwards. Hardness decides how much of that
signature survives. ``amount_blend`` swaps signature amounts for draws from
the legitimate distribution of the same channel; ``timing_spread_days``
stretches a pattern's steps from hours to weeks; ``activity_camouflage``
keeps normal activity on pattern accounts; ``ring_overlap`` lets rings share
members; ``decoy_ratio`` adds legitimate structures with the same shape.

The catalog is the AMLworld set (fan-in, fan-out, gather-scatter,
scatter-gather, cycle, stack, bipartite) plus the behaviours a bank actually
files SARs on: structuring under the reporting threshold, mule networks with
pass-through and shared devices, bust-out on credit, and synthetic identities
sharing phone, address and device.

Every pattern records its accounts with roles and every transaction it
creates; the truth tables are built from those records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl

from graphfaker.domains.fraud.config import TYPOLOGIES, FraudConfig, HardnessProfile
from graphfaker.domains.fraud.process import (
    BUSINESS_HOURS,
    HOUR_PROFILE,
    PAYS,
    TRANSFERS,
    WIRE_AMOUNT,
    WIRES,
    MerchantIndex,
    Population,
    merchant_amounts,
    transfer_amounts,
)

#: Natural span, in days at ``timing_spread_days = 1``, of each typology.
NATURAL_SPAN = {
    "fan_in": 2.0,
    "fan_out": 1.0,
    "gather_scatter": 3.0,
    "scatter_gather": 3.0,
    "cycle": 2.0,
    "stack": 2.0,
    "bipartite": 3.0,
    "structuring": 10.0,
    "mule_network": 1.0,
    "bust_out": 60.0,
    "synthetic_identity": 20.0,
}
#: Shapes that have a legitimate twin.
DECOY_TYPOLOGIES = ("fan_in", "fan_out", "cycle")


@dataclass
class Pattern:
    pattern_id: str
    typology: str
    is_fraud: bool
    roles: dict[int, str] = field(default_factory=dict)  # account idx -> role
    start: np.datetime64 | None = None
    end: np.datetime64 | None = None
    n_transactions: int = 0


@dataclass
class Injection:
    """Everything the typologies produced, to be merged into the run."""

    patterns: list[Pattern]
    transactions: dict[str, pl.DataFrame]  # channel -> frame with pattern_id
    #: (customer idx, device idx) pairs to add as USES edges.
    shared_devices: list[tuple[int, int]]
    #: customer idx -> attribute overrides (synthetic identities).
    customer_overrides: dict[int, dict[str, Any]]
    #: account idx -> new opened_at (fresh mule accounts), datetime64[D].
    fresh_accounts: dict[int, np.datetime64]
    #: account idx whose legitimate activity is removed (no camouflage).
    stripped_accounts: set[int]


class TypologyContext:
    def __init__(
        self,
        rng: np.random.Generator,
        pop: Population,
        config: FraudConfig,
        merchants: MerchantIndex,
        n_customers: int,
        n_devices: int,
    ):
        self.rng = rng
        self.pop = pop
        self.config = config
        self.profile: HardnessProfile = config.profile
        self.merchants = merchants
        self.n_customers = n_customers
        self.n_devices = n_devices
        self.used: set[int] = set()
        #: While True, ``pick`` may reuse pattern accounts (ring overlap).
        #: Decoys turn it off: a legitimate payroll must not share members
        #: with a mule ring, or its label would be ambiguous.
        self.allow_overlap = True
        usable = np.isin(pop.account_type, ["checking", "business", "savings"]) & (pop.account_status != "closed")
        self.eligible = np.flatnonzero(usable)
        # Recruitment weights: under camouflage, pattern members are drawn
        # from accounts that are already active, so the extra transactions do
        # not make them outliers by count alone.
        activity = pop.account_weight + 1e-9
        self.recruit_weight = activity ** self.profile.activity_camouflage
        self.credit = np.flatnonzero((pop.account_type == "credit") & (pop.account_status == "active"))
        self.rows: dict[str, list[tuple]] = {PAYS: [], TRANSFERS: [], WIRES: []}
        self.shared_devices: list[tuple[int, int]] = []
        self.customer_overrides: dict[int, dict[str, Any]] = {}
        self.fresh_accounts: dict[int, np.datetime64] = {}
        self.period_end = pop.period_start + np.timedelta64(pop.period_days * 86_400, "s")

    # ---------------------------------------------------------------- picks

    def pick(self, k: int, pool: np.ndarray | None = None) -> np.ndarray:
        """``k`` distinct accounts. With ``ring_overlap`` probability a member is
        drawn from accounts already in a pattern; otherwise from fresh ones."""
        pool = self.eligible if pool is None else pool
        fresh = np.setdiff1d(pool, np.fromiter(self.used, dtype=np.int64, count=len(self.used)))
        used = np.fromiter(self.used, dtype=np.int64, count=len(self.used))
        chosen: list[int] = []
        while len(chosen) < k:
            take_used = self.allow_overlap and len(used) and self.rng.random() < self.profile.ring_overlap
            source = used if take_used else fresh
            if len(source) == 0:
                source = fresh if len(fresh) else pool
            weights = self.recruit_weight[source]
            candidate = int(self.rng.choice(source, p=weights / weights.sum()))
            if candidate not in chosen:
                chosen.append(candidate)
            if len(pool) <= k and len(chosen) == len(set(pool.tolist())):
                break  # tiny populations: accept what there is
        self.used.update(chosen)
        return np.array(chosen, dtype=np.int64)

    def size(self, lo: int, hi: int, floor: int = 3) -> int:
        """A pattern size in ``[lo, hi]`` scaled down by hardness, never
        below ``floor``."""
        scale = self.profile.size_scale
        lo, hi = max(floor, round(lo * scale)), max(floor + 1, round(hi * scale))
        return int(self.rng.integers(lo, hi + 1))

    def bystanders(self, k: int) -> np.ndarray:
        """Accounts that send to a pattern without being part of it (victims
        of a mule network, customers of a marketplace)."""
        return self.rng.choice(self.eligible, size=k, replace=False)

    # --------------------------------------------------------------- timing

    def span_seconds(self, typology: str) -> int:
        days = NATURAL_SPAN[typology] * self.profile.timing_spread_days
        days = min(days, self.pop.period_days - 1)
        return max(3_600, int(days * 86_400))

    def window(self, typology: str) -> tuple[np.datetime64, int]:
        span = self.span_seconds(typology)
        latest = self.pop.period_days * 86_400 - span
        offset = int(self.rng.integers(0, max(1, latest)))
        return self.pop.period_start + np.timedelta64(offset, "s"), span

    def steps(self, start: np.datetime64, span: int, n: int, ordered: bool = True, business: bool = False) -> np.ndarray:
        """``n`` timestamps inside ``[start, start + span)``. Ordered steps
        model a chain; unordered ones a batch."""
        if span <= 86_400 * 1.5:
            offsets = self.rng.integers(0, span, size=n)
        else:
            days = self.rng.integers(0, max(1, span // 86_400), size=n)
            hours = HOUR_PROFILE if not business else BUSINESS_HOURS + 0.02
            hour = self.rng.choice(24, size=n, p=hours / hours.sum())
            offsets = days * 86_400 + hour * 3_600 + self.rng.integers(0, 3_600, size=n)
        offsets = np.minimum(offsets, max(0, span - 1))
        if ordered:
            offsets = np.sort(offsets)
        return start + offsets.astype("timedelta64[s]")

    # -------------------------------------------------------------- amounts

    def legit_amount(self, channel: str, account_idx: int) -> float:
        base = transfer_amounts(self.rng, 1)[0] if channel == TRANSFERS else self.rng.lognormal(*WIRE_AMOUNT)
        return float(base * self.pop.income_factor(np.array([account_idx]))[0])

    def amount(self, signature: float, channel: str, account_idx: int) -> float:
        """The signature amount, or a legitimate-looking one with probability
        ``amount_blend``."""
        if self.rng.random() < self.profile.amount_blend:
            return self.legit_amount(channel, account_idx)
        return float(signature)

    def round_amount(self, lo: int, hi: int, step: int = 100) -> float:
        return float(self.rng.integers(lo // step, hi // step + 1) * step)

    # ----------------------------------------------------------------- rows

    def tx(self, channel: str, src: int, dst: Any, amount: float, ts: np.datetime64, pattern: Pattern, memo: str = "") -> None:
        source = self.pop.account_ids[src]
        if channel == PAYS:
            target = self.pop.merchant_ids[dst]
        elif channel == WIRES:
            target = self.pop.counterparty_ids[dst]
        else:
            target = self.pop.account_ids[dst]
        self.rows[channel].append((source, target, round(float(amount), 2), ts, memo, pattern.pattern_id))
        pattern.n_transactions += 1
        pattern.start = ts if pattern.start is None or ts < pattern.start else pattern.start
        pattern.end = ts if pattern.end is None or ts > pattern.end else pattern.end

    def frames(self) -> dict[str, pl.DataFrame]:
        out = {}
        for channel, rows in self.rows.items():
            if rows:
                src, dst, amt, ts, memo, pid = zip(*rows)
                out[channel] = pl.DataFrame(
                    {
                        "source": list(src),
                        "target": list(dst),
                        "amount": list(amt),
                        "timestamp": np.array(ts).astype("datetime64[us]"),
                        "memo": list(memo),
                        "recurring": [False] * len(rows),
                        "pattern_id": list(pid),
                    }
                )
        return out


# ------------------------------------------------------------- typologies


def fan_in(ctx: TypologyContext, pattern: Pattern) -> None:
    k = ctx.size(5, 15)
    members = ctx.pick(k + 1)
    collector, sources = int(members[0]), members[1:]
    pattern.roles[collector] = "collector"
    start, span = ctx.window("fan_in")
    for src, ts in zip(sources, ctx.steps(start, span, k, ordered=False)):
        pattern.roles[int(src)] = "source"
        ctx.tx(TRANSFERS, int(src), collector, ctx.amount(ctx.round_amount(1_000, 9_000), TRANSFERS, int(src)), ts, pattern)
    # Placement then exit: the collector often wires the pool out.
    if len(ctx.pop.counterparty_ids) and ctx.rng.random() < 0.6:
        total = sum(r[2] for r in ctx.rows[TRANSFERS][-k:])
        ts = pattern.end + np.timedelta64(int(ctx.rng.integers(3_600, span + 3_600)), "s")
        if ts < ctx.period_end:
            ctx.tx(WIRES, collector, int(ctx.rng.integers(0, len(ctx.pop.counterparty_ids))), total * 0.95, ts, pattern, "international transfer")


def fan_out(ctx: TypologyContext, pattern: Pattern) -> None:
    k = ctx.size(5, 15)
    members = ctx.pick(k + 1)
    distributor, receivers = int(members[0]), members[1:]
    pattern.roles[distributor] = "distributor"
    start, span = ctx.window("fan_out")
    total = float(ctx.rng.uniform(20_000, 80_000))
    for dst, ts in zip(receivers, ctx.steps(start, span, k, ordered=False)):
        pattern.roles[int(dst)] = "receiver"
        # Near-identical splits are the signature.
        signature = total / k * ctx.rng.normal(1.0, 0.01)
        ctx.tx(TRANSFERS, distributor, int(dst), ctx.amount(signature, TRANSFERS, distributor), ts, pattern)


def gather_scatter(ctx: TypologyContext, pattern: Pattern) -> None:
    k, m = ctx.size(4, 10), ctx.size(4, 10)
    members = ctx.pick(k + m + 1)
    hub, sources, receivers = int(members[0]), members[1 : k + 1], members[k + 1 :]
    pattern.roles[hub] = "hub"
    start, span = ctx.window("gather_scatter")
    half = span // 2
    gathered = 0.0
    for src, ts in zip(sources, ctx.steps(start, half, k, ordered=False)):
        pattern.roles[int(src)] = "source"
        amount = ctx.amount(ctx.round_amount(2_000, 9_000), TRANSFERS, int(src))
        gathered += amount
        ctx.tx(TRANSFERS, int(src), hub, amount, ts, pattern)
    out_total = gathered * float(ctx.rng.uniform(0.92, 0.98))
    for dst, ts in zip(receivers, ctx.steps(start + np.timedelta64(half, "s"), span - half, m, ordered=False)):
        pattern.roles[int(dst)] = "receiver"
        ctx.tx(TRANSFERS, hub, int(dst), ctx.amount(out_total / m * ctx.rng.normal(1.0, 0.02), TRANSFERS, hub), ts, pattern)


def scatter_gather(ctx: TypologyContext, pattern: Pattern) -> None:
    k = ctx.size(4, 10)
    members = ctx.pick(k + 2)
    source, collector, mids = int(members[0]), int(members[1]), members[2:]
    pattern.roles[source], pattern.roles[collector] = "source", "collector"
    start, span = ctx.window("scatter_gather")
    half = span // 2
    total = float(ctx.rng.uniform(20_000, 90_000))
    outs = ctx.steps(start, half, k, ordered=False)
    for mid, ts in zip(mids, outs):
        pattern.roles[int(mid)] = "intermediary"
        amount = ctx.amount(total / k * ctx.rng.normal(1.0, 0.02), TRANSFERS, source)
        ctx.tx(TRANSFERS, source, int(mid), amount, ts, pattern)
        delay = int(ctx.rng.integers(600, max(601, span - half)))
        ctx.tx(TRANSFERS, int(mid), collector, amount * float(ctx.rng.uniform(0.9, 0.98)), ts + np.timedelta64(delay, "s"), pattern)


def _chain(ctx: TypologyContext, pattern: Pattern, typology: str, close: bool) -> None:
    depth = ctx.size(4, 7, floor=3) if close else ctx.size(3, 6, floor=3)
    members = ctx.pick(depth)
    for i, acc in enumerate(members):
        pattern.roles[int(acc)] = "origin" if i == 0 else "hop"
    start, span = ctx.window(typology)
    hops = depth if close else depth - 1
    amount = float(ctx.rng.uniform(5_000, 15_000))
    for i, ts in enumerate(ctx.steps(start, span, hops, ordered=True)):
        src = int(members[i])
        dst = int(members[(i + 1) % depth])
        ctx.tx(TRANSFERS, src, dst, ctx.amount(amount, TRANSFERS, src), ts, pattern)
        amount *= float(ctx.rng.uniform(0.97, 0.995))  # fees skimmed each hop


def cycle(ctx: TypologyContext, pattern: Pattern) -> None:
    _chain(ctx, pattern, "cycle", close=True)


def stack(ctx: TypologyContext, pattern: Pattern) -> None:
    _chain(ctx, pattern, "stack", close=False)


def bipartite(ctx: TypologyContext, pattern: Pattern) -> None:
    k, m = ctx.size(3, 6, floor=2), ctx.size(3, 6, floor=2)
    members = ctx.pick(k + m)
    sources, targets = members[:k], members[k:]
    for s in sources:
        pattern.roles[int(s)] = "source"
    for t in targets:
        pattern.roles[int(t)] = "target"
    start, span = ctx.window("bipartite")
    pairs = [(int(s), int(t)) for s in sources for t in targets if ctx.rng.random() < 0.75]
    for (s, t), ts in zip(pairs, ctx.steps(start, span, len(pairs), ordered=False)):
        ctx.tx(TRANSFERS, s, t, ctx.amount(ctx.round_amount(500, 4_000, 50), TRANSFERS, s), ts, pattern)


def structuring(ctx: TypologyContext, pattern: Pattern) -> None:
    """Many deposits just under the reporting threshold. Blending widens the
    band below the threshold instead of abandoning it: structuring that is
    not under the threshold is not structuring."""
    n_receivers = int(ctx.rng.integers(1, 4))
    members = ctx.pick(n_receivers + 1)
    source, receivers = int(members[0]), members[1:]
    pattern.roles[source] = "structurer"
    for r in receivers:
        pattern.roles[int(r)] = "receiver"
    start, span = ctx.window("structuring")
    n = int(ctx.rng.integers(6, 21))
    threshold = ctx.config.reporting_threshold
    lo = 0.9 - 0.4 * ctx.profile.amount_blend
    for ts in ctx.steps(start, span, n, ordered=True, business=True):
        fraction = float(ctx.rng.uniform(lo, 0.995))
        amount = threshold * fraction
        if ctx.rng.random() < ctx.profile.amount_blend * 0.3:
            amount = threshold * float(ctx.rng.uniform(1.0, 1.4))  # occasional slip above
        ctx.tx(TRANSFERS, source, int(ctx.rng.choice(receivers)), amount, ts, pattern, "deposit")


def mule_network(ctx: TypologyContext, pattern: Pattern) -> None:
    """Mules receive from victims and forward almost everything to a hub
    within hours; mules share a device; at low hardness their accounts are
    freshly opened."""
    m = ctx.size(3, 10)
    members = ctx.pick(m + 1)
    hub, mules = int(members[0]), members[1:]
    pattern.roles[hub] = "hub"
    start, span = ctx.window("mule_network")
    device = int(ctx.rng.integers(0, ctx.n_devices))
    for mule in mules:
        mule = int(mule)
        pattern.roles[mule] = "mule"
        ctx.shared_devices.append((int(ctx.pop.account_customer[mule]), device))
        if ctx.rng.random() > ctx.profile.activity_camouflage:
            fresh_days = int(ctx.rng.integers(3, 45))
            ctx.fresh_accounts[mule] = (start - np.timedelta64(fresh_days * 86_400, "s")).astype("datetime64[D]")
        inbound = int(ctx.rng.integers(1, 4))
        victims = ctx.bystanders(inbound)
        received = 0.0
        last = start
        for victim, ts in zip(victims, ctx.steps(start, max(1, span // 2), inbound, ordered=True)):
            amount = ctx.amount(ctx.round_amount(1_000, 8_000), TRANSFERS, int(victim))
            received += amount
            last = max(last, ts)
            ctx.tx(TRANSFERS, int(victim), mule, amount, ts, pattern)
        delay = int(ctx.rng.integers(900, max(901, span // 2)))
        ctx.tx(TRANSFERS, mule, hub, received * float(ctx.rng.uniform(0.9, 0.97)), last + np.timedelta64(delay, "s"), pattern)


def bust_out(ctx: TypologyContext, pattern: Pattern) -> None:
    """Credit built up with rising, well-behaved spending, then maxed out in a
    burst of high-value purchases with no repayment."""
    if len(ctx.credit) == 0:
        return fan_out(ctx, pattern)
    acc = int(ctx.pick(1, pool=ctx.credit)[0])
    pattern.roles[acc] = "buster"
    start, span = ctx.window("bust_out")
    build = int(span * 0.85)
    n_build = int(ctx.rng.integers(4, 12))
    stamps = ctx.steps(start, build, n_build, ordered=True)
    for i, ts in enumerate(stamps):
        merchant = int(ctx.merchants.choose_for_accounts(ctx.rng, np.array([acc]), None)[0])
        base = float(merchant_amounts(ctx.rng, ctx.pop, np.array([merchant]), np.array([acc]))[0])
        ctx.tx(PAYS, acc, merchant, base * (1 + 2.0 * i / n_build), ts, pattern, "")
    burst_start = start + np.timedelta64(build, "s")
    n_burst = int(ctx.rng.integers(3, 9))
    for ts in ctx.steps(burst_start, span - build, n_burst, ordered=True):
        category = str(ctx.rng.choice(["electronics", "travel", "retail"]))
        merchant = int(ctx.merchants.choose(ctx.rng, 1, None, category)[0])
        # The signature is value escalation at high-ticket merchants, not
        # round numbers: card purchases are never round.
        typical = float(merchant_amounts(ctx.rng, ctx.pop, np.array([merchant]), np.array([acc]))[0])
        multiplier = float(ctx.rng.uniform(4.0, 8.0)) * (1 - ctx.profile.amount_blend) + float(ctx.rng.uniform(1.5, 3.0)) * ctx.profile.amount_blend
        ctx.tx(PAYS, acc, merchant, typical * multiplier, ts, pattern)


def synthetic_identity(ctx: TypologyContext, pattern: Pattern) -> None:
    """Several customers that are the same person: identical phone and
    address, one device, and accounts that funnel to a collector."""
    n = ctx.size(3, 7)
    members = ctx.pick(n + 1)
    collector, identities = int(members[0]), members[1:]
    pattern.roles[collector] = "collector"
    template = int(ctx.pop.account_customer[identities[0]])
    device = int(ctx.rng.integers(0, ctx.n_devices))
    start, span = ctx.window("synthetic_identity")
    for acc in identities:
        acc = int(acc)
        pattern.roles[acc] = "identity"
        cust = int(ctx.pop.account_customer[acc])
        ctx.customer_overrides[cust] = {"template": template}
        ctx.shared_devices.append((cust, device))
        inbound = int(ctx.rng.integers(1, 3))
        received = 0.0
        last = start
        for victim, ts in zip(ctx.bystanders(inbound), ctx.steps(start, max(1, int(span * 0.7)), inbound, ordered=True)):
            amount = ctx.amount(ctx.round_amount(500, 5_000), TRANSFERS, int(victim))
            received += amount
            last = max(last, ts)
            ctx.tx(TRANSFERS, int(victim), acc, amount, ts, pattern)
        delay = int(ctx.rng.integers(3_600, max(3_601, int(span * 0.3))))
        ctx.tx(TRANSFERS, acc, collector, received * float(ctx.rng.uniform(0.85, 0.98)), last + np.timedelta64(delay, "s"), pattern)


# ----------------------------------------------------------------- decoys


def decoy_fan_out(ctx: TypologyContext, pattern: Pattern) -> None:
    """Payroll: a business pays many people similar amounts on one day."""
    business = np.flatnonzero((ctx.pop.account_type == "business") & (ctx.pop.account_status == "active"))
    k = int(ctx.rng.integers(5, 16))
    payer = int(ctx.pick(1, pool=business if len(business) else None)[0])
    receivers = ctx.pick(k)
    pattern.roles[payer] = "employer"
    start, span = ctx.window("fan_out")
    for dst, ts in zip(receivers, ctx.steps(start, max(3_600, span // 4), k, ordered=False, business=True)):
        pattern.roles[int(dst)] = "employee"
        salary = float(np.exp(ctx.pop.account_log_income[int(dst)]) / 12 * ctx.rng.normal(1.0, 0.02))
        ctx.tx(TRANSFERS, payer, int(dst), salary, ts, pattern, "salary")


def decoy_fan_in(ctx: TypologyContext, pattern: Pattern) -> None:
    """A marketplace or club account collecting many small payments."""
    k = int(ctx.rng.integers(8, 25))
    collector = int(ctx.pick(1)[0])
    pattern.roles[collector] = "marketplace"
    start, span = ctx.window("fan_in")
    for src, ts in zip(ctx.bystanders(k), ctx.steps(start, span, k, ordered=False)):
        ctx.tx(TRANSFERS, int(src), collector, ctx.legit_amount(TRANSFERS, int(src)), ts, pattern, "order")


def decoy_cycle(ctx: TypologyContext, pattern: Pattern) -> None:
    """Suppliers invoicing each other in a loop over weeks."""
    depth = int(ctx.rng.integers(3, 6))
    business = np.flatnonzero((ctx.pop.account_type == "business") & (ctx.pop.account_status != "closed"))
    members = ctx.pick(depth, pool=business if len(business) >= depth else None)
    for acc in members:
        pattern.roles[int(acc)] = "supplier"
    start, span = ctx.window("cycle")
    remaining = int((ctx.period_end - start) / np.timedelta64(1, "s"))
    span = min(max(span, 14 * 86_400), remaining)
    for i, ts in enumerate(ctx.steps(start, span, len(members), ordered=True, business=True)):
        src, dst = int(members[i]), int(members[(i + 1) % len(members)])
        ctx.tx(TRANSFERS, src, dst, ctx.legit_amount(TRANSFERS, src) * 8, ts, pattern, "invoice")


TYPOLOGY_FUNCTIONS = {
    "fan_in": fan_in,
    "fan_out": fan_out,
    "gather_scatter": gather_scatter,
    "scatter_gather": scatter_gather,
    "cycle": cycle,
    "stack": stack,
    "bipartite": bipartite,
    "structuring": structuring,
    "mule_network": mule_network,
    "bust_out": bust_out,
    "synthetic_identity": synthetic_identity,
}
DECOY_FUNCTIONS = {"fan_in": decoy_fan_in, "fan_out": decoy_fan_out, "cycle": decoy_cycle}


def inject(
    rng: np.random.Generator,
    pop: Population,
    config: FraudConfig,
    merchants: MerchantIndex,
    n_customers: int,
    n_devices: int,
) -> Injection:
    ctx = TypologyContext(rng, pop, config, merchants, n_customers, n_devices)
    patterns: list[Pattern] = []
    counter = 0
    for typology in TYPOLOGIES:
        for _ in range(config.pattern_counts.get(typology, 0)):
            pattern = Pattern(pattern_id=f"pat_{counter}", typology=typology, is_fraud=True)
            TYPOLOGY_FUNCTIONS[typology](ctx, pattern)
            patterns.append(pattern)
            counter += 1

    n_decoys = round(config.profile.decoy_ratio * config.num_patterns)
    ctx.allow_overlap = False
    for i in range(n_decoys):
        typology = DECOY_TYPOLOGIES[i % len(DECOY_TYPOLOGIES)]
        pattern = Pattern(pattern_id=f"pat_{counter}", typology=typology, is_fraud=False)
        DECOY_FUNCTIONS[typology](ctx, pattern)
        patterns.append(pattern)
        counter += 1

    fraud_accounts = {acc for p in patterns if p.is_fraud for acc in p.roles}
    keep = config.profile.activity_camouflage
    stripped = {acc for acc in fraud_accounts if rng.random() >= keep}

    return Injection(
        patterns=patterns,
        transactions=ctx.frames(),
        shared_devices=ctx.shared_devices,
        customer_overrides=ctx.customer_overrides,
        fresh_accounts=ctx.fresh_accounts,
        stripped_accounts=stripped,
    )
