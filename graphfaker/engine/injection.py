"""Running a pattern catalogue against a generated population.

The fraud pack hides laundering in a bank; the coordination pack hides
amplification in a social platform. Underneath they do the same six things:
recruit members without reusing them unless the dials say to, place the
pattern somewhere in the period, record every row it creates against its id,
keep the roles for the truth tables, strip ordinary activity from the
accounts that are meant to look bare, and then do all of it again for the
decoys with overlap turned off.

That is what lives here. What does not is the drawing itself: how many
sources a fan-in has, what a copypasta posts, what an amount looks like.
Those differ per domain and per shape, and the useful thing a shared layer
can do is leave them alone while making sure the bookkeeping around them is
identical, because the bookkeeping is what the truth tables and every metric
are computed from.

A pack subclasses :class:`InjectionContext`, keeps its own drawing methods on
the subclass, and calls :func:`run_catalog`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from graphfaker.schema.patterns import Camouflage, PatternCatalog


@dataclass
class Pattern:
    """One injected structure and everything the truth needs to describe it.

    ``name`` is the shape (``fan_in``, ``copypasta``); ``labelled`` is whether
    it is the thing being looked for, so a decoy is a pattern with
    ``labelled=False`` rather than a different kind of object. Packs subclass
    this to give the two fields the names their readers use.
    """

    pattern_id: str
    name: str
    labelled: bool
    roles: dict[int, str] = field(default_factory=dict)  # member index -> role
    start: np.datetime64 | None = None
    end: np.datetime64 | None = None
    events: int = 0
    #: Anything the shape needs to record that the truth reports, such as the
    #: topic a campaign pushes.
    extra: dict[str, Any] = field(default_factory=dict)

    def touch(self, stamp: np.datetime64) -> None:
        """Record one event at ``stamp``, widening the pattern's window."""
        self.events += 1
        if self.start is None or stamp < self.start:
            self.start = stamp
        if self.end is None or stamp > self.end:
            self.end = stamp


class InjectionContext:
    """State shared by every shape in one run: who is taken, which dials are
    set, and where the period starts and ends.

    Subclasses add the domain's own drawing. They should call :meth:`claim`
    when they recruit, so that overlap and decoy separation hold across
    shapes, and :meth:`Pattern.touch` for every row they emit, so that a
    pattern's window and event count are right whatever drew them.
    """

    def __init__(
        self,
        rng: np.random.Generator,
        profile: Camouflage,
        catalog: PatternCatalog,
        period_start: np.datetime64,
        period_days: int,
    ):
        self.rng = rng
        self.profile = profile
        self.catalog = catalog
        self.period_start = period_start
        self.period_days = period_days
        self.period_end = period_start + np.timedelta64(period_days * 86_400, "s")
        #: Members already in a pattern. Recruitment consults it so that
        #: patterns overlap only as often as the dial says.
        self.used: set[int] = set()
        #: While True, recruitment may reuse members. Decoys turn it off: an
        #: innocent structure sharing members with a real one would have an
        #: ambiguous label, and being unambiguous is the whole point of it.
        self.allow_overlap = True

    def claim(self, members: np.ndarray | list[int]) -> None:
        self.used.update(int(m) for m in members)

    def scaled(self, size: int, floor: int = 2) -> int:
        """A pattern size after the size dial, never below ``floor``.

        Size is the one property camouflage cannot blend away: a collector
        with fifteen senders is an outlier wherever an ordinary account has
        three partners a quarter, so hiding costs members.
        """
        return max(floor, round(size * self.profile.size_scale))


def run_catalog(
    ctx: InjectionContext,
    counts: dict[str, int],
    functions: dict[str, Callable[[Any, Any], None]],
    make: Callable[[str, int, bool], Any],
    decoys: Sequence[str] = (),
    drop_empty: bool = False,
) -> list[Any]:
    """Inject every pattern the budget asks for, then the decoys.

    ``counts`` is how many of each injected shape, ``functions`` draws one,
    and ``make(name, index, labelled)`` builds the pattern record, so a pack
    keeps its own numbering and its own subclass. ``decoys`` is the exact
    sequence of decoy shapes to inject, which :func:`round_robin_decoys` and
    :func:`grouped_decoys` build. ``drop_empty`` discards a pattern that
    recruited nobody, which happens on populations too small for a shape.

    Order is part of the contract, not an implementation detail: shapes in
    catalogue order, each shape's patterns in index order, decoys last and
    with overlap off. A dataset is reproducible only if this loop is, so a
    pack that changes the order changes its data.
    """
    patterns: list[Any] = []
    for name in ctx.catalog.injected:
        for index in range(counts.get(name, 0)):
            pattern = make(name, index, True)
            functions[name](ctx, pattern)
            if pattern.roles or not drop_empty:
                patterns.append(pattern)

    if len(decoys):
        # Decoys do not share members with anything: an innocent structure
        # that overlapped a real one would have an ambiguous label, and being
        # unambiguous is the only reason it is in the data.
        ctx.allow_overlap = False
        seen: dict[str, int] = {}
        for name in decoys:
            index = seen.get(name, 0)
            seen[name] = index + 1
            pattern = make(name, index, False)
            functions[name](ctx, pattern)
            if pattern.roles or not drop_empty:
                patterns.append(pattern)
    return patterns


def round_robin_decoys(catalog: PatternCatalog, total: int) -> list[str]:
    """``total`` decoys, cycling through the decoy shapes one at a time.

    Use this when the budget is a count rather than a count per shape: every
    shape gets its first decoy before any gets its second, so a small budget
    still answers each of the naive rules the decoys exist to challenge.
    """
    shapes = catalog.decoys
    if not shapes or total <= 0:
        return []
    return [shapes[i % len(shapes)] for i in range(total)]


def grouped_decoys(catalog: PatternCatalog, total: int) -> list[str]:
    """``total`` decoys split evenly, all of one shape before the next.

    The same budget as :func:`round_robin_decoys` spent in a different order,
    which matters because the order is part of what a seed reproduces.
    """
    shapes = catalog.decoys
    if not shapes or total <= 0:
        return []
    per_shape = max(1, total // len(shapes))
    return [name for name in shapes for _ in range(per_shape)]


def stripped_members(
    rng: np.random.Generator, patterns: list[Any], camouflage: float
) -> set[int]:
    """Members whose ordinary activity is removed.

    An account that only ever does the pattern is the loudest signal in the
    data, so ``activity_camouflage`` decides what share keep behaving
    normally. Only labelled patterns are considered: a decoy that lost its
    ordinary activity would stop being innocent-looking, which would defeat
    the purpose of having it.
    """
    members = {member for p in patterns if p.labelled for member in p.roles}
    return {member for member in members if rng.random() >= camouflage}
