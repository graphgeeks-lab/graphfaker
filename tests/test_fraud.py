"""The fraud / AML domain pack."""

import datetime as dt
import itertools

import numpy as np
import polars as pl
import pytest

from graphfaker.domains import fraud
from graphfaker.domains.fraud.config import TYPOLOGIES, FraudConfig
from graphfaker.domains.fraud.evaluate import evaluate
from graphfaker.domains.fraud.hardness import auc, hardness_report, realism_report
from graphfaker.engine import GraphRun, fingerprint

SCALE = 0.0005  # 5,000 accounts, 45,000 transactions: a few seconds


@pytest.fixture(scope="module")
def run() -> GraphRun:
    return fraud.generate(scale=SCALE, seed=11, hardness="medium")


@pytest.fixture(scope="module")
def low() -> GraphRun:
    return fraud.generate(scale=SCALE, seed=11, hardness="low")


@pytest.fixture(scope="module")
def high() -> GraphRun:
    return fraud.generate(scale=SCALE, seed=11, hardness="high")


# -------------------------------------------------------------------- config


def test_scale_convention_matches_gen_fraud_graph():
    cfg = FraudConfig(scale=1.0)
    assert cfg.num_accounts == 10_000_000
    assert cfg.num_transactions == 90_000_000
    assert cfg.num_patterns == 1_000


def test_every_typology_gets_patterns_at_small_scale():
    counts = FraudConfig(scale=0.0001).pattern_counts
    assert set(counts) == set(TYPOLOGIES)
    assert min(counts.values()) >= 2


def test_pattern_counts_sum_to_total():
    cfg = FraudConfig(scale=0.37)
    assert sum(cfg.pattern_counts.values()) == cfg.num_patterns


def test_explicit_pattern_counts():
    cfg = FraudConfig(patterns={"cycle": 3, "fan_in": 1})
    assert cfg.pattern_counts["cycle"] == 3
    assert cfg.pattern_counts["stack"] == 0
    with pytest.raises(ValueError, match="unknown typologies"):
        FraudConfig(patterns={"laundromat": 1})


# ------------------------------------------------------------------ entities


def test_node_tables_have_the_configured_sizes(run):
    cfg = FraudConfig(scale=SCALE)
    assert run.tables.nodes["Account"].height == cfg.num_accounts
    assert run.tables.nodes["Customer"].height == cfg.num_customers
    assert run.tables.nodes["Merchant"].height == cfg.num_merchants


def test_every_account_has_an_owner_in_the_same_region(run):
    accounts = run.tables.nodes["Account"].select(["id", "customer", "region"])
    customers = run.tables.nodes["Customer"].select(["id", "region"]).rename({"id": "customer", "region": "owner_region"})
    joined = accounts.join(customers, on="customer", how="left")
    assert joined["owner_region"].null_count() == 0
    assert (joined["region"] == joined["owner_region"]).all()
    assert run.tables.edges["OWNS"].height == accounts.height


def test_accounts_open_before_their_first_transaction(run):
    accounts = run.tables.nodes["Account"].select(["id", "opened_at"])
    for channel in ("PAYS", "TRANSFERS", "WIRES"):
        tx = run.tables.edges[channel].select(["source", "timestamp"])
        joined = tx.join(accounts.rename({"id": "source"}), on="source", how="left")
        assert (joined["timestamp"].dt.date() >= joined["opened_at"]).all(), channel


def test_devices_are_shared_by_households(run):
    users = run.tables.edges["USES"].group_by("target").len()
    assert users.filter(pl.col("len") > 1).height > 0
    assert run.tables.nodes["Device"]["first_seen"].null_count() == 0


# ------------------------------------------------------------------- process


def test_transaction_budget_is_met(run):
    cfg = FraudConfig(scale=SCALE)
    total = sum(run.tables.edges[c].height for c in ("PAYS", "TRANSFERS", "WIRES"))
    assert cfg.num_transactions * 0.95 <= total <= cfg.num_transactions * 1.05


def test_transaction_ids_are_unique_and_ordered_in_time(run):
    tx = pl.concat([run.tables.edges[c].select(["tx_id", "timestamp"]) for c in ("PAYS", "TRANSFERS", "WIRES")])
    assert tx["tx_id"].n_unique() == tx.height
    ordered = tx.sort("timestamp")["tx_id"].str.strip_prefix("tx_").cast(pl.Int64)
    assert ordered.is_sorted()


def test_transactions_fall_inside_the_period(run):
    cfg = FraudConfig(scale=SCALE)
    tx = run.tables.edges["TRANSFERS"]
    assert tx["timestamp"].min().date() >= cfg.period_start
    assert tx["timestamp"].max().date() < cfg.period_end


def test_process_is_realistic(run):
    report = realism_report(run)
    assert report["merchant_indegree_gini"] > 0.4, "merchant popularity should be heavy-tailed"
    assert report["transfer_repeat_partner_share"] > 0.3, "people transfer to the same people"
    assert report["transfer_same_region_share"] > 0.5, "transfers stay mostly local"
    assert 0.15 <= report["recurring_share"] <= 0.45
    assert report["weekend_share"] < 2 / 7, "weekends are quieter"
    assert report["night_share_0_6"] < 0.08


