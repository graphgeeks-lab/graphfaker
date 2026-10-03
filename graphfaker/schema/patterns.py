"""Injected patterns, declared.

A domain pack that hides something in its data needs the same four things,
whether the thing is money laundering or a paid amplification ring: a
catalogue of shapes to inject, a budget saying how many of each, a set of
dials deciding how well they hide, and decoys that look like the real thing
and are not. This module is where a pack declares them.

What it does not hold is how a shape is drawn. ``fan_in`` and ``copypasta``
are code, because the thing that makes an injected pattern worth having is
exactly the part that does not generalise: which accounts, in what order,
with what amounts or text. The catalogue says what exists and how much of it;
:mod:`graphfaker.engine.injection` runs it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Camouflage(BaseModel):
    """How well injected patterns hide, as numbers rather than adjectives.

    Every dial trades detectability for something. ``signature_blend``
    replaces the giveaway a detector was written to catch (a round amount, an
    identical message) with a draw from the legitimate distribution, so the
    feature stops carrying signal. ``timing_spread`` stretches a pattern's
    steps from a burst to a background. ``overlap`` lets patterns share
    members, which is realistic and makes attribution harder.
    ``activity_camouflage`` keeps ordinary behaviour on the accounts a
    pattern recruits, so none of them is single-purpose. ``size_scale``
    shrinks patterns, because degree is the one signal blending cannot hide.
    ``decoy_ratio`` adds innocent structures of the same shape, which is what
    makes precision measurable at all.

    A pack subclasses this to add dials of its own and to give these ones the
    names its readers use.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    signature_blend: float = Field(ge=0.0, le=1.0)
    timing_spread: float = Field(gt=0.0)
    overlap: float = Field(ge=0.0, le=1.0)
    decoy_ratio: float = Field(ge=0.0)
    activity_camouflage: float = Field(ge=0.0, le=1.0)
    size_scale: float = Field(default=1.0, gt=0.0, le=1.0)


class PatternSpec(BaseModel):
    """One shape a domain can inject.

    ``span_days`` is the natural width of the pattern before
    :attr:`Camouflage.timing_spread` stretches it: a mule hand-off is hours, a
    bust-out is months. ``share`` is its slice of the pattern budget.
    A spec with ``imitates`` set is a decoy: the same shape as the spec it
    names, labelled innocent, and counted as a false positive when a detector
    flags it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    span_days: float = Field(default=1.0, gt=0.0)
    share: float = Field(default=0.0, ge=0.0)
    imitates: str | None = None

    @property
    def is_decoy(self) -> bool:
        return self.imitates is not None


class PatternCatalog(BaseModel):
    """Every shape a pack injects, and how the budget is split between them.

    ``base`` is the number of patterns at ``scale=1.0``; ``floor`` is the
    minimum per shape, so a small dataset still covers the catalogue instead
    of containing three copies of the most common thing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    patterns: list[PatternSpec] = Field(min_length=1)
    base: int = Field(default=1_000, gt=0)
    floor: int = Field(default=2, ge=0)

    def spec(self, name: str) -> PatternSpec:
        for spec in self.patterns:
            if spec.name == name:
                return spec
        raise KeyError(f"unknown pattern {name!r}; the catalogue has {', '.join(self.names)}")

    @property
    def names(self) -> tuple[str, ...]:
        """Every shape, in the order patterns are allocated and injected."""
        return tuple(spec.name for spec in self.patterns)

    @property
    def injected(self) -> tuple[str, ...]:
        """The shapes that are the thing to find."""
        return tuple(spec.name for spec in self.patterns if not spec.is_decoy)

    @property
    def decoys(self) -> tuple[str, ...]:
        """The shapes that imitate another and are labelled innocent."""
        return tuple(spec.name for spec in self.patterns if spec.is_decoy)

    @property
    def twins(self) -> dict[str, str]:
        """Decoy name to the shape it imitates."""
        return {spec.name: spec.imitates for spec in self.patterns if spec.imitates}

    def span_days(self, name: str) -> float:
        return self.spec(name).span_days

    def total(self, scale: float) -> int:
        """How many patterns a dataset of this scale gets.

        At least ``floor`` of every injected shape, so the catalogue is
        covered even at the sizes people try first.
        """
        return max(self.floor * len(self.injected), int(self.base * scale))

    def counts(self, total: int) -> dict[str, int]:
        """Allocate ``total`` patterns across the injected shapes.

        Proportional to ``share``, every shape at least ``floor``, the
        remainder going to the largest fractional parts, so the counts add up
        to ``total`` exactly rather than drifting with rounding.
        """
        mix = {spec.name: spec.share for spec in self.patterns if not spec.is_decoy}
        weight = sum(mix.values())
        if weight <= 0:  # no shares declared: split evenly
            mix = dict.fromkeys(mix, 1.0 / len(mix))
        elif abs(weight - 1.0) > 1e-9:
            mix = {name: share / weight for name, share in mix.items()}

        counts = dict.fromkeys(mix, self.floor)
        remaining = total - self.floor * len(mix)
        if remaining <= 0:
            return counts
        exact = {name: remaining * share for name, share in mix.items()}
        for name, value in exact.items():
            counts[name] += int(value)
        leftover = remaining - sum(int(v) for v in exact.values())
        for name in sorted(exact, key=lambda n: exact[n] - int(exact[n]), reverse=True)[:leftover]:
            counts[name] += 1
        return counts
