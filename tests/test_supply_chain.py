"""The supply chain pack: the network, the events on it, and the patterns.

The tests that matter most here are the ones that would catch a label
leaking into the graph. A dataset whose fraud can be found by a column is
worth nothing as a benchmark, and the easiest way to produce one is by
accident: a play that draws its quantities from a different distribution, or
a legitimate process that never does something the play does.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from graphfaker.backends.tables import SOURCE, TARGET
from graphfaker.domains.supply_chain import evaluate, generate, hardness_report, realism_report
from graphfaker.domains.supply_chain.config import CATALOG, DECOY_TWIN, SupplyChainConfig
from graphfaker.domains.supply_chain.hardness import supplier_features
from graphfaker.domains.supply_chain.process import (
    DELIVERS,
    INTERCOMPANY,
    INVOICES,
    ORDERS,
    SHIPS,
)

SCALE = 0.01


@pytest.fixture(scope="module")
def run():
    return generate(scale=SCALE, hardness="medium", seed=5)


@pytest.fixture(scope="module")
def runs():
    return {h: generate(scale=SCALE, hardness=h, seed=5) for h in ("low", "medium", "high")}


# ------------------------------------------------------------------ shape


def test_the_network_has_every_level_and_the_tiers_connect(run):
    nodes = run.tables.nodes
    assert set(nodes) == {
        "Supplier", "Plant", "Warehouse", "Customer", "Product", "Carrier", "Person",
    }
    tiers = set(nodes["Supplier"]["tier"].to_list())
    assert tiers == {1, 2, 3}, "all three tiers exist, or there is nothing to hide behind"

    edges = run.tables.edges
    for name in ("SUPPLIES", "SUBCONTRACTS", "PRODUCES", "STOCKS", "SERVES", "HAULS"):
        assert edges[name].height > 0, name
    # A contract is between a supplier and a plant, and subcontracting runs
    # between suppliers without a company subcontracting to itself.
    assert edges["SUBCONTRACTS"].filter(pl.col(SOURCE) == pl.col(TARGET)).height == 0


def test_events_run_on_the_network_and_inside_the_period(run):
    edges, config = run.tables.edges, run.manifest.extra["supply_chain"]
    start = np.datetime64(config["period_start"], "s")
    end = start + np.timedelta64(config["period_days"] * 86_400, "s")
    for channel in (ORDERS, SHIPS, INVOICES, INTERCOMPANY, DELIVERS):
        frame = edges[channel]
        assert frame.height > 0, channel
        stamps = frame["timestamp"].to_numpy().astype("datetime64[s]")
        assert stamps.min() >= start and stamps.max() <= end, channel
        assert frame["event_id"].n_unique() == frame.height, f"{channel} ids are not unique"

    # Orders are placed with suppliers that have a contract.
    contracted = set(edges["SUPPLIES"][SOURCE].to_list())
    ordered = set(edges[ORDERS][TARGET].to_list())
    assert len(ordered - contracted) == 0, "an order was placed with a supplier under no contract"


def test_event_ids_carry_the_time_order_across_channels(run):
    frames = [
        run.tables.edges[c].select(["event_id", "timestamp"])
        for c in (ORDERS, SHIPS, INVOICES, INTERCOMPANY, DELIVERS)
    ]
    joined = pl.concat(frames).with_columns(
        pl.col("event_id").str.strip_prefix("evt_").cast(pl.Int64).alias("rank")
    )
    assert joined["rank"].n_unique() == joined.height
    by_rank = joined.sort("rank")["timestamp"].to_numpy()
    assert (np.diff(by_rank.astype("datetime64[s]").astype(np.int64)) >= 0).all()


def test_the_calendar_is_visible_in_the_ordering(run):
    """Weekdays over weekends, which is the rhythm a pattern hides in."""
    orders = run.tables.edges[ORDERS]
    weekday = orders["timestamp"].dt.weekday().to_numpy()
    weekend_share = float(np.isin(weekday, (6, 7)).mean())
    assert 0.0 < weekend_share < 0.15, f"weekend share {weekend_share:.3f} is not a working week"


# ------------------------------------------------------------------ truth


def test_the_truth_has_the_shape_every_sink_looks_for(run):
    truth = run.truth
    assert set(truth) == {"patterns", "suppliers", "events", "region", "category"}
    patterns, members, events = truth["patterns"], truth["suppliers"], truth["events"]
    assert {"pattern_id", "play", "is_fraud", "roles", "suppliers"} <= set(patterns.columns)
    assert {"supplier_id", "pattern_id", "play", "role", "is_fraud"} <= set(members.columns)
    assert {"event_id", "pattern_id", "play", "is_fraud"} <= set(events.columns)

    # Memberships and labelled events point at patterns that exist.
    known = set(patterns["pattern_id"].to_list())
    assert set(members["pattern_id"].to_list()) <= known
    assert set(events["pattern_id"].to_list()) <= known
    # And the labelled events really are in the graph.
    in_graph = set()
    for channel in (ORDERS, SHIPS, INVOICES, INTERCOMPANY, DELIVERS):
        in_graph |= set(run.tables.edges[channel]["event_id"].to_list())
    assert set(events["event_id"].to_list()) <= in_graph


def test_both_the_fraud_and_its_twin_are_present_and_labelled(run):
    patterns = run.truth["patterns"]
    plays = set(patterns["play"].to_list())
    assert set(CATALOG.injected) <= plays, "a play in the catalogue was never injected"
    legitimate = patterns.filter(~pl.col("is_fraud"))
    assert legitimate.height > 0, "no decoys: precision would be unmeasurable"
    # A decoy reports the play it imitates, so the truth describes the shape
    # and ``is_fraud`` says whether it is one.
    assert set(legitimate["play"].to_list()) <= set(DECOY_TWIN.values())


def test_no_node_attribute_names_the_answer(run):
    for label, frame in run.tables.nodes.items():
        for column in frame.columns:
            assert "fraud" not in column and "pattern" not in column, f"{label}.{column}"
    # And no edge carries the label either: that lives in truth/.
    for rel, frame in run.tables.edges.items():
        assert "is_fraud" not in frame.columns and "pattern_id" not in frame.columns, rel


# --------------------------------------------------------------- hardness


def test_no_single_feature_separates_a_play_perfectly(runs):
    """A perfectly separating feature means a label has leaked.

    This caught three during development: invoices were never round in the
    legitimate process, every legitimate invoice had a shipment behind it,
    and inter-company movements only ever ran one way, so a reversed one was
    proof of a ring.
    """
    report = hardness_report(runs["high"])
    for play in report.plays:
        if play.is_fraud and play.n_suppliers >= 4:
            assert play.max_auc < 1.0, (
                f"{play.play} is perfectly separable by {play.best_feature}: a label has leaked"
            )


def test_hardness_hides_the_patterns_better_as_it_rises(runs):
    """The dial has to move the measurement, or it is decoration.

    Asserted on the whole catalogue rather than per play, because a single
    play on a few suppliers is a noisy estimate; the per-play curve and the
    one play that stays flat are in ``docs/domains/supply-chain.md`` with
    five-seed numbers.
    """
    def mean_best(report):
        values = [p.max_auc for p in report.plays if p.is_fraud and not np.isnan(p.max_auc)]
        return float(np.mean(values))

    def best(report, play):
        found = [p for p in report.plays if p.play == play and p.is_fraud]
        return found[0].max_auc if found else float("nan")

    # Three seeds, because a play on a handful of suppliers is a noisy
    # estimate and a single run can order two neighbouring levels either
    # way. The docs carry five seeds at a larger scale.
    means = {
        hardness: float(
            np.mean(
                [
                    mean_best(hardness_report(generate(scale=SCALE, hardness=hardness, seed=seed)))
                    for seed in (5, 17, 29)
                ]
            )
        )
        for hardness in ("low", "high")
    }
    assert means["high"] < means["low"] + 0.02, (
        f"high ({means['high']:.3f}) is materially easier than low ({means['low']:.3f})"
    )

    # The phantom supplier is the stable case and the clearest: at low it
    # bills round amounts from an account opened last week, at high only
    # the missing goods are left, and that holds in every run.
    reports = {h: hardness_report(r) for h, r in runs.items()}
    assert best(reports["low"], "phantom_supplier") > 0.99, "low should be easy"
    assert best(reports["high"], "phantom_supplier") < best(reports["low"], "phantom_supplier")


def test_the_report_names_a_feature_and_a_family(run):
    report = hardness_report(run)
    assert report.hardness == "medium"
    assert report.plays and all(p.best_feature for p in report.plays)
    assert all(p.best_family for p in report.plays)
    assert "hardness: medium" in report.summary()
    assert report.as_dict()["plays"][0]["play"]


def test_features_are_computable_without_the_truth(run):
    features = supplier_features(run)
    assert features.height == run.tables.nodes["Supplier"].height
    assert "supplier_id" in features.columns
    assert not features.null_count().sum_horizontal().item()


def test_realism_report_describes_the_ordinary_traffic(run):
    report = realism_report(run)
    # Most orders are scheduled replenishment, and an invoice follows nearly
    # every shipment but not quite: some are still unbilled at period end.
    assert 0.4 < report["scheduled_share"] < 0.8
    assert 0.8 < report["invoice_to_ship_ratio"] <= 1.2
    assert report["suppliers_per_plant"] > 1


def test_a_pattern_displaces_ordinary_business_rather_than_adding_to_it(runs):
    """Camouflage, in this pack, is displacement.

    A company running a scheme does it instead of part of its ordinary
    work, so its total volume stays where it was. Without this a member's
    event count is its usual volume plus the pattern's, and total volume is
    a detector nothing can defeat. At ``low`` nothing is displaced, which is
    what makes ``low`` easy.
    """
    counts = {}
    for hardness, run in runs.items():
        members = set(
            run.truth["suppliers"].filter(pl.col("is_fraud"))["supplier_id"].to_list()
        )
        features = supplier_features(run)
        inside = features.filter(pl.col("supplier_id").is_in(list(members)))
        outside = features.filter(
            ~pl.col("supplier_id").is_in(list(members)) & (pl.col("event_count") > 0)
        )
        counts[hardness] = inside["event_count"].mean() / max(outside["event_count"].mean(), 1.0)
    assert counts["high"] < counts["low"], (
        f"members are {counts['high']:.2f}x their peers at high and "
        f"{counts['low']:.2f}x at low; displacement is not working"
    )


def test_invoices_without_shipments_happen_legitimately(run):
    """Services, tooling and goods invoiced on acceptance.

    If every legitimate invoice had a shipment behind it, the phantom
    supplier play would be findable with a join and nothing else.
    """
    edges = run.tables.edges
    billed = set(edges[SHIPS]["event_id"].to_list())
    referenced = [r for r in edges[INVOICES]["reference"].to_list() if r]
    unmatched = sum(1 for r in referenced if r not in billed)
    assert unmatched > 0, "every invoice has goods behind it, which makes the phantom play trivial"


# --------------------------------------------------------------- scoring


def test_a_threshold_rule_finds_split_orders_and_accuses_the_innocent(run):
    """The pack's own thesis, as an assertion.

    A rule that counts orders just under the approval limit is the first
    thing a procurement team writes. It should find the scheme it was
    written for, be nearly blind to the others, and flag the blanket
    agreements that look exactly like it. If it ever stops flagging them,
    the decoys have stopped working and every precision number measured
    here is flattering.
    """
    threshold = run.manifest.extra["supply_chain"]["approval_threshold"]
    orders = run.tables.edges[ORDERS]
    near = orders.filter(
        (pl.col("amount") > threshold * 0.8) & (pl.col("amount") < threshold)
    )
    flagged = near.group_by(TARGET).len().filter(pl.col("len") >= 4)[TARGET].to_list()

    result = evaluate(run, flagged_suppliers=flagged)
    assert result.recall_by_play["split_orders"] > 0.5, "the rule misses what it was written for"
    # Not zero: the rule flags a tenth of the supplier base, so it picks up
    # a ring member now and then by accident. Across seeds that is 0 to 0.23
    # of the rings against all of the split-order schemes, and an incidental
    # hit from a rule that cannot express a cycle is not detection.
    assert result.recall_by_play["invoice_kiting"] < result.recall_by_play["split_orders"] / 2
    assert result.legitimate_false_positive_rate > 0.0, "the decoys are not being caught"
    assert result.supplier.precision < 0.5, "a one-line rule should not be accurate here"
    assert "legitimate suppliers flagged" in result.summary()


def test_evaluate_reads_a_written_dataset(run, tmp_path):
    root = tmp_path / "chain"
    run.write(root)
    guilty = run.truth["suppliers"].filter(pl.col("is_fraud"))["supplier_id"].to_list()
    result = evaluate(root, flagged_suppliers=guilty)
    assert result.supplier.recall == 1.0 and result.supplier.fn == 0
    assert result.pattern.tp > 0


def test_a_perfect_detector_scores_perfectly_and_an_empty_one_does_not(run):
    guilty = run.truth["suppliers"].filter(pl.col("is_fraud"))["supplier_id"].to_list()
    events = run.truth["events"].filter(pl.col("is_fraud"))["event_id"].to_list()
    perfect = evaluate(run, flagged_suppliers=guilty, flagged_events=events)
    assert perfect.supplier.recall == 1.0 and perfect.event.recall == 1.0
    assert perfect.legitimate_false_positive_rate == 0.0

    nothing = evaluate(run)
    assert nothing.supplier.recall == 0.0 and nothing.supplier.precision == 0.0


# -------------------------------------------------------- reproducibility


def test_the_same_seed_gives_the_same_dataset():
    from graphfaker.engine import fingerprint

    first = generate(scale=0.004, hardness="medium", seed=99)
    second = generate(scale=0.004, hardness="medium", seed=99)
    assert fingerprint(first) == fingerprint(second)
    assert fingerprint(generate(scale=0.004, hardness="medium", seed=100)) != fingerprint(first)


def test_the_config_rejects_a_pattern_it_does_not_have():
    with pytest.raises(ValueError, match="unknown pattern"):
        SupplyChainConfig(patterns={"not_a_play": 3})


def test_small_networks_still_cover_the_catalogue():
    """The floors exist so that a dataset someone tries first is not three
    warehouses and one play."""
    config = SupplyChainConfig(scale=0.0001)
    assert config.num_plants >= 3 and config.num_warehouses >= 4
    assert set(config.pattern_counts) == set(CATALOG.injected)
    assert min(config.pattern_counts.values()) >= 2