def test_salary_arrives_monthly(run):
    salary = run.tables.edges["TRANSFERS"].filter(pl.col("memo") == "salary")
    assert salary.height > 0
    per_account = salary.group_by("target").agg(pl.col("timestamp").dt.month().n_unique().alias("months"))
    assert per_account["months"].max() == 3


def test_amounts_are_income_scaled(run):
    accounts = run.tables.nodes["Account"].select(["id", "customer"]).rename({"id": "source"})
    customers = run.tables.nodes["Customer"].select(["id", "log_income"]).rename({"id": "customer"})
    merchants = run.tables.nodes["Merchant"].select(["id", "category"]).rename({"id": "target"})
    pays = run.tables.edges["PAYS"].join(accounts, on="source").join(customers, on="customer").join(merchants, on="target")
    grocery = pays.filter(pl.col("category") == "grocery")
    by_income = grocery.with_columns((pl.col("log_income") > pl.col("log_income").median()).alias("rich"))
    medians = by_income.group_by("rich").agg(pl.col("amount").median()).sort("rich")
    assert medians["amount"][1] > medians["amount"][0]


# ---------------------------------------------------------------- typologies


def test_every_typology_is_injected_and_labelled(run):
    patterns = run.truth["patterns"]
    assert set(patterns.filter(pl.col("is_fraud"))["typology"].to_list()) == set(TYPOLOGIES)
    assert patterns.filter(~pl.col("is_fraud")).height > 0, "medium hardness adds decoys"
    assert (patterns["n_transactions"] > 0).all()


def test_truth_tables_are_consistent(run):
    patterns, accounts, transactions = run.truth["patterns"], run.truth["accounts"], run.truth["transactions"]
    assert set(accounts["pattern_id"]) == set(patterns["pattern_id"])
    assert set(transactions["pattern_id"]) == set(patterns["pattern_id"])
    all_tx = pl.concat([run.tables.edges[c].select("tx_id") for c in ("PAYS", "TRANSFERS", "WIRES")])
    assert transactions["tx_id"].is_in(all_tx["tx_id"].implode()).all()
    assert (patterns["n_transactions"].sum()) == transactions.height


def test_edge_tables_carry_no_labels(run):
    for frame in run.tables.edges.values():
        assert "pattern_id" not in frame.columns and "is_fraud" not in frame.columns


def test_cycle_closes(run):
    patterns = run.truth["patterns"].filter((pl.col("typology") == "cycle") & pl.col("is_fraud"))
    tx = run.truth["transactions"].join(run.tables.edges["TRANSFERS"], on="tx_id")
    for row in patterns.iter_rows(named=True):
        ring = tx.filter(pl.col("pattern_id") == row["pattern_id"]).sort("timestamp")
        sources, targets = ring["source"].to_list(), ring["target"].to_list()
        assert targets[:-1] == sources[1:], "each hop starts where the last ended"
        assert targets[-1] == sources[0], "the ring closes"


def test_structuring_stays_under_the_threshold(low):
    cfg = FraudConfig(scale=SCALE, hardness="low")
    tx = low.truth["transactions"].filter(pl.col("typology") == "structuring").join(low.tables.edges["TRANSFERS"], on="tx_id")
    assert tx.height > 0
    assert (tx["amount"] < cfg.reporting_threshold).all()
    assert (tx["amount"] >= 0.9 * cfg.reporting_threshold).all()


def test_mules_share_a_device(run):
    mules = run.truth["accounts"].filter(pl.col("role") == "mule")
    owners = run.tables.nodes["Account"].select(["id", "customer"]).rename({"id": "account_id"})
    uses = run.tables.edges["USES"].rename({"source": "customer", "target": "device"})
    per_pattern = mules.join(owners, on="account_id").join(uses, on="customer")
    for _, group in per_pattern.group_by("pattern_id"):
        shared = group.group_by("device").len().filter(pl.col("len") >= 2)
        assert shared.height >= 1


def test_synthetic_identities_collide_on_phone_and_address(run):
    identities = run.truth["accounts"].filter(pl.col("role") == "identity")
    owners = run.tables.nodes["Account"].select(["id", "customer"]).rename({"id": "account_id"})
    customers = run.tables.nodes["Customer"].select(["id", "phone", "street"]).rename({"id": "customer"})
    joined = identities.join(owners, on="account_id").join(customers, on="customer")
    for _, group in joined.group_by("pattern_id"):
        assert group["phone"].n_unique() == 1 and group["street"].n_unique() == 1


def test_low_hardness_strips_camouflage_and_adds_no_decoys(low):
    assert low.truth["patterns"].filter(~pl.col("is_fraud")).height == 0
    fraud_accounts = low.truth["accounts"].filter(pl.col("is_fraud"))["account_id"]
    legit_ids = low.truth["transactions"]["tx_id"]
    legit = run_tx(low).filter(~pl.col("tx_id").is_in(legit_ids.implode()))
    # No pattern account carries legitimate activity at low hardness.
    assert legit.filter(pl.col("source").is_in(fraud_accounts.implode())).height == 0


