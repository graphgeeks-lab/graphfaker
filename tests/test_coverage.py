"""Identifiers that are mostly missing.

The point of the module under test is that a generated company should not
have every column filled, so most of these check that something is absent
often enough. The two that matter beyond the rates are that an identifier
describing a place is the same for everyone at that place, and that the
formats are checkable rather than merely random.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from graphfaker.engine.coverage import (
    COMPLETE,
    REGISTER,
    _mod97,
    address_index,
    apply,
    coverage_stats,
    lei_numbers,
    npi_numbers,
    placekeys,
    websites,
)


def companies(rows: int, addresses: int, *, heavy: bool = False) -> pl.DataFrame:
    """``rows`` companies over ``addresses`` doors, optionally with a head."""
    rng = np.random.default_rng(0)
    if heavy:
        # A quarter of the rows at one address, the way a registered agent
        # holds a quarter of a county.
        at = np.concatenate([np.zeros(rows // 4, dtype=int), rng.integers(1, addresses, size=rows - rows // 4)])
    else:
        at = rng.integers(0, addresses, size=rows)
    return pl.DataFrame(
        {
            "name": [f"COMPANY {i % (rows // 2 or 1)} LLC" for i in range(rows)],
            "address_line1": [f"{100 + int(a)} Example Street" for a in at],
            "address_city": ["Springfield"] * rows,
            "address_postal_code": [f"{10000 + int(a):05d}" for a in at],
        }
    )


def test_a_register_leaves_most_columns_empty():
    frame = apply(np.random.default_rng(1), companies(20_000, 6_000))
    measured = coverage_stats(frame)
    for attribute in REGISTER.attributes:
        assert measured[attribute.name] == pytest.approx(attribute.rate, abs=0.02), attribute.name
    # The headline: nothing is on every row, which is where this started.
    assert max(measured.values()) < 0.75


def test_the_complete_profile_puts_everything_back():
    frame = apply(np.random.default_rng(1), companies(2_000, 800), COMPLETE)
    assert all(value == 1.0 for value in coverage_stats(frame, COMPLETE).values())


def test_a_column_the_frame_already_has_is_thinned_rather_than_replaced():
    before = companies(5_000, 1_500)
    after = apply(np.random.default_rng(2), before)
    kept = after["address_postal_code"].is_not_null()
    assert kept.mean() == pytest.approx(REGISTER.rate("postal_code"), abs=0.02)
    # The postcodes that survive are the ones that were there.
    assert (after.filter(kept)["address_postal_code"] == before.filter(kept)["address_postal_code"]).all()


def test_an_identifier_of_the_place_is_the_same_for_everyone_at_it():
    frame = apply(np.random.default_rng(3), companies(10_000, 2_000))
    for column in ("geo_latitude", "geo_longitude", "address_postal_code"):
        spread = (
            frame.group_by("address_line1")
            .agg(pl.col(column).n_unique().alias("n"), pl.col(column).null_count().alias("nulls"), pl.len())
        )
        # One value per address, and either everybody there has it or
        # nobody does: a door is geocoded or it is not.
        assert (spread["n"] <= 1).all(), column
        assert ((spread["nulls"] == 0) | (spread["nulls"] == spread["len"])).all(), column

    where = frame.filter(pl.col("placekey").is_not_null()).with_columns(
        pl.col("placekey").str.split("@").list.last().alias("where")
    )
    assert (where.group_by("address_line1").agg(pl.col("where").n_unique())["where"] == 1).all()


def test_a_heavy_address_does_not_drag_the_rate_off_target():
    """The reason the place-level draw is weighted.

    A coin flip per address misses the record-level rate by several points
    when one door holds a quarter of the register, because the flip for
    that one door decides a quarter of the answer.
    """
    frame = apply(np.random.default_rng(4), companies(20_000, 3_000, heavy=True))
    assert coverage_stats(frame)["geo"] == pytest.approx(REGISTER.rate("geo"), abs=0.01)


def test_an_identifier_of_the_company_follows_its_name():
    rng = np.random.default_rng(5)
    assert websites(rng, ["RICE, INC"])[0].startswith("www.rice.")
    # Two spellings of one company give one address, which is a join a
    # resolver can make and a register does contain.
    assert websites(np.random.default_rng(5), ["ACME HOLDINGS LLC"])[0] == websites(
        np.random.default_rng(5), ["Acme Holdings, L.L.C."]
    )[0]


def test_a_second_address_line_does_not_land_on_an_address_that_has_one():
    frame = apply(np.random.default_rng(6), pl.DataFrame({
        "name": [f"C{i} LLC" for i in range(4_000)],
        "address_line1": [f"{i} Main Street Suite {i}" if i % 2 else f"{i} Main Street" for i in range(4_000)],
        "address_city": ["Springfield"] * 4_000,
    }))
    carried = frame.filter(pl.col("address_line2").is_not_null())
    assert carried.height, "some records should carry one"
    assert not carried["address_line1"].str.contains("(?i)suite").any()
    assert coverage_stats(frame)["address_line2"] == pytest.approx(REGISTER.rate("address_line2"), abs=0.01)


def test_the_formats_check_out():
    rng = np.random.default_rng(7)
    for lei in lei_numbers(rng, 300):
        assert len(lei) == 20
        assert _mod97(lei) == 1, lei  # ISO 7064: a valid LEI leaves 1

    for npi in npi_numbers(rng, 300):
        assert len(npi) == 10 and npi[0] == "2", npi  # 2 is the organisation range
        digits = [int(c) for c in "80840" + npi]
        total = 0
        for position, digit in enumerate(reversed(digits)):
            doubled = digit * 2 if position % 2 == 1 else digit
            total += doubled - 9 if doubled > 9 else doubled
        assert total % 10 == 0, npi

    places = np.array([0, 0, 1, 1, 2])
    keys = placekeys(rng, places)
    assert len(keys) == 5
    assert keys[0].split("@")[-1] == keys[1].split("@")[-1]
    assert all(len(key.split("@")[-1]) == 11 for key in keys)


def test_an_empty_frame_is_left_alone():
    empty = companies(10, 4).head(0)
    assert apply(np.random.default_rng(8), empty).height == 0
    assert len(address_index(empty)) == 0


# ------------------------------------------------- the pack that uses it


def test_the_supply_chain_companies_look_like_a_register():
    from graphfaker.domains import get

    run = get("supply_chain").run(seed=1, scale=0.02)
    for label in ("Supplier", "Plant", "Warehouse", "Customer"):
        assert set(coverage_stats(run.tables.nodes[label])) == {a.name for a in REGISTER.attributes}, label

    # The rate is a property of the population, so it is checked on the
    # tables big enough to have one: ten plants cannot be 67.3% anything.
    for label in ("Supplier", "Customer"):
        measured = coverage_stats(run.tables.nodes[label])
        for name in ("geo", "postal_code", "placekey", "linkedin"):
            assert measured[name] == pytest.approx(REGISTER.rate(name), abs=0.03), (label, name)

    # Two companies at one door agree, across tables as well as inside one.
    both = pl.concat(
        [run.tables.nodes[label].select("address_line1", "geo_latitude") for label in ("Supplier", "Plant")],
        how="vertical",
    )
    assert (both.group_by("address_line1").agg(pl.col("geo_latitude").n_unique())["geo_latitude"] <= 1).all()
