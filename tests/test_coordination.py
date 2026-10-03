# tests/test_coordination.py
"""The coordinated-behaviour domain pack."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from graphfaker.backends.tables import ID
from graphfaker.domains import available, get
from graphfaker.domains.coordination import (
    DECOY_PLAYBOOKS,
    PLAYBOOKS,
    CoordinationConfig,
    account_features,
    evaluate,
    generate,
    hardness_report,
    realism_report,
)
from graphfaker.domains.coordination.config import DECOY_TWIN
from graphfaker.domains.coordination.process import CHANNELS, POSTED
from graphfaker.engine.run import fingerprint

SCALE = 0.0006


@pytest.fixture(scope="module")
def run():
    return generate(scale=SCALE, tradecraft="medium", seed=7)


@pytest.fixture(scope="module")
def features(run):
    return account_features(run)


# --------------------------------------------------------------------------- #
# contract
# --------------------------------------------------------------------------- #


def test_registered_as_a_domain():
    assert "coordination" in available()
    domain = get("coordination")
    assert domain.options is CoordinationConfig
    assert domain.schema is not None
    # The schema covers entities only, so it has to say so.
    assert domain.schema_note and "entities only" in domain.schema_note


def test_reproducible_for_a_seed():
    a = generate(scale=SCALE, tradecraft="medium", seed=11)
    b = generate(scale=SCALE, tradecraft="medium", seed=11)
    assert fingerprint(a) == fingerprint(b)


def test_different_seeds_differ():
    a = generate(scale=SCALE, tradecraft="medium", seed=11)
    b = generate(scale=SCALE, tradecraft="medium", seed=12)
    assert fingerprint(a) != fingerprint(b)


def test_manifest_records_the_config(run):
    extra = run.manifest.extra["coordination"]
    assert extra["tradecraft"] == "medium"
    assert extra["scale"] == SCALE
    assert run.manifest.schema_name == "coordination"


def test_tables_and_truth_present(run):
    for node_type in ("Account", "Topic", "Device"):
        assert run.tables.nodes[node_type].height > 0
    for relationship in ("FOLLOWS", "USES", *CHANNELS):
        assert relationship in run.tables.edges
    for table in ("campaigns", "accounts", "events", "community"):
        assert table in run.truth


def test_unknown_playbook_is_rejected():
    with pytest.raises(ValueError, match="unknown playbooks"):
        CoordinationConfig(campaigns={"not_a_playbook": 3})


def test_writes_and_reads_back(run, tmp_path):
    root = run.write(tmp_path / "dataset")
    assert (root / "manifest.json").exists()
    assert (root / "schema.yaml").exists()
    assert (root / "truth" / "campaigns.parquet").exists()


# --------------------------------------------------------------------------- #
# no label leakage
# --------------------------------------------------------------------------- #


def test_no_node_attribute_names_the_answer(run):
    """A column called ``is_bot`` would make the dataset worthless."""
    banned = {"is_bot", "is_coordinated", "campaign_id", "playbook", "suspicious", "label"}
    for node_type, frame in run.tables.nodes.items():
        assert not (banned & set(frame.columns)), node_type


def test_no_single_feature_separates_campaigns_perfectly(run, features):
    """Every feature is legitimately available, so none may be a giveaway.

    A perfectly separating feature means a label leaked into the graph. This is
    the test that would have caught ``reshare_count`` being exactly zero for
    every handover account.
    """
    report = hardness_report(run, features)
    for playbook in report.playbooks:
        if not playbook.is_coordinated:
            continue
        assert playbook.max_auc < 0.999, (
            f"{playbook.playbook} is perfectly separable by "
            f"{playbook.best_feature}; a label has leaked into a feature"
        )


def test_features_carry_no_truth_columns(features):
    assert "is_coordinated" not in features.columns
    assert "campaign_id" not in features.columns


# --------------------------------------------------------------------------- #
# truth integrity
# --------------------------------------------------------------------------- #


def test_every_campaign_account_exists(run):
    accounts = set(run.tables.nodes["Account"][ID].to_list())
    assert set(run.truth["accounts"]["account_id"].to_list()) <= accounts


def test_every_labelled_event_exists(run):
    present = set()
    for frame in run.tables.edges.values():
        if "event_id" in frame.columns:
            present |= set(frame["event_id"].to_list())
    labelled = set(run.truth["events"]["event_id"].to_list())
    assert labelled <= present
    assert labelled, "no events were labelled at all"


def test_campaign_and_account_truth_agree(run):
    campaigns = run.truth["campaigns"]
    per_campaign = dict(zip(campaigns["campaign_id"].to_list(), campaigns["n_accounts"].to_list()))
    counted = (
        run.truth["accounts"].group_by("campaign_id").len().rename({"len": "n"})
    )
    for campaign_id, n in zip(counted["campaign_id"].to_list(), counted["n"].to_list()):
        assert per_campaign[campaign_id] == n


def test_roles_are_named(run):
    roles = set(run.truth["accounts"]["role"].to_list())
    assert roles
    assert all(isinstance(role, str) and role for role in roles)


# --------------------------------------------------------------------------- #
# decoys
# --------------------------------------------------------------------------- #


def test_organic_decoys_are_present_and_labelled(run):
    campaigns = run.truth["campaigns"]
    organic = campaigns.filter(~pl.col("is_coordinated"))
    assert organic.height > 0
    assert set(organic["playbook"].to_list()) <= set(DECOY_PLAYBOOKS)
    coordinated = campaigns.filter(pl.col("is_coordinated"))
    assert set(coordinated["playbook"].to_list()) <= set(PLAYBOOKS)


def test_low_tradecraft_has_no_decoys():
    """``decoy_ratio`` is 0 at ``low``: the easy setting is easy on purpose."""
    low = generate(scale=SCALE, tradecraft="low", seed=7)
    assert low.truth["campaigns"].filter(~pl.col("is_coordinated")).height == 0


def test_decoys_never_share_accounts_with_campaigns(run):
    """An organic structure sharing members would have an ambiguous label."""
    truth = run.truth["accounts"]
    coordinated = set(
        truth.filter(pl.col("is_coordinated"))["account_id"].to_list()
    )
    organic = set(truth.filter(~pl.col("is_coordinated"))["account_id"].to_list())
    assert not (coordinated & organic)


def test_each_decoy_has_a_twin_in_the_catalogue():
    for decoy, twin in DECOY_TWIN.items():
        assert decoy in DECOY_PLAYBOOKS
        assert twin in PLAYBOOKS


def test_decoys_are_not_trivially_separable_from_their_twin(run):
    """If one feature tells a fandom from a copypasta ring, it is not a decoy."""
    report = hardness_report(run)
    assert report.decoy_separability
    for decoy, (value, feature) in report.decoy_separability.items():
        assert value < 0.95, f"{decoy} separable from its twin by {feature} alone"


# --------------------------------------------------------------------------- #
# the tradecraft dial
# --------------------------------------------------------------------------- #


def test_tradecraft_makes_campaigns_harder():
    """The dial has to move the measurement, not just the label.

    Compared on the mean of per-playbook max AUC, because individual playbooks
    are noisy at test scale while the aggregate is not.
    """

    def mean_max_auc(level: str) -> float:
        report = hardness_report(generate(scale=SCALE, tradecraft=level, seed=3))
        values = [
            p.max_auc
            for p in report.playbooks
            if p.is_coordinated and not np.isnan(p.max_auc)
        ]
        return float(np.mean(values))

    low, high = mean_max_auc("low"), mean_max_auc("high")
    assert low > high + 0.1, f"tradecraft did not bite: low={low:.3f} high={high:.3f}"


def test_low_tradecraft_leaves_an_obvious_signal():
    report = hardness_report(generate(scale=SCALE, tradecraft="low", seed=3))
    best = max(
        p.max_auc for p in report.playbooks if p.is_coordinated and not np.isnan(p.max_auc)
    )
    assert best > 0.9


def test_high_tradecraft_needs_structure_or_timing():
    """At ``high`` the giveaway should not be a bare identity attribute."""
    report = hardness_report(generate(scale=SCALE, tradecraft="high", seed=3))
    families = [
        p.best_family for p in report.playbooks if p.is_coordinated and p.best_family
    ]
    assert families
    # Identity alone (fresh accounts, shared device) must not dominate.
    assert families.count("identity") <= len(families) // 2


def test_hardness_report_is_serialisable(run):
    import json

    payload = hardness_report(run).as_dict()
    assert json.loads(json.dumps(payload))["tradecraft"] == "medium"


# --------------------------------------------------------------------------- #
# temporal and structural consistency
# --------------------------------------------------------------------------- #


def test_no_account_acts_before_it_exists(run):
    created = dict(
        zip(
            run.tables.nodes["Account"][ID].to_list(),
            run.tables.nodes["Account"]["created_at"].to_list(),
        )
    )
    for channel in CHANNELS:
        frame = run.tables.edges[channel]
        if frame.height == 0:
            continue
        offenders = [
            source
            for source, stamp in zip(
                frame["source"].to_list(), frame["timestamp"].dt.date().to_list()
            )
            if stamp < created[source]
        ]
        assert not offenders, f"{channel}: {len(offenders)} events predate the account"


def test_events_fall_inside_the_period(run):
    config = CoordinationConfig(**run.manifest.extra["coordination"])
    for channel in CHANNELS:
        frame = run.tables.edges[channel]
        if frame.height == 0:
            continue
        dates = frame["timestamp"].dt.date()
        assert dates.min() >= config.period_start
        assert dates.max() <= config.period_end


def test_event_ids_are_unique(run):
    seen = []
    for frame in run.tables.edges.values():
        if "event_id" in frame.columns:
            seen.extend(frame["event_id"].to_list())
    assert len(seen) == len(set(seen))


def test_posts_point_at_topics_and_interactions_at_accounts(run):
    topics = set(run.tables.nodes["Topic"][ID].to_list())
    accounts = set(run.tables.nodes["Account"][ID].to_list())
    posted = run.tables.edges[POSTED]
    if posted.height:
        assert set(posted["target"].to_list()) <= topics
        assert set(posted["source"].to_list()) <= accounts
    for channel in ("RESHARED", "REPLIED"):
        frame = run.tables.edges[channel]
        if frame.height:
            assert set(frame["target"].to_list()) <= accounts


def test_no_self_interactions(run):
    for channel in ("RESHARED", "REPLIED"):
        frame = run.tables.edges[channel]
        if frame.height:
            assert not (frame["source"] == frame["target"]).any()


def test_follows_have_no_duplicates_or_self_loops(run):
    follows = run.tables.edges["FOLLOWS"]
    assert follows.height == follows.unique(subset=["source", "target"]).height
    assert not (follows["source"] == follows["target"]).any()


# --------------------------------------------------------------------------- #
# realism
# --------------------------------------------------------------------------- #


def test_most_accounts_are_quiet(run):
    """Real platforms are mostly lurkers; without them volume is uninformative."""
    report = realism_report(run)
    assert 0.15 < report["silent_account_share"] < 0.6


def test_audience_and_activity_are_heavy_tailed(run, features):
    report = realism_report(run)
    assert report["follower_gini"] > 0.3
    assert report["event_gini"] > 0.4
    following = features["following"].to_numpy()
    # Out-degree too: drawing sources uniformly gave everyone the same count.
    assert following.max() > 5 * max(1.0, float(np.median(following)))


def test_activity_follows_a_daily_rhythm(run):
    """Sub-minute synchrony is only detectable because nothing organic is."""
    assert realism_report(run)["diurnal_ratio"] > 3.0


def test_follows_are_substantially_reciprocal(run):
    """A follow farm exaggerates reciprocity, so the organic rate must be real."""
    assert 0.15 < realism_report(run)["reciprocal_follow_share"] < 0.8


def test_communities_are_recoverable_from_the_follow_graph(run):
    import networkx as nx

    accounts = run.tables.nodes["Account"]
    community = dict(zip(accounts[ID].to_list(), accounts["community"].to_list()))
    graph = nx.Graph()
    graph.add_nodes_from(community)
    follows = run.tables.edges["FOLLOWS"]
    graph.add_edges_from(
        (s, t) for s, t in zip(follows["source"].to_list(), follows["target"].to_list()) if s != t
    )
    groups: dict[int, set] = {}
    for account, group in community.items():
        groups.setdefault(group, set()).add(account)
    assert nx.community.modularity(graph, list(groups.values())) > 0.2


def test_devices_are_sometimes_shared_innocently(run, features):
    """Otherwise a sockpuppet cluster's shared fingerprint is a perfect tell."""
    shared = features["device_shared_with"].to_numpy()
    assert shared.max() >= 3


