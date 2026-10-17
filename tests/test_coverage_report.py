"""What a dataset contains, measured from its truth tables.

The report is domain-agnostic, so most of these run against all three packs
rather than against one: a pack that gets the report by existing is only
worth having if it actually works for a pack nobody tuned it against.
"""

from __future__ import annotations

import polars as pl
import pytest

from graphfaker.domains import get
from graphfaker.engine.coverage_report import coverage_report
from graphfaker.schema.patterns import PatternCatalog, PatternSpec

PACKS = (
    ("fraud", {"scale": 0.002, "seed": 1}),
    ("supply_chain", {"scale": 0.004, "seed": 3}),
    ("coordination", {"scale": 0.0006, "seed": 1}),
)


@pytest.fixture(scope="module")
def runs():
    return {name: get(name).run(**options) for name, options in PACKS}


@pytest.mark.parametrize("pack", [name for name, _ in PACKS])
def test_every_catalogue_shape_gets_a_row(runs, pack):
    report = coverage_report(runs[pack])
    catalog = get(pack).patterns
    assert report.shapes["shape"].to_list() == list(catalog.names)
    assert set(report.shapes["kind"].to_list()) == {"injected", "decoy"}
    # A shape with no pattern is a row of zeros rather than an absent row:
    # the point of the report is saying what did not happen.
    present, total = report.shapes_covered
    assert total == len(catalog.names)
    assert present == total - len(report.missing)


@pytest.mark.parametrize("pack", [name for name, _ in PACKS])
def test_the_counts_add_up_to_the_truth_tables(runs, pack):
    run = runs[pack]
    report = coverage_report(run)
    truth = run.truth
    patterns = next(f for f in truth.values() if "roles" in f.columns)
    members = next(f for n, f in truth.items() if "role" in f.columns)
    assert report.shapes["patterns"].sum() == patterns.height
    assert report.shapes["members"].sum() == members.height


@pytest.mark.parametrize("pack", [name for name, _ in PACKS])
def test_a_decoy_is_counted_as_itself_whichever_name_the_pack_wrote(runs, pack):
    """The two conventions in the packs, read the same way.

    Fraud and supply chain record a decoy under the name of the shape it
    imitates, so the shape column says what the structure looks like and the
    label says whether it is the thing being looked for. Coordination records
    a decoy under its own playbook name. Counting by the shape column alone
    gets one of the two wrong, and which one depends on the pack.
    """
    run = runs[pack]
    report = coverage_report(run)
    catalog = get(pack).patterns

    patterns = next(f for f in run.truth.values() if "roles" in f.columns)
    label = next(c for c, t in patterns.schema.items() if t == pl.Boolean)
    unlabelled = int((~patterns[label]).sum())

    decoys = report.shapes.filter(pl.col("kind") == "decoy")["patterns"].sum()
    assert decoys == unlabelled, "every unlabelled pattern is a decoy of some shape"
    injected = report.shapes.filter(pl.col("kind") == "injected")["patterns"].sum()
    assert injected == int(patterns[label].sum())
    assert set(report.shapes.filter(pl.col("kind") == "decoy")["shape"]) == set(catalog.decoys)


@pytest.mark.parametrize("pack", [name for name, _ in PACKS])
def test_a_decoy_is_matched_to_the_shape_it_imitates(runs, pack):
    report = coverage_report(runs[pack])
    catalog = get(pack).patterns
    assert report.twins["shape"].to_list() == list(catalog.injected)
    declared = {row["shape"]: row["decoy"] for row in report.twins.iter_rows(named=True)}
    for decoy, imitates in catalog.twins.items():
        assert declared[imitates] == decoy
    # A shape with no decoy says so rather than reporting zero, because
    # "no decoy was declared" and "the decoy never fired" are different
    # problems and only one of them is a budget.
    without = [name for name, decoy in declared.items() if decoy is None]
    assert set(without) == set(catalog.injected) - set(catalog.twins.values())


@pytest.mark.parametrize("pack", [name for name, _ in PACKS])
def test_the_regimes_are_shares_of_the_run(runs, pack):
    report = coverage_report(runs[pack])
    assert report.regimes.height
    for dimension in ("channel", "day part", "week part"):
        rows = report.regimes.filter(pl.col("dimension") == dimension)
        assert rows.height, dimension
        # Each dimension partitions the events, so its shares sum to one.
        assert rows["background_share"].sum() == pytest.approx(1.0, abs=0.01), dimension
        assert rows["pattern_share"].sum() == pytest.approx(1.0, abs=0.01), dimension


def test_amounts_are_a_regime_only_where_a_pack_has_them(runs):
    assert coverage_report(runs["fraud"]).regimes.filter(pl.col("dimension") == "amount decile").height == 10
    # The coordination pack has no amounts, so it reports no amount buckets
    # rather than ten empty ones.
    assert coverage_report(runs["coordination"]).regimes.filter(
        pl.col("dimension") == "amount decile"
    ).height == 0


def test_the_segments_are_parts_of_the_population_rather_than_identifiers(runs):
    report = coverage_report(runs["fraud"])
    attributes = set(report.segments["attribute"].to_list())
    assert "account_type" in attributes and "status" in attributes
    assert "id" not in attributes and "balance" not in attributes

    # A sparse identifier is low-cardinality for the wrong reason: an LEI on
    # nine rows has nine distinct values and nobody belongs to any of them.
    supply = coverage_report(runs["supply_chain"])
    assert not any(a.startswith(("registration_number", "lei", "npi")) for a in supply.segments["attribute"])

    for row in report.segments.iter_rows(named=True):
        assert row["recruited"] <= row["population"]


def test_a_run_with_no_patterns_reports_nothing_rather_than_failing():
    report = coverage_report(get("social").run(seed=1, total_nodes=200, total_edges=400))
    assert report.empty
    assert "no injected patterns" in report.summary()
    assert report.as_dict()["shapes_covered"] == 0


def test_without_the_catalogue_it_reports_what_is_there_and_not_what_is_missing(runs):
    run = runs["fraud"]
    tiny = PatternCatalog(patterns=[PatternSpec(name="fan_in", share=1.0)])
    report = coverage_report(run, tiny)
    assert report.shapes["shape"].to_list() == ["fan_in"]
    assert report.shapes_covered == (1, 1)


def test_the_summary_and_the_dict_say_the_same_thing(runs):
    report = coverage_report(runs["supply_chain"])
    data = report.as_dict()
    assert data["shapes_covered"] == report.shapes_covered[0]
    assert len(data["shapes"]) == report.shapes.height
    assert len(data["twins"]) == report.twins.height
    text = report.summary()
    for shape in report.shapes["shape"].to_list():
        assert shape in text


def test_roles_are_reported_per_shape(runs):
    report = coverage_report(runs["fraud"])
    assert report.roles.height
    by_shape = dict(zip(report.shapes["shape"].to_list(), report.shapes["roles"].to_list(), strict=True))
    assert "collector" in by_shape["fan_in"]
    # The roles column on the shapes table is the same thing the roles frame
    # holds, so a reader of either gets the same answer.
    for shape, roles in by_shape.items():
        listed = set(report.roles.filter(pl.col("shape") == shape)["role"].to_list())
        assert set(r for r in roles.split(", ") if r) == listed, shape
