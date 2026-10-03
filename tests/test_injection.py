"""The pattern catalogue and the injection driver, which both domain packs run on."""

from __future__ import annotations

import numpy as np
import pytest

from graphfaker.engine.injection import (
    InjectionContext,
    Pattern,
    grouped_decoys,
    round_robin_decoys,
    run_catalog,
    stripped_members,
)
from graphfaker.schema import Camouflage, PatternCatalog, PatternSpec

CATALOG = PatternCatalog(
    base=100,
    floor=2,
    patterns=[
        PatternSpec(name="ring", share=0.6, span_days=2.0),
        PatternSpec(name="burst", share=0.4, span_days=0.5),
        PatternSpec(name="choir", imitates="burst", span_days=0.5),
        PatternSpec(name="club", imitates="ring", span_days=2.0),
    ],
)

PROFILE = Camouflage(
    signature_blend=0.5, timing_spread=3.0, overlap=0.2, decoy_ratio=0.5,
    activity_camouflage=0.6, size_scale=0.5,
)


def _ctx(**over) -> InjectionContext:
    profile = PROFILE.model_copy(update=over)
    return InjectionContext(
        np.random.default_rng(0), profile, CATALOG, np.datetime64("2026-01-01", "s"), 90
    )


def test_catalog_separates_what_is_injected_from_what_imitates_it():
    assert CATALOG.injected == ("ring", "burst")
    assert CATALOG.decoys == ("choir", "club")
    assert CATALOG.twins == {"choir": "burst", "club": "ring"}
    assert CATALOG.span_days("ring") == 2.0
    assert not CATALOG.spec("ring").is_decoy and CATALOG.spec("club").is_decoy
    with pytest.raises(KeyError, match="unknown pattern"):
        CATALOG.spec("nope")


def test_counts_respect_the_floor_the_shares_and_the_total():
    # Small datasets get the floor of every shape, so the catalogue is covered.
    assert CATALOG.total(0.001) == 4 and CATALOG.counts(4) == {"ring": 2, "burst": 2}
    # Large ones follow the shares, and the parts add up to the whole.
    counts = CATALOG.counts(CATALOG.total(1.0))
    assert sum(counts.values()) == 100
    assert counts["ring"] > counts["burst"]
    # Decoys are not part of the budget: they are a ratio of what was injected.
    assert set(counts) == {"ring", "burst"}


def test_counts_normalise_shares_that_do_not_sum_to_one():
    catalog = PatternCatalog(
        patterns=[PatternSpec(name="a", share=3.0), PatternSpec(name="b", share=1.0)], floor=0
    )
    assert catalog.counts(100) == {"a": 75, "b": 25}


def test_decoy_orders_differ_and_both_spend_the_budget():
    # Round robin answers every naive rule once before answering any twice;
    # grouped keeps a shape together. The two orders give different datasets
    # from the same seed, which is why a pack picks one and keeps it.
    assert round_robin_decoys(CATALOG, 3) == ["choir", "club", "choir"]
    assert grouped_decoys(CATALOG, 4) == ["choir", "choir", "club", "club"]
    assert round_robin_decoys(CATALOG, 0) == [] and grouped_decoys(CATALOG, 0) == []


def test_run_catalog_order_ids_and_decoy_isolation():
    ctx = _ctx()
    seen: list[tuple[str, bool, bool]] = []

    def draw(context: InjectionContext, pattern: Pattern) -> None:
        seen.append((pattern.name, pattern.labelled, context.allow_overlap))
        pattern.roles[len(seen)] = "member"
        pattern.touch(np.datetime64("2026-02-01T00:00", "s"))

    patterns = run_catalog(
        ctx,
        {"ring": 2, "burst": 1},
        dict.fromkeys(("ring", "burst", "choir", "club"), draw),
        lambda name, index, labelled: Pattern(f"{name}_{index}", name, labelled),
        round_robin_decoys(CATALOG, 2),
    )

    assert [p.pattern_id for p in patterns] == ["ring_0", "ring_1", "burst_0", "choir_0", "club_0"]
    # Injected shapes first in catalogue order, decoys last and never overlapping.
    assert [overlap for _, _, overlap in seen] == [True, True, True, False, False]
    assert [p.labelled for p in patterns] == [True, True, True, False, False]
    assert all(p.events == 1 and p.start == p.end for p in patterns)


def test_run_catalog_can_drop_the_shapes_that_recruited_nobody():
    ctx = _ctx()
    functions = {"ring": lambda c, p: None, "burst": lambda c, p: p.roles.update({1: "a"})}
    kept = run_catalog(ctx, {"ring": 1, "burst": 1}, functions, _make, drop_empty=True)
    assert [p.name for p in kept] == ["burst"]
    both = run_catalog(ctx, {"ring": 1, "burst": 1}, functions, _make)
    assert [p.name for p in both] == ["ring", "burst"]


def _make(name: str, index: int, labelled: bool) -> Pattern:
    return Pattern(f"{name}_{index}", name, labelled)


def test_claim_and_size_apply_the_dials():
    ctx = _ctx()
    ctx.claim(np.array([3, 4]))
    ctx.claim([4, 5])
    assert ctx.used == {3, 4, 5}
    # size_scale 0.5 halves a pattern, and the floor protects the shape.
    assert ctx.scaled(10) == 5 and ctx.scaled(2) == 2 and ctx.scaled(10, floor=8) == 8
    assert _ctx(size_scale=1.0).scaled(10) == 10


def test_stripped_members_only_touches_labelled_patterns():
    real = Pattern("a_0", "ring", True, roles={1: "x", 2: "y"})
    decoy = Pattern("club_0", "club", False, roles={3: "x"})
    # Full camouflage keeps everyone's ordinary activity; none strips it all.
    assert stripped_members(np.random.default_rng(1), [real, decoy], 1.0) == set()
    assert stripped_members(np.random.default_rng(1), [real, decoy], 0.0) == {1, 2}


def test_the_period_is_the_window_patterns_are_placed_in():
    ctx = _ctx()
    assert ctx.period_end - ctx.period_start == np.timedelta64(90 * 86_400, "s")
    assert ctx.allow_overlap and ctx.used == set()