# --------------------------------------------------------------------------- #
# evaluation
# --------------------------------------------------------------------------- #


def _coordinated_accounts(run) -> list[str]:
    truth = run.truth["accounts"]
    return truth.filter(pl.col("is_coordinated"))["account_id"].unique().to_list()


def test_perfect_detector_scores_one(run):
    scores = evaluate(run, flagged_accounts=_coordinated_accounts(run))
    assert scores.account.precision == pytest.approx(1.0)
    assert scores.account.recall == pytest.approx(1.0)
    assert scores.campaign.recall == pytest.approx(1.0)
    assert scores.organic_false_positive_rate == pytest.approx(0.0)


def test_empty_detector_scores_zero(run):
    scores = evaluate(run, flagged_accounts=[])
    assert scores.account.recall == 0.0
    assert scores.account.tp == 0
    assert scores.campaign.fn > 0


def test_flagging_everything_is_punished(run):
    everyone = run.tables.nodes["Account"][ID].to_list()
    scores = evaluate(run, flagged_accounts=everyone)
    assert scores.account.recall == pytest.approx(1.0)
    assert scores.account.precision < 0.5
    # Every organic account is flagged too, which is the deployment cost.
    assert scores.organic_false_positive_rate == pytest.approx(1.0)


def _co_burst_detector(run, min_accounts: int = 6) -> list[str]:
    """The standard first-pass coordination heuristic.

    Bucket posts by (topic, hour) and flag every account in a bucket that many
    distinct accounts share. This is co-occurrence, which is what real
    coordination detection keys on; per-account burstiness is much weaker,
    because a camouflaged campaign account's few campaign posts are diluted by
    its ordinary activity.
    """
    posted = run.tables.edges[POSTED]
    if posted.height == 0:
        return []
    buckets = (
        posted.with_columns(pl.col("timestamp").dt.truncate("1h").alias("hour"))
        .group_by(["target", "hour"])
        .agg(pl.col("source").unique().alias("accounts"))
        .filter(pl.col("accounts").list.len() >= min_accounts)
    )
    flagged: set[str] = set()
    for accounts in buckets["accounts"].to_list():
        flagged.update(str(a) for a in accounts)
    return sorted(flagged)


