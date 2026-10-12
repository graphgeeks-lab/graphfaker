"""Company names, against the register they were measured from.

The numbers here come from ``benchmarks/realism/corporate-lasvegas.json``,
the 631,846 companies in it, and they are the reason this module exists in
two directions at once. A register's legal forms are a distribution we had
wrong, and a register's names are far more unique than Faker's were at scale.

The test that matters most is the one about scale. At 500 rows the old names
looked fine; at 200,000 one stem carried 1,451 companies, and nobody would
have noticed from a small run.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from graphfaker.engine.names import (
    FORM_SPELLINGS,
    REGISTER,
    UNIQUE,
    collision_stats,
    company_names,
    stem,
    suffixes,
    variants,
)
from graphfaker.engine.seeding import Streams

REFERENCE = Path(__file__).resolve().parents[1] / "benchmarks" / "realism" / "corporate-lasvegas.json"


@pytest.fixture(scope="module")
def reference():
    if not REFERENCE.is_file():
        pytest.skip("the measured reference is not in this checkout")
    return json.loads(REFERENCE.read_text(encoding="utf-8"))


def test_the_profile_is_the_measurement(reference):
    """The numbers in the code are the numbers in the reference file.

    Not a tautology: the profile is written by hand and the reference is
    measured, so this is what stops the two drifting apart.
    """
    measured = reference["legal_form_spellings"]
    for spelling, share in REGISTER.spellings:
        assert measured[spelling or "(none)"] == pytest.approx(share, abs=0.0005), spelling
    assert REGISTER.stem_reuse == pytest.approx(
        reference["name_collision_companies"]["repeated_without_legal_form_share"], abs=0.0005
    )
    assert REGISTER.max_per_stem == reference["shared_names"]["max"]
    assert "631,846 companies" in REGISTER.source


def test_the_legal_form_mix_is_the_measured_one():
    names = company_names(Streams.root(1), 20_000)
    mix = collision_stats(names)["form_mix"]
    expected = dict(REGISTER.spellings)
    for spelling in ("LLC", "INC", "L.L.C", "CORPORATION", "LTD", "CORP"):
        assert mix[spelling] == pytest.approx(expected[spelling], abs=0.01), spelling
    # Faker's own provider offers PLC and "and Sons", which a US register
    # does not contain at any rate worth generating.
    assert "PLC" not in mix and "AND SONS" not in mix


def test_half_the_names_put_a_comma_before_the_form():
    names = [n for n in company_names(Streams.root(2), 20_000) if stem(n) != n.strip().upper()]
    with_comma = sum(1 for n in names if "," in n)
    assert with_comma / len(names) == pytest.approx(REGISTER.comma, abs=0.02)


def test_name_reuse_holds_at_every_scale():
    """The failure this module was written for.

    Faker's company provider gave 1.63% repeated names at 500 rows and 10.31%
    at 200,000, with one stem on 1,451 companies, because a thousand surnames
    do not go round. A rate that moves with the row count cannot be compared
    with a register's.
    """
    for rows in (500, 5_000, 50_000):
        stats = collision_stats(company_names(Streams.root(3), rows))
        assert stats["repeated_stem_share"] == pytest.approx(REGISTER.stem_reuse, abs=0.006), rows
        assert stats["repeated_exact_share"] < 0.01, rows
        assert stats["max_per_stem"] <= REGISTER.max_per_stem, rows


def test_a_domain_can_ask_for_unique_names_and_say_so():
    names = company_names(Streams.root(4), 2_000, UNIQUE)
    assert len({stem(n) for n in names}) == len(names), "every company its own name"
    assert UNIQUE.source == "not a register"
    # The suffixes are still a register's: unique is about the stem.
    assert collision_stats(names)["form_mix"]["LLC"] == pytest.approx(0.539, abs=0.03)


def test_the_same_seed_gives_the_same_names():
    assert company_names(Streams.root(9), 300) == company_names(Streams.root(9), 300)
    assert company_names(Streams.root(10), 300) != company_names(Streams.root(9), 300)


def test_the_stem_is_what_two_records_of_one_company_share():
    assert stem("ACME HOLDINGS, LLC") == "ACME HOLDINGS"
    assert stem("Acme Holdings L.L.C.") == "ACME HOLDINGS"
    # Only the trailing form goes: GROUP in the middle is part of the name.
    assert stem("THE ACME GROUP OF NEVADA") == "ACME GROUP NEVADA"
    assert stem("ACME") == "ACME"


def test_the_stem_agrees_with_the_one_the_reference_measures():
    """The benchmark strips names its own way, and the two have to agree.

    ``benchmarks/realism/corporate.py`` normalises a name by dropping every
    legal-form token wherever it appears; this module takes the trailing form
    off. On generated names they must come out the same, or the measured reuse
    rate and the generated one are not the same statistic.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks" / "realism"))
    from corporate import normalise

    names = company_names(Streams.root(5), 3_000)
    assert [stem(n) for n in names] == [normalise(n) for n in names]


def test_variants_are_spellings_the_register_contains():
    rng = np.random.default_rng(0)
    written = variants("ACME HOLDINGS, LLC", rng, 6)
    assert "ACME HOLDINGS LLC" in written, "the comma is optional in the real data"
    assert any("L.L.C" in v for v in written), "and so is the punctuation inside the form"
    assert "ACME HOLDINGS" in written, "and the form itself is sometimes absent"
    # A variant is the same company: the stem never changes.
    assert {stem(v) for v in written} == {"ACME HOLDINGS"}
    # Every alternative is a spelling of the form the name already had.
    assert all(
        any(s in v for s, _ in FORM_SPELLINGS["LLC"]) or v == "ACME HOLDINGS" for v in written
    )
    assert variants("ACME HOLDINGS", rng, 3) == [], "nothing to vary without a form"


def test_suffixes_are_drawn_not_cycled():
    rng = np.random.default_rng(1)
    drawn = suffixes(rng, 50)
    assert len(set(drawn)) > 1
    assert all(s in {spelling for spelling, _ in REGISTER.spellings} for s in drawn)


def test_the_supply_chain_names_its_companies_like_a_register():
    from graphfaker.domains.supply_chain import generate

    run = generate(scale=0.01, seed=42)
    suppliers = run.tables.nodes["Supplier"]["name"].to_list()
    mix = collision_stats(suppliers)["form_mix"]
    assert mix.get("LLC", 0) > 0.4, "a US register is mostly LLCs, and ours was 5.6%"

    # A warehouse is a site, not a legal entity, and keeps its place name.
    warehouses = run.tables.nodes["Warehouse"]["name"].to_list()
    assert not any("LLC" in str(name).upper() for name in warehouses)

    # The name is not a label: being in a pattern does not change how a
    # company is called.
    members = set(run.truth["suppliers"].filter(pl.col("is_fraud"))["supplier_id"].to_list())
    frame = run.tables.nodes["Supplier"]
    flagged = frame.filter(pl.col("id").is_in(list(members)))["name"].to_list()
    if flagged:
        flagged_llc = sum(1 for n in flagged if "LLC" in n.upper()) / len(flagged)
        overall = sum(1 for n in suppliers if "LLC" in n.upper()) / len(suppliers)
        assert abs(flagged_llc - overall) < 0.35
