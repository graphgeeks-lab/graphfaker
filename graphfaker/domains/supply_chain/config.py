"""Options, derived sizes, hardness presets and the pattern catalogue for the
supply chain pack.

``scale`` follows the convention the other packs use: 1.0 is a large
manufacturer's network, and everything derives from it. The floors matter
more here than elsewhere, because a supply chain with one plant and two
warehouses is not a supply chain, and the shapes the pack injects need
somewhere to hide.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from graphfaker.schema.patterns import Camouflage, PatternCatalog, PatternSpec

Hardness = Literal["low", "medium", "high"]

#: Sizes at scale 1.0.
BASE_SUPPLIERS = 50_000
BASE_PLANTS = 500
BASE_WAREHOUSES = 2_000
BASE_CUSTOMERS = 500_000
BASE_PRODUCTS = 20_000
BASE_CARRIERS = 1_000
BASE_EVENTS = 50_000_000
BASE_PATTERNS = 1_000

#: Smallest network that still behaves like one: three tiers of suppliers,
#: more than one plant to compare, and enough warehouses for a lane to mean
#: something.
FLOORS = {
    "suppliers": 30,
    "plants": 3,
    "warehouses": 4,
    "customers": 50,
    "products": 20,
    "carriers": 3,
}

#: Share of suppliers in each tier. Tier 1 sells to the plants; tiers 2 and 3
#: sell to the tier above, which is where visibility runs out in practice and
#: where a counterfeit substitution is worth hiding.
TIER_MIX = (0.18, 0.32, 0.50)

#: What the pack injects. Four shapes a procurement team would investigate,
#: each paired with a legitimate structure that produces the same evidence.
#: The pairing is the point: every naive rule that catches the fraud also
#: catches its twin, and the twin is labelled innocent.
CATALOG = PatternCatalog(
    base=BASE_PATTERNS,
    floor=2,
    patterns=[
        # Invoices with no goods behind them, through a supplier that exists
        # only on paper.
        PatternSpec(name="phantom_supplier", share=0.28, span_days=45.0),
        # Goods circulating between related suppliers to inflate volume: the
        # supply chain's version of round-tripping.
        PatternSpec(name="invoice_kiting", share=0.22, span_days=30.0),
        # Orders broken up to stay under the approval threshold.
        PatternSpec(name="split_orders", share=0.30, span_days=10.0),
        # A tier-3 supplier substituting product on a lane, visible only as
        # quality falling downstream.
        PatternSpec(name="counterfeit_injection", share=0.20, span_days=60.0),
        # The legitimate twins. A port closure stops goods arriving while the
        # invoices for them already exist; a blanket agreement is call-offs
        # under a threshold by policy; a consignment loop moves stock between
        # sites of the same group; a process change dents quality on one lane.
        PatternSpec(name="disruption_cascade", imitates="phantom_supplier", span_days=45.0),
        PatternSpec(name="consignment_loop", imitates="invoice_kiting", span_days=30.0),
        PatternSpec(name="blanket_calloffs", imitates="split_orders", span_days=10.0),
        PatternSpec(name="quality_incident", imitates="counterfeit_injection", span_days=60.0),
    ],
)

#: The injectable shapes, in allocation order.
PLAYS = CATALOG.injected
#: The legitimate structures, labelled ``is_fraud=False``.
DECOY_PLAYS = CATALOG.decoys
#: Which shape each decoy imitates, for the hardness report.
DECOY_TWIN = CATALOG.twins


class HardnessProfile(Camouflage):
    """What a hardness level does to injected patterns.

    ``amount_blend`` (the shared signature blend) is the main dial, and in
    procurement the signature is the amount: round numbers, invoices for
    exactly the contracted quantity, orders sitting at 99% of the approval
    limit. At 1 the amounts come from the legitimate order distribution for
    that product and lane, so the number on the invoice says nothing.

    ``threshold_margin`` is the pack's own: how far under the approval
    threshold a split order sits. A tight margin is the giveaway a rule is
    written to catch; a wide one makes split orders look like ordinary
    small purchases, at the cost of needing more of them.
    """

    #: Fraction of the approval threshold a split order may reach.
    threshold_margin: float = Field(ge=0.0, le=1.0)

    @property
    def amount_blend(self) -> float:
        return self.signature_blend

    @property
    def timing_spread_days(self) -> float:
        return self.timing_spread

    @property
    def ring_overlap(self) -> float:
        return self.overlap


HARDNESS_PROFILES: dict[str, HardnessProfile] = {
    "low": HardnessProfile(
        signature_blend=0.0,
        timing_spread=0.2,
        overlap=0.0,
        decoy_ratio=0.0,
        activity_camouflage=0.0,
        size_scale=1.0,
        threshold_margin=0.99,
    ),
    "medium": HardnessProfile(
        signature_blend=0.5,
        timing_spread=3.0,
        overlap=0.1,
        decoy_ratio=0.5,
        activity_camouflage=0.6,
        size_scale=0.75,
        threshold_margin=0.92,
    ),
    "high": HardnessProfile(
        signature_blend=0.9,
        timing_spread=10.0,
        # Deliberately lower than ``medium``. Overlap makes attribution
        # harder and degree easier, and degree is the signal nothing else
        # can hide: measured, a supplier in two rings was more visible than
        # one in a single larger ring.
        overlap=0.12,
        decoy_ratio=1.0,
        activity_camouflage=1.0,
        size_scale=0.5,
        threshold_margin=0.70,
    ),
}


class SupplyChainConfig(BaseModel):
    """Everything a supply chain run needs, and nothing a detector may read."""

    model_config = ConfigDict(extra="forbid")

    scale: float = Field(default=0.001, gt=0.0, le=10.0)
    hardness: Hardness = "medium"
    period_start: dt.date = dt.date(2026, 1, 1)
    period_days: int = Field(default=180, ge=28, le=1095)
    locale: str = "en_US"
    currency: str = "USD"
    #: Orders above this need a signature, which is the thing split orders
    #: are splitting around. Procurement thresholds cluster at round numbers.
    approval_threshold: float = Field(default=25_000.0, gt=0.0)
    #: Latent regions: demand level, cost index and how exposed each one is
    #: to delay.
    regions: int = Field(default=6, ge=1, le=64)
    #: Product families: margin and base lead time.
    categories: int = Field(default=8, ge=1, le=64)
    #: Override the number of patterns per shape.
    patterns: dict[str, int] | None = None

    @model_validator(mode="after")
    def _known_patterns(self) -> SupplyChainConfig:
        if self.patterns:
            unknown = set(self.patterns) - set(CATALOG.names)
            if unknown:
                raise ValueError(f"unknown pattern(s): {', '.join(sorted(unknown))}")
        return self

    def _count(self, base: int, key: str) -> int:
        return max(FLOORS[key], int(base * self.scale))

    @property
    def num_suppliers(self) -> int:
        return self._count(BASE_SUPPLIERS, "suppliers")

    @property
    def num_plants(self) -> int:
        return self._count(BASE_PLANTS, "plants")

    @property
    def num_warehouses(self) -> int:
        return self._count(BASE_WAREHOUSES, "warehouses")

    @property
    def num_customers(self) -> int:
        return self._count(BASE_CUSTOMERS, "customers")

    @property
    def num_products(self) -> int:
        return self._count(BASE_PRODUCTS, "products")

    @property
    def num_carriers(self) -> int:
        return self._count(BASE_CARRIERS, "carriers")

    @property
    def event_budget(self) -> int:
        """Orders, shipments, invoices and deliveries together."""
        return max(2_000, int(BASE_EVENTS * self.scale))

    @property
    def num_patterns(self) -> int:
        if self.patterns is not None:
            return sum(self.patterns.values())
        return CATALOG.total(self.scale)

    @property
    def pattern_counts(self) -> dict[str, int]:
        if self.patterns is not None:
            return {name: self.patterns.get(name, 0) for name in PLAYS}
        return CATALOG.counts(self.num_patterns)

    @property
    def profile(self) -> HardnessProfile:
        return HARDNESS_PROFILES[self.hardness]

    @property
    def period_end(self) -> dt.date:
        return self.period_start + dt.timedelta(days=self.period_days)