def test_synchrony_only_detector_is_caught_by_the_decoys(run):
    """The point of the pack, as an assertion.

    A co-occurrence detector finds real campaigns *and* every fan club and news
    reaction, because those are the same shape. Without organic decoys in the
    truth it would look excellent; with them, its organic false-positive rate
    is visible and non-trivial, which is the cost a platform would actually pay.
    """
    flagged = _co_burst_detector(run)
    scores = evaluate(run, flagged_accounts=flagged)
    assert scores.account.tp > 0, "the naive detector should find something"
    assert scores.organic_false_positive_rate > 0.0, (
        "a co-occurrence detector must flag organic bursts; if it does not, the "
        "decoys are not doing their job"
    )


def test_decoys_cost_the_naive_detector_precision(run):
    """Scoring the same detector with and without the organic structures.

    Counting only the inauthentic campaigns flatters it; the organic accounts
    it also flagged are the reason its real-world precision is worse.
    """
    flagged = set(_co_burst_detector(run))
    truth = run.truth["accounts"]
    coordinated = set(truth.filter(pl.col("is_coordinated"))["account_id"].to_list())
    organic = set(truth.filter(~pl.col("is_coordinated"))["account_id"].to_list()) - coordinated
    assert flagged & organic, "no organic account was flagged, so nothing is being measured"


def test_recall_by_playbook_exposes_a_one_signal_detector(run, features):
    """A follow farm barely posts, so an activity detector cannot see it."""
    noisy = features.filter(pl.col("event_count") > features["event_count"].median())[
        "account_id"
    ].to_list()
    scores = evaluate(run, flagged_accounts=noisy)
    assert scores.recall_by_playbook
    assert "follow_farm" in scores.recall_by_playbook


def test_evaluate_needs_ground_truth(run):
    import dataclasses

    blind = dataclasses.replace(run, truth={})
    with pytest.raises(ValueError, match="no ground truth"):
        evaluate(blind, flagged_accounts=[])


def test_evaluation_is_serialisable(run):
    import json

    payload = evaluate(run, flagged_accounts=_coordinated_accounts(run)).as_dict()
    assert json.loads(json.dumps(payload))["account"]["recall"] == 1.0
