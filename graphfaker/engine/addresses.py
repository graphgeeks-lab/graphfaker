"""Addresses that organisations share, in the proportions registers do.

Giving every company its own address is the single easiest way to make a
corporate dataset useless for the thing people most want to do with one.
In a real register an address is not an identifier: most hold one company,
a few hold a dozen, and a handful of registered agents hold tens of
thousands. Measured on a register of 2,026,444 records
(``benchmarks/realism/corporate-lasvegas.json``): the median address holds
one organisation, the 90th percentile holds four, the 99th holds 31, the
busiest holds 99,522, and the top 1% of addresses carry 53.7% of all rows.

That shape is why address is not a blocking key and why matching on it
merges a city. A generator that misses it hands people a dataset where
deduplicating on address works perfectly, which is the opposite of the
lesson.

A second measurement agrees, which is why these numbers are treated as
properties of the population rather than of one file: the 1,033,773 companies
registered in Nevada, cut out of the national open data export of
2026-03-05 (``benchmarks/realism/corporate-nevada.json``), give a mean of
4.49, a median of 1, a 90th percentile of 4, a 99th of 27, a busiest address
of 99,794 and a top 1% share of 51.0%.

The model is one Zipf curve. Allocate rows across a pool of addresses with
weight proportional to ``rank ** -alpha``, deterministically rather than by
sampling, so the shape is the same at every scale and the randomness is in
which organisation lands where. With the defaults below and 200,000
organisations it reproduces the mean, the median and the 90th percentile
exactly, and overshoots the tail a little: p99 of 40 against 31, top 1%
share of 0.56 against 0.537.

Three honest limits. A small dataset cannot have the real head: 1% of 160
addresses is one address, so the "top 1% share" of a 500-company run is a
different statistic from the same number on a register. The pool is drawn
from Faker's street vocabulary, so the addresses are plausible rather than
real places. And one curve cannot have both ends: the exponent that sets the
mean also sets the tail, so bringing p99 down to the measured 27 to 31 takes
the mean to 3.7, and the mean is the number more things depend on. Fixing
that properly means two components, a heavy head of registered agents over a
nearly unique body, which is in the plan.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from graphfaker.engine.seeding import Streams


@dataclass(frozen=True)
class AddressProfile:
    """How concentrated address reuse is.

    ``mean_per_address`` and ``pool_factor`` together set how many distinct
    addresses exist; ``alpha`` sets how unequally they are used. Higher
    alpha means a heavier head: more companies at the busiest address and
    fewer addresses used once.
    """

    #: Organisations per address, averaged over the register.
    mean_per_address: float = 4.76
    #: Exponent of the rank curve, and the dial that actually decides how
    #: shared an address is. At 0 the pool is used evenly, so a pool at
    #: least as large as the row count gives every organisation its own
    #: address. At 1 the register's concentration comes back. Setting
    #: ``mean_per_address`` alone does not make addresses unique, which is
    #: worth saying because it is the mistake this comment exists to stop.
    alpha: float = 1.0
    #: Pool size relative to ``rows / mean_per_address``. Above one because
    #: the median address has to hold exactly one organisation, which needs
    #: more addresses than the mean alone implies.
    pool_factor: float = 1.9
    #: Where the numbers came from, carried so a dataset can say.
    source: str = "Las Vegas company register (2,026,444 records)"


#: The measured profile: a company register, with its registered agents.
REGISTER = AddressProfile()

#: A retail customer base. People mostly live at their own address, a few
#: share one, and nobody is registered through an agent. Not unique, because
#: households and shared offices exist, but nothing like a register.
RETAIL = AddressProfile(
    mean_per_address=1.08, alpha=0.18, pool_factor=1.05, source="assumed, not measured"
)

#: Every organisation at its own address. Rarely true of anything real, so a
#: domain using it should be saying something rather than defaulting into it.
UNIQUE = AddressProfile(
    mean_per_address=1.0, alpha=0.0, pool_factor=1.0, source="not a register"
)


def allocate(rows: int, profile: AddressProfile = REGISTER) -> np.ndarray:
    """How many organisations sit at each address, largest first.

    Deterministic: the same shape at every scale, with the sampling left to
    :func:`assign`. Returns one count per address that has at least one.
    """
    if rows <= 0:
        return np.empty(0, dtype=np.int64)
    pool = max(1, int(rows / profile.mean_per_address * profile.pool_factor))
    weights = np.power(np.arange(1, pool + 1, dtype=np.float64), -profile.alpha)
    weights /= weights.sum()
    exact = rows * weights
    counts = np.floor(exact).astype(np.int64)
    short = rows - int(counts.sum())
    if short > 0:
        # Largest remainder, so the counts add up to the row count exactly
        # instead of drifting with the rounding.
        counts[np.argsort(-(exact - counts))[:short]] += 1
    return counts[counts > 0]


def assign(rng: np.random.Generator, rows: int, profile: AddressProfile = REGISTER) -> np.ndarray:
    """An address index for each of ``rows`` organisations.

    Which organisation ends up at the shared address is random; how many
    organisations a shared address holds is not.
    """
    counts = allocate(rows, profile)
    if not len(counts):
        return np.empty(0, dtype=np.int64)
    indices = np.repeat(np.arange(len(counts), dtype=np.int64), counts)
    rng.shuffle(indices)
    return indices


def address_pool(streams: Streams, size: int) -> dict[str, list[str]]:
    """``size`` distinct addresses, drawn column at a time."""
    if size <= 0:
        return {"address_line1": [], "address_city": [], "address_postal_code": []}
    fast, rng = streams.fast, streams.rng
    return {
        "address_line1": fast.draw("street_address", {}, size, rng),
        "address_city": fast.draw("city", {}, size, rng),
        "address_postal_code": fast.draw("postcode", {}, size, rng),
    }


def shared_addresses(
    streams: Streams, rows: int, profile: AddressProfile = REGISTER
) -> dict[str, list[str]]:
    """Address columns for ``rows`` organisations, reused as a register does.

    The returned columns are ready to attach to a node table: rows at the
    same address carry identical strings, which is what makes an address a
    thing to be resolved rather than a key.
    """
    indices = assign(streams.rng, rows, profile)
    pool = address_pool(streams, int(indices.max()) + 1 if len(indices) else 0)
    return {
        column: [values[i] for i in indices.tolist()] for column, values in pool.items()
    }


def reuse_stats(indices: np.ndarray) -> dict[str, float]:
    """The numbers the reference file reports, for checking a run against it."""
    if not len(indices):
        return {}
    counts = np.bincount(indices)
    counts = np.sort(counts[counts > 0])[::-1]
    total, n = int(counts.sum()), len(counts)
    head = max(1, n // 100)
    return {
        "addresses": n,
        "mean": round(total / n, 3),
        "p50": int(counts[min(n - 1, n // 2)]),
        "p90": int(counts[min(n - 1, n // 10)]),
        "p99": int(counts[min(n - 1, n // 100)]),
        "max": int(counts[0]),
        "top1pct_share": round(float(counts[:head].sum() / total), 4),
    }