def run_tx(run: GraphRun) -> pl.DataFrame:
    return pl.concat([run.tables.edges[c] for c in ("PAYS", "TRANSFERS", "WIRES")])


# ------------------------------------------------------------------ hardness


def test_auc():
    assert auc([1, 2, 3, 4], [False, False, True, True]) == 1.0
    assert auc([4, 3, 2, 1], [False, False, True, True]) == 1.0  # symmetric
    assert auc([1, 1, 1, 1], [False, True, False, True]) == 0.5
    assert auc([1, 2], [True, True]) != auc([1, 2], [True, True])  # nan


def test_amount_signal_fades_with_hardness(low, run, high):
    def amount_auc(r):
        rep = hardness_report(r)
        return rep.transactions.filter((pl.col("typology") == "all") & (pl.col("feature") == "amount"))["auc"][0]

    a_low, a_medium, a_high = amount_auc(low), amount_auc(run), amount_auc(high)
    assert a_low > a_medium > a_high
    assert a_low > 0.9
    assert a_high < 0.75


def test_hardness_report_shape(run):
    report = hardness_report(run)
    assert set(report.accounts["typology"]) >= set(TYPOLOGIES) | {"all"}
    assert 0.5 <= report.max_auc <= 1.0
    assert "max single-feature AUC" in report.summary()


# ---------------------------------------------------------------- evaluation


def test_oracle_scores_perfectly(run):
    truth = run.truth
    accounts = truth["accounts"].filter(pl.col("is_fraud"))["account_id"].unique().to_list()
    transactions = truth["transactions"].filter(pl.col("is_fraud"))["tx_id"].to_list()
    result = evaluate(run, accounts, transactions)
    assert result.account.f1 == 1.0 and result.transaction.f1 == 1.0 and result.pattern.recall == 1.0


def test_flagging_decoys_is_a_false_positive(run):
    decoys = run.truth["accounts"].filter(~pl.col("is_fraud"))["account_id"].unique().to_list()
    result = evaluate(run, decoys)
    assert result.account.tp == 0 and result.account.fp == len(decoys)


def test_ring_threshold(run):
    patterns = run.truth["patterns"].filter(pl.col("is_fraud"))
    half = [accs[: (len(accs) + 1) // 2] for accs in patterns["accounts"].to_list()]
    flagged = [a for group in half for a in group]
    assert evaluate(run, flagged, ring_threshold=1.0).pattern.tp < patterns.height
    assert evaluate(run, flagged, ring_threshold=0.5).pattern.tp == patterns.height


def test_evaluate_reads_from_disk(tmp_path, run):
    run.write(tmp_path)
    accounts = run.truth["accounts"].filter(pl.col("is_fraud"))["account_id"].unique().to_list()
    assert evaluate(tmp_path, accounts).account.recall == 1.0


# ------------------------------------------------------------ reproducibility


def test_reproducible():
    a = fraud.generate(scale=SCALE, seed=3)
    b = fraud.generate(scale=SCALE, seed=3)
    assert fingerprint(a) == fingerprint(b)
    assert a.truth["patterns"].equals(b.truth["patterns"])


def test_manifest_records_the_config(run):
    assert run.manifest.extra["fraud"]["hardness"] == "medium"
    assert run.manifest.extra["fraud"]["scale"] == SCALE
    assert run.manifest.node_counts["Account"] == run.tables.nodes["Account"].height


def test_period_is_configurable():
    r = fraud.generate(scale=SCALE, seed=1, period_days=30, period_start=dt.date(2025, 6, 1))
    tx = r.tables.edges["TRANSFERS"]
    assert tx["timestamp"].min().date() >= dt.date(2025, 6, 1)
    assert tx["timestamp"].max().date() < dt.date(2025, 7, 1)


def test_account_blocks_cover_every_account_once():
    from graphfaker.domains.fraud.process import _account_blocks

    counts = np.array([0, 5, 3, 0, 0, 10, 1, 1, 4])
    blocks = _account_blocks(counts, target=6)
    assert blocks[0][0] == 0 and blocks[-1][1] == len(counts)
    assert all(b[0] == a[1] for a, b in itertools.pairwise(blocks)), "contiguous"
    assert all(last > first for first, last in blocks)
    assert sum(counts[a:b].sum() for a, b in blocks) == counts.sum()
    assert _account_blocks(np.zeros(4, dtype=int), 6) == []


def test_transactions_arrive_in_blocks_and_ids_still_rank_time(run):
    """The process builds each channel in parts; the frames are chunked
    concatenations, and tx_id is the rank of the transaction in time across
    all channels."""
    edges = run.tables.edges
    assert edges["PAYS"].n_chunks() > 1
    ranks = pl.concat([f.select("tx_id", "timestamp") for f in (edges["PAYS"], edges["TRANSFERS"], edges["WIRES"])])
    ranks = ranks.with_columns(pl.col("tx_id").str.strip_prefix("tx_").cast(pl.Int64).alias("n")).sort("n")
    assert ranks["n"].to_list() == list(range(ranks.height)), "dense ids"
    assert ranks["timestamp"].is_sorted(), "ids follow time"
