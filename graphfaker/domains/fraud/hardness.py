"""Measure, rather than assert, how hard the injected fraud is to find.

For every typology, each single feature a naive detector might threshold on
(amount, round amounts, proximity to the reporting threshold, degree,
pass-through ratio, burstiness, account age) is scored by the AUC it
achieves separating that typology's accounts (or transactions) from
legitimate ones. ``max_auc`` is the number to quote: at ``hardness="high"``
no single feature should exceed ~0.7, which means the pattern is only
findable by looking at structure.

The same module produces a realism report: the properties of the
legitimate process a reader would check first.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from graphfaker.engine.run import GraphRun

ACCOUNT_FEATURES = [
    "tx_count",
    "in_count",
    "out_count",
    "in_amount",
    "out_amount",
    "max_amount",
    "mean_amount",
    "in_partners",
    "out_partners",
    "pass_through",
    "round_share",
    "near_threshold_share",
    "burst_share",
    "account_age_days",
]
TRANSACTION_FEATURES = ["amount", "is_round", "near_threshold", "hour", "is_weekend"]


def auc(scores: np.ndarray, positive: np.ndarray) -> float:
    """Mann–Whitney AUC with tie handling; symmetric so a feature that works
    in either direction scores above 0.5."""
    scores = np.asarray(scores, dtype=float)
    positive = np.asarray(positive, dtype=bool)
    n_pos, n_neg = int(positive.sum()), int((~positive).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    # Average ranks for ties: rank of a value = mean of the 1-based positions
    # its group would occupy in sorted order.
    _, inverse, counts = np.unique(scores, return_inverse=True, return_counts=True)
    ends = np.cumsum(counts)
    starts = ends - counts + 1
    ranks = ((starts + ends) / 2.0)[inverse]
    value = (ranks[positive].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
    return float(max(value, 1.0 - value))


def _transactions(run: GraphRun) -> pl.DataFrame:
    parts = []
    for channel in ("PAYS", "TRANSFERS", "WIRES"):
        frame = run.tables.edges.get(channel)
        if frame is not None and frame.height:
            parts.append(frame.with_columns(pl.lit(channel).alias("channel")))
    return pl.concat(parts) if parts else pl.DataFrame()


def account_features(run: GraphRun, threshold: float) -> pl.DataFrame:
    tx = _transactions(run)
    accounts = run.tables.nodes["Account"].select(["id", "opened_at"])
    period_start = run.manifest.extra["fraud"]["period_start"]
    if tx.height == 0:
        return accounts.rename({"id": "account_id"}).with_columns([pl.lit(0.0).alias(f) for f in ACCOUNT_FEATURES])

    tx = tx.with_columns(
        (pl.col("amount") % 100 == 0).alias("is_round"),
        ((pl.col("amount") >= 0.85 * threshold) & (pl.col("amount") < threshold)).alias("near_threshold"),
        pl.col("timestamp").dt.date().alias("day"),
    )
    out = tx.group_by("source").agg(
        pl.len().alias("out_count"),
        pl.col("amount").sum().alias("out_amount"),
        pl.col("target").n_unique().alias("out_partners"),
        pl.col("amount").max().alias("max_out"),
        pl.col("amount").mean().alias("mean_out"),
        pl.col("is_round").mean().alias("round_share"),
        pl.col("near_threshold").mean().alias("near_threshold_share"),
    ).rename({"source": "account_id"})
    inbound = tx.filter(pl.col("channel") == "TRANSFERS").group_by("target").agg(
        pl.len().alias("in_count"),
        pl.col("amount").sum().alias("in_amount"),
        pl.col("source").n_unique().alias("in_partners"),
        pl.col("amount").max().alias("max_in"),
    ).rename({"target": "account_id"})
    # Burstiness: share of an account's outgoing transactions on its busiest day.
    per_day = tx.group_by(["source", "day"]).len().group_by("source").agg(pl.col("len").max().alias("busiest_day")).rename({"source": "account_id"})

    features = (
        accounts.rename({"id": "account_id"})
        .join(out, on="account_id", how="left")
        .join(inbound, on="account_id", how="left")
        .join(per_day, on="account_id", how="left")
        .fill_null(0)
    )
    return features.with_columns(
        (pl.col("in_count") + pl.col("out_count")).alias("tx_count"),
        pl.max_horizontal("max_out", "max_in").alias("max_amount"),
        pl.col("mean_out").alias("mean_amount"),
        (
            pl.min_horizontal("in_amount", "out_amount")
            / pl.max_horizontal("in_amount", "out_amount").clip(lower_bound=1e-9)
        ).alias("pass_through"),
        (pl.col("busiest_day") / pl.col("out_count").clip(lower_bound=1)).alias("burst_share"),
        (pl.lit(period_start).str.to_date() - pl.col("opened_at")).dt.total_days().cast(pl.Float64).alias("account_age_days"),
    ).select(["account_id", *ACCOUNT_FEATURES])


def transaction_features(run: GraphRun, threshold: float) -> pl.DataFrame:
    tx = _transactions(run)
    if tx.height == 0:
        return pl.DataFrame({"tx_id": []}).with_columns([pl.lit(0.0).alias(f) for f in TRANSACTION_FEATURES])
    return tx.select(
        "tx_id",
        pl.col("amount"),
        (pl.col("amount") % 100 == 0).cast(pl.Float64).alias("is_round"),
        ((pl.col("amount") >= 0.85 * threshold) & (pl.col("amount") < threshold)).cast(pl.Float64).alias("near_threshold"),
        pl.col("timestamp").dt.hour().cast(pl.Float64).alias("hour"),
        (pl.col("timestamp").dt.weekday() >= 6).cast(pl.Float64).alias("is_weekend"),
    )


@dataclass
class HardnessReport:
    accounts: pl.DataFrame  # typology, feature, auc, positives
    transactions: pl.DataFrame

    @property
    def max_auc(self) -> float:
        values = [
            v for frame in (self.accounts, self.transactions)
            if frame.height for v in frame["auc"].drop_nans().to_list()
        ]
        return max(values) if values else float("nan")

    def worst(self, n: int = 8) -> pl.DataFrame:
        both = pl.concat([
            self.accounts.with_columns(pl.lit("account").alias("level")),
            self.transactions.with_columns(pl.lit("transaction").alias("level")),
        ])
        return both.drop_nans("auc").sort("auc", descending=True).head(n)

    def summary(self) -> str:
        lines = [f"hardness report: max single-feature AUC {self.max_auc:.3f}", ""]
        lines.append(f"{'level':<12}{'typology':<20}{'feature':<22}{'auc':>7}")
        for row in self.worst().iter_rows(named=True):
            lines.append(f"{row['level']:<12}{row['typology']:<20}{row['feature']:<22}{row['auc']:>7.3f}")
        return "\n".join(lines)


def hardness_report(run: GraphRun) -> HardnessReport:
    threshold = float(run.manifest.extra["fraud"]["reporting_threshold"])
    acc_truth = run.truth["accounts"]
    tx_truth = run.truth["transactions"]

    features = account_features(run, threshold)
    fraud_accounts = acc_truth.filter(pl.col("is_fraud"))
    ids = features["account_id"]
    negatives = ~ids.is_in(fraud_accounts["account_id"].implode()).to_numpy()

    rows = []
    typologies = sorted(set(fraud_accounts["typology"].to_list()))
    for typology in [*typologies, "all"]:
        members = fraud_accounts if typology == "all" else fraud_accounts.filter(pl.col("typology") == typology)
        positive = ids.is_in(members["account_id"].implode()).to_numpy()
        mask = positive | negatives
        for feature in ACCOUNT_FEATURES:
            rows.append({
                "typology": typology,
                "feature": feature,
                "auc": auc(features[feature].to_numpy()[mask], positive[mask]),
                "positives": int(positive.sum()),
            })
    accounts = pl.DataFrame(rows) if rows else pl.DataFrame({"typology": [], "feature": [], "auc": [], "positives": []})

    tx_features = transaction_features(run, threshold)
    fraud_tx = tx_truth.filter(pl.col("is_fraud"))
    tx_ids = tx_features["tx_id"]
    tx_negative = ~tx_ids.is_in(fraud_tx["tx_id"].implode()).to_numpy()
    rows = []
    for typology in [*sorted(set(fraud_tx["typology"].to_list())), "all"]:
        members = fraud_tx if typology == "all" else fraud_tx.filter(pl.col("typology") == typology)
        positive = tx_ids.is_in(members["tx_id"].implode()).to_numpy()
        mask = positive | tx_negative
        for feature in TRANSACTION_FEATURES:
            rows.append({
                "typology": typology,
                "feature": feature,
                "auc": auc(tx_features[feature].to_numpy()[mask], positive[mask]),
                "positives": int(positive.sum()),
            })
    transactions = pl.DataFrame(rows) if rows else pl.DataFrame({"typology": [], "feature": [], "auc": [], "positives": []})
    return HardnessReport(accounts=accounts, transactions=transactions)


# ---------------------------------------------------------------- realism


def gini(values: np.ndarray) -> float:
    values = np.sort(np.asarray(values, dtype=float))
    if len(values) == 0 or values.sum() == 0:
        return 0.0
    n = len(values)
    return float((2 * np.arange(1, n + 1) - n - 1).dot(values) / (n * values.sum()))


def realism_report(run: GraphRun) -> dict[str, float]:
    """Headline properties of the legitimate process."""
    tx = _transactions(run)
    transfers = run.tables.edges["TRANSFERS"]
    pays = run.tables.edges["PAYS"]
    accounts = run.tables.nodes["Account"].select(["id", "region"])
    report: dict[str, float] = {}
    if pays.height:
        report["merchant_indegree_gini"] = gini(pays.group_by("target").len()["len"].to_numpy())
    if transfers.height:
        pairs = transfers.group_by(["source", "target"]).len()
        report["transfer_repeat_partner_share"] = float(pairs.filter(pl.col("len") > 1)["len"].sum() / transfers.height)
        joined = (
            transfers.join(accounts.rename({"id": "source", "region": "src_region"}), on="source", how="left")
            .join(accounts.rename({"id": "target", "region": "dst_region"}), on="target", how="left")
        )
        report["transfer_same_region_share"] = float((joined["src_region"] == joined["dst_region"]).mean())
        report["transfer_indegree_gini"] = gini(transfers.group_by("target").len()["len"].to_numpy())
    if tx.height:
        report["recurring_share"] = float(tx["recurring"].mean())
        report["weekend_share"] = float((tx["timestamp"].dt.weekday() >= 6).mean())
        hours = tx["timestamp"].dt.hour().to_numpy()
        report["night_share_0_6"] = float(np.mean((hours >= 0) & (hours < 6)))
        report["amount_median"] = float(tx["amount"].median())
        report["amount_p99"] = float(tx["amount"].quantile(0.99))
    return report
