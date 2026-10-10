"""Address reuse, against the register it was measured from.

The numbers here are not arbitrary: they come from
``benchmarks/realism/corporate-lasvegas.json``, 2,026,444 records of a real
company register. The test exists because giving every organisation its own
address is the easiest way to make a corporate dataset useless, and it is
the kind of thing that quietly comes back.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from graphfaker.engine.addresses import (
    REGISTER,
    RETAIL,
    UNIQUE,
    AddressProfile,
    allocate,
    assign,
    reuse_stats,
    shared_addresses,
)
from graphfaker.engine.seeding import Streams


def test_the_allocation_adds_up_and_is_ordered():
    counts = allocate(10_000)
    assert counts.sum() == 10_000
    assert (np.diff(counts) <= 0).all(), "counts should come out largest first"
    assert (counts > 0).all(), "an address nobody uses is not an address"


def test_the_shape_matches_the_register_where_the_scale_allows():
    """Median, 90th and mean are reproduced; the extreme head is not.

    A register's busiest address holds 6% of every row in it. At 200,000
    organisations this model puts 16,857 at the busiest, which is the same
    order and not the same number, and the tail quantiles run a little hot.
    Asserting the parts that hold and naming the parts that do not is the
    honest version of this test.
    """
    stats = reuse_stats(assign(np.random.default_rng(1), 200_000))
    assert stats["p50"] == 1, "most addresses hold exactly one organisation"
    assert stats["p90"] == 4
    assert 4.5 < stats["mean"] < 5.0  # register: 4.76
    assert 0.5 < stats["top1pct_share"] < 0.6  # register: 0.537
    assert stats["max"] > 1_000, "somebody has to be the registered agent"
    # The tail is heavier than the register's: p99 of about 40 against 31.
    assert 25 < stats["p99"] < 50


def test_a_small_dataset_keeps_the_median_and_loses_the_head():
    """1% of 160 addresses is one address, so the top 1% share of a small
    run is a different statistic from the same number on a register."""
    stats = reuse_stats(assign(np.random.default_rng(1), 500))
    assert stats["p50"] == 1
    assert stats["max"] > 20, "the concentration survives even when the head cannot"
    assert stats["top1pct_share"] < 0.4


def test_a_domain_can_ask_for_one_address_each_and_say_so():
    indices = assign(np.random.default_rng(0), 1_000, UNIQUE)
    assert len(set(indices.tolist())) == len(indices), "every organisation at its own address"
    # The dial that does it is alpha, not the mean. Asking for a mean of one
    # while leaving the curve alone still concentrates, which is the mistake
    # this pins down.
    careless = AddressProfile(mean_per_address=1.0, pool_factor=1.0)
    assert len(set(assign(np.random.default_rng(0), 1_000, careless).tolist())) < 1_000


def test_shared_addresses_returns_columns_that_actually_repeat():
    streams = Streams.root(7)
    columns = shared_addresses(streams, 2_000)
    assert set(columns) == {"address_line1", "address_city", "address_postal_code"}
    assert all(len(v) == 2_000 for v in columns.values())
    distinct = len(set(columns["address_line1"]))
    assert distinct < 2_000, "addresses are supposed to be shared"
    # The parts of one address travel together: the same line always has the
    # same city, or the address is not an address.
    pairs = dict(zip(columns["address_line1"], columns["address_city"], strict=False))
    assert all(
        pairs[line] == city
        for line, city in zip(columns["address_line1"], columns["address_city"], strict=False)
    )


def test_the_same_seed_gives_the_same_addresses():
    first = shared_addresses(Streams.root(11), 500)
    second = shared_addresses(Streams.root(11), 500)
    assert first == second
    assert shared_addresses(Streams.root(12), 500) != first


def test_the_profile_carries_its_provenance():
    assert "register" in REGISTER.source
    assert REGISTER.mean_per_address == 4.76


def test_the_supply_chain_puts_organisations_at_shared_addresses():
    from graphfaker.domains.supply_chain import generate

    run = generate(scale=0.01, seed=42)
    suppliers = run.tables.nodes["Supplier"]
    assert {"address_line1", "address_city", "address_postal_code"} <= set(suppliers.columns)

    counts = suppliers.group_by("address_line1").len()
    assert counts["len"].median() == 1, "most suppliers have an address to themselves"
    assert counts["len"].max() > 10, "and somebody is a registered agent"

    # Customers are a retail base, not companies registered through agents,
    # so their addresses are nearly all their own.
    customers = run.tables.nodes["Customer"]
    retail = customers.group_by("address_line1").len()
    assert retail["len"].median() == 1
    assert retail["len"].max() < 10, "a retail base has no registered agents"
    assert RETAIL.source == "assumed, not measured"

    # The address is not a label: it says nothing about being in a pattern.
    members = set(run.truth["suppliers"]["supplier_id"].to_list())
    flagged = suppliers.filter(pl.col("id").is_in(list(members)))
    busiest = counts.sort("len", descending=True)["address_line1"][0]
    assert flagged.filter(pl.col("address_line1") == busiest).height < flagged.height
