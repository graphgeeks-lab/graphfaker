"""Configuration for the fraud / AML domain pack.

The scale convention is gen-fraud-graph's so datasets are comparable:
``scale=1.0`` is ~10M accounts and ~90M transactions. Everything else is
derived from it.

``hardness`` controls how close injected typologies sit to legitimate
behaviour. Unlike a preset that merely asserts difficulty, the values here
are inputs to :func:`graphfaker.domains.fraud.hardness.hardness_report`,
which measures what a trivial detector can still see.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Hardness = Literal["low", "medium", "high"]

#: Base sizes at scale 1.0.
BASE_ACCOUNTS = 10_000_000
BASE_TRANSACTIONS = 90_000_000
BASE_PATTERNS = 1_000

#: Every typology the pack can inject, in the order patterns are allocated.
TYPOLOGIES = (
    "fan_in",
    "fan_out",
    "gather_scatter",
    "scatter_gather",
    "cycle",
    "stack",
    "bipartite",
    "structuring",
    "mule_network",
    "bust_out",
    "synthetic_identity",
)

#: Relative frequency of each typology when counts are derived from scale.
TYPOLOGY_MIX = {
    "fan_in": 0.14,
    "fan_out": 0.12,
    "gather_scatter": 0.10,
    "scatter_gather": 0.10,
    "cycle": 0.10,
    "stack": 0.08,
    "bipartite": 0.06,
    "structuring": 0.10,
    "mule_network": 0.08,
    "bust_out": 0.06,
    "synthetic_identity": 0.06,
}


class HardnessProfile(BaseModel):
    """What a hardness level does to injected patterns.

    ``amount_blend`` — 0 keeps a typology's signature amounts (round, near a
    threshold, sentinel-like); 1 draws them from the legitimate amount
    distribution of the same channel, so amount alone carries no signal.

    ``timing_spread_days`` — the window a pattern's steps are spread over. Tight
    windows (hours) are the classic tell; long windows bury the pattern under
    normal activity.

    ``ring_overlap`` — probability a new pattern reuses an account already in
    one, producing overlapping rings.

    ``decoy_ratio`` — legitimate structures that look like typologies
    (payroll fan-out, marketplace fan-in, supplier cycles), as a fraction of
    the fraud pattern count. Decoys are labelled in the truth as not fraud.

    ``activity_camouflage`` — fraction of pattern accounts that also carry
    normal transaction activity at the population rate, so degree and volume
    do not single them out; pattern members are also recruited among active
    accounts in proportion to this.

    ``size_scale`` — multiplier on pattern sizes (sources in a fan-in, hops in
    a chain). Degree is the one signal hardness cannot blend away: a collector
    with fifteen sources is an outlier in a population where a typical account
    has three partners a quarter. Smaller rings are how real launderers stay
    under the radar, and how ``high`` keeps degree from being a giveaway.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    amount_blend: float = Field(ge=0.0, le=1.0)
    timing_spread_days: float = Field(gt=0.0)
    ring_overlap: float = Field(ge=0.0, le=1.0)
    decoy_ratio: float = Field(ge=0.0)
    activity_camouflage: float = Field(ge=0.0, le=1.0)
    size_scale: float = Field(default=1.0, gt=0.0, le=1.0)


HARDNESS_PROFILES: dict[str, HardnessProfile] = {
    "low": HardnessProfile(
        amount_blend=0.0,
        timing_spread_days=0.1,
        ring_overlap=0.0,
        decoy_ratio=0.0,
        activity_camouflage=0.0,
        size_scale=1.0,
    ),
    "medium": HardnessProfile(
        amount_blend=0.5,
        timing_spread_days=3.0,
        ring_overlap=0.2,
        decoy_ratio=0.5,
        activity_camouflage=0.6,
        size_scale=0.75,
    ),
    "high": HardnessProfile(
        amount_blend=0.9,
        timing_spread_days=14.0,
        ring_overlap=0.4,
        decoy_ratio=1.0,
        activity_camouflage=1.0,
        size_scale=0.5,
    ),
}


def _largest_remainder(total: int, mix: dict[str, float], floor: int) -> dict[str, int]:
    """Allocate ``total`` across ``mix`` proportionally, every key at least
    ``floor``, remainders to the largest fractional parts."""
    counts = dict.fromkeys(mix, floor)
    remaining = total - floor * len(mix)
    if remaining <= 0:
        return counts
    exact = {name: remaining * share for name, share in mix.items()}
    for name, value in exact.items():
        counts[name] += int(value)
    leftover = remaining - sum(int(v) for v in exact.values())
    for name in sorted(exact, key=lambda n: exact[n] - int(exact[n]), reverse=True)[:leftover]:
        counts[name] += 1
    return counts


class FraudConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: ``1.0`` = ~10M accounts / ~90M transactions (gen-fraud-graph convention).
    scale: float = Field(default=0.001, gt=0.0)
    hardness: Hardness = "medium"
    #: Transaction period.
    period_start: dt.date = dt.date(2024, 1, 1)
    period_days: int = Field(default=90, ge=7)
    locale: str = "en_US"
    currency: str = "USD"
    #: Reporting threshold structuring stays under (USD CTR threshold).
    reporting_threshold: float = 10_000.0
    #: Override the number of patterns per typology. ``None`` derives them
    #: from scale with :data:`TYPOLOGY_MIX`.
    patterns: dict[str, int] | None = None
    #: Latent regions; attributes and partner choice are conditioned on them.
    regions: int = Field(default=8, ge=1)

    # ---------------------------------------------------------------- derived

    @property
    def num_accounts(self) -> int:
        return max(200, int(BASE_ACCOUNTS * self.scale))

    @property
    def num_customers(self) -> int:
        # ~1.4 accounts per customer.
        return max(150, int(self.num_accounts / 1.4))

    @property
    def num_merchants(self) -> int:
        # Roughly one merchant per fifty accounts; a card network sees far
        # more merchants than a naive "a few big shops" model suggests, and
        # too few per region makes category mix a spurious regional signal.
        return max(200, int(self.num_accounts * 0.02))

    @property
    def num_devices(self) -> int:
        # Most customers have one device, some two, households share.
        return max(120, int(self.num_customers * 1.15))

    @property
    def num_counterparties(self) -> int:
        return max(20, int(self.num_accounts * 0.002))

    @property
    def num_transactions(self) -> int:
        return max(2_000, int(BASE_TRANSACTIONS * self.scale))

    @property
    def num_patterns(self) -> int:
        if self.patterns is not None:
            return sum(self.patterns.values())
        # At least two of every typology, so small datasets still cover the
        # catalog; gen-fraud-graph's floor of 10 would leave most at zero.
        return max(2 * len(TYPOLOGIES), int(BASE_PATTERNS * self.scale))

    @property
    def pattern_counts(self) -> dict[str, int]:
        if self.patterns is not None:
            return {name: self.patterns.get(name, 0) for name in TYPOLOGIES}
        return _largest_remainder(self.num_patterns, TYPOLOGY_MIX, floor=2)

    @property
    def profile(self) -> HardnessProfile:
        return HARDNESS_PROFILES[self.hardness]

    @property
    def period_end(self) -> dt.date:
        return self.period_start + dt.timedelta(days=self.period_days)

    @model_validator(mode="after")
    def _known_typologies(self) -> FraudConfig:
        if self.patterns:
            unknown = set(self.patterns) - set(TYPOLOGIES)
            if unknown:
                raise ValueError(f"unknown typologies: {sorted(unknown)}")
        return self
