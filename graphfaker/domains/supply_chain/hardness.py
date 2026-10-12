"""How visible the injected patterns are, measured rather than asserted.

``hardness`` is three numbers in a preset; this is what those numbers buy.
Every feature here is one a procurement analyst would reach for first, and
each is scored by the AUC it achieves separating the suppliers in a play from
the suppliers in nothing. A play nobody can find by any single feature is
hard; a play found at 0.99 by ``invoice_without_shipment`` is a tutorial.

The second table is the one that matters more. ``decoy_separability`` asks
the same question of each legitimate twin against the play it imitates: if
one feature tells a blanket agreement from a split-order scheme, the decoy is
decoration rather than a hard negative, and any precision measured against it
is flattering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl

from graphfaker.backends.tables import ID, SOURCE, TARGET
from graphfaker.domains.supply_chain.config import DECOY_TWIN
from graphfaker.domains.supply_chain.process import (
    DELIVERS,
    INTERCOMPANY,
    INVOICES,
    ORDERS,
    SHIPS,
)
from graphfaker.engine.run import GraphRun

#: What kind of evidence each feature is. A play found only by ``identity``
#: is found by onboarding dates; one found by ``structure`` needs the graph.
FEATURE_FAMILIES = {
    "volume": ("invoice_count", "order_count", "shipment_count", "intercompany_count"),
    "money": ("invoice_total", "mean_amount", "max_amount", "round_amount_share", "repeat_amount_share"),
    "fulfilment": ("invoice_without_shipment", "unit_price_ratio", "mean_quantity"),
    "threshold": ("under_threshold_share", "near_threshold_share"),
    "structure": ("distinct_plants", "distinct_partners", "intercompany_partners", "reciprocity"),
    # What a supplier spends its time doing. A company whose entire activity
    # is moving stock between related parties, or billing without shipping,
    # is worth a look whatever the amounts say. This family exists because
    # the dials that strip a pattern member's ordinary trading made it
    # *quieter* than its peers, and a feature set that only counted events
    # scored quiet as normal.
    "profile": ("intercompany_only", "invoice_only", "event_count"),
    "identity": ("onboarded_days", "tier"),
}


def auc(positive: np.ndarray, negative: np.ndarray) -> float:
    """Probability a random positive scores above a random negative.

    The rank form, so it costs a sort rather than a loop, and ties count as
    half, which is what makes a constant feature score 0.5 instead of 1.0.
    """
    positive = positive[~np.isnan(positive)]
    negative = negative[~np.isnan(negative)]
    if not len(positive) or not len(negative):
        return float("nan")
    joined = np.concatenate([positive, negative])
    order = joined.argsort()
    ranks = np.empty(len(joined), dtype=np.float64)
    ranks[order] = np.arange(1, len(joined) + 1)
    # Average the ranks of tied values, or a feature everyone shares looks
    # like a perfect detector.
    unique, inverse, counts = np.unique(joined, return_inverse=True, return_counts=True)
    sums = np.zeros(len(unique))
    np.add.at(sums, inverse, ranks)
    ranks = (sums / counts)[inverse]
    n_pos, n_neg = len(positive), len(negative)
    return float((ranks[:n_pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def supplier_features(run: GraphRun) -> pl.DataFrame:
    """One row per supplier, with the features a first pass would try.

    Everything here is computable from the tables a user gets. Nothing reads
    the truth, which is the property that makes the AUCs below mean anything.
    """
    suppliers = run.tables.nodes["Supplier"]
    edges = run.tables.edges
    threshold = float(run.manifest.extra.get("supply_chain", {}).get("approval_threshold", 25_000.0))
    frame = suppliers.select(
        [ID, pl.col("tier").cast(pl.Float64).alias("tier")]
    ).rename({ID: "supplier_id"})

    def by_supplier(channel: str, column: str, how: str = SOURCE) -> pl.DataFrame:
        table = edges.get(channel)
        if table is None or table.height == 0:
            return pl.DataFrame({"supplier_id": [], column: []}, schema={"supplier_id": pl.String, column: pl.Float64})
        return table.group_by(how).len().rename({how: "supplier_id", "len": column}).with_columns(
            pl.col(column).cast(pl.Float64)
        )

    frame = frame.join(by_supplier(INVOICES, "invoice_count"), on="supplier_id", how="left")
    frame = frame.join(by_supplier(SHIPS, "shipment_count"), on="supplier_id", how="left")
    frame = frame.join(by_supplier(ORDERS, "order_count", how=TARGET), on="supplier_id", how="left")
    frame = frame.join(by_supplier(INTERCOMPANY, "intercompany_count"), on="supplier_id", how="left")

    invoices = edges.get(INVOICES)
    if invoices is not None and invoices.height:
        money = invoices.group_by(SOURCE).agg(
            pl.col("amount").sum().alias("invoice_total"),
            pl.col("amount").mean().alias("mean_amount"),
            pl.col("amount").max().alias("max_amount"),
            pl.col("quantity").mean().cast(pl.Float64).alias("mean_quantity"),
            # Somebody typed it: invoices for exact multiples of 500.
            ((pl.col("amount") % 500 == 0).mean()).alias("round_amount_share"),
            pl.col(TARGET).n_unique().cast(pl.Float64).alias("distinct_plants"),
        ).rename({SOURCE: "supplier_id"})
        frame = frame.join(money, on="supplier_id", how="left")

    orders = edges.get(ORDERS)
    if orders is not None and orders.height:
        thresholds = orders.group_by(TARGET).agg(
            ((pl.col("amount") < threshold).mean()).alias("under_threshold_share"),
            # The band a split order lives in: close enough to the limit to
            # be deliberate, which is the rule a procurement team writes.
            (((pl.col("amount") > threshold * 0.8) & (pl.col("amount") < threshold)).mean()).alias(
                "near_threshold_share"
            ),
        ).rename({TARGET: "supplier_id"})
        frame = frame.join(thresholds, on="supplier_id", how="left")

    ships = edges.get(SHIPS)
    if ships is not None and ships.height:
        unit = ships.with_columns(
            (pl.col("amount") / pl.col("quantity").cast(pl.Float64).clip(1.0)).alias("unit_price")
        )
        norm = float(unit["unit_price"].median() or 1.0)
        per_supplier = unit.group_by(SOURCE).agg(
            (pl.col("unit_price").mean() / norm).alias("unit_price_ratio")
        ).rename({SOURCE: "supplier_id"})
        frame = frame.join(per_supplier, on="supplier_id", how="left")

    # Invoices with no shipment behind them, by count rather than by join:
    # a supplier that bills more often than it ships is the phantom signal.
    frame = frame.with_columns(
        (
            (pl.col("invoice_count").fill_null(0) - pl.col("shipment_count").fill_null(0))
            / pl.col("invoice_count").fill_null(0).clip(1.0)
        ).alias("invoice_without_shipment")
    )

    moves = edges.get(INTERCOMPANY)
    if moves is not None and moves.height:
        # The oldest tell in the book: the same figure going round a circle.
        # A real movement is priced on what moved, so repeats are rare; a
        # ring passing value hop to hop repeats by construction unless the
        # amounts are blended, which is what ``amount_blend`` does.
        repeats = (
            moves.with_columns(pl.col("amount").round(2).alias("rounded"))
            .group_by([SOURCE, "rounded"])
            .len()
            .group_by(SOURCE)
            .agg(
                ((pl.col("len") - 1).sum() / pl.col("len").sum().clip(1)).alias(
                    "repeat_amount_share"
                )
            )
            .rename({SOURCE: "supplier_id"})
        )
        frame = frame.join(repeats, on="supplier_id", how="left")
        out_partners = moves.group_by(SOURCE).agg(
            pl.col(TARGET).n_unique().cast(pl.Float64).alias("intercompany_partners")
        ).rename({SOURCE: "supplier_id"})
        pairs = set(zip(moves[SOURCE].to_list(), moves[TARGET].to_list(), strict=False))
        back = {s for s, t in pairs if (t, s) in pairs}
        frame = frame.join(out_partners, on="supplier_id", how="left").with_columns(
            pl.col("supplier_id").is_in(list(back)).cast(pl.Float64).alias("reciprocity")
        )

    supplies = run.tables.edges.get("SUPPLIES")
    if supplies is not None and supplies.height:
        partners = supplies.group_by(SOURCE).agg(
            pl.col(TARGET).n_unique().cast(pl.Float64).alias("distinct_partners")
        ).rename({SOURCE: "supplier_id"})
        frame = frame.join(partners, on="supplier_id", how="left")

    if "onboarded_at" in suppliers.columns:
        first = suppliers.select([pl.col(ID).alias("supplier_id"), "onboarded_at"])
        period_start = run.manifest.extra.get("supply_chain", {}).get("period_start")
        start = pl.lit(period_start).str.to_date() if isinstance(period_start, str) else pl.lit(None)
        frame = frame.join(first, on="supplier_id", how="left").with_columns(
            (start - pl.col("onboarded_at")).dt.total_days().cast(pl.Float64).alias("onboarded_days")
        ).drop("onboarded_at")

    # What share of a supplier's activity is one kind of thing. A ring
    # member stripped of its ordinary trading does nothing but move stock in
    # circles, and that is visible here even though every count is small.
    frame = frame.with_columns(
        (
            pl.col("invoice_count").fill_null(0)
            + pl.col("order_count").fill_null(0)
            + pl.col("shipment_count").fill_null(0)
            + pl.col("intercompany_count").fill_null(0)
        ).alias("event_count")
    ).with_columns(
        (pl.col("intercompany_count").fill_null(0) / pl.col("event_count").clip(1.0)).alias(
            "intercompany_only"
        ),
        (pl.col("invoice_count").fill_null(0) / pl.col("event_count").clip(1.0)).alias(
            "invoice_only"
        ),
    )

    known = [name for names in FEATURE_FAMILIES.values() for name in names]
    missing = [name for name in known if name not in frame.columns]
    frame = frame.with_columns([pl.lit(None, dtype=pl.Float64).alias(name) for name in missing])
    return frame.select(["supplier_id", *known]).fill_null(0.0)


@dataclass
class PlayHardness:
    play: str
    is_fraud: bool
    n_suppliers: int
    feature_auc: dict[str, float] = field(default_factory=dict)

    @property
    def max_auc(self) -> float:
        values = [v for v in self.feature_auc.values() if not np.isnan(v)]
        return max(values) if values else float("nan")

    @property
    def best_feature(self) -> str | None:
        ranked = [(v, k) for k, v in self.feature_auc.items() if not np.isnan(v)]
        return max(ranked)[1] if ranked else None

    @property
    def best_family(self) -> str | None:
        best = self.best_feature
        if best is None:
            return None
        return next((family for family, names in FEATURE_FAMILIES.items() if best in names), None)


@dataclass
class HardnessReport:
    hardness: str
    plays: list[PlayHardness]
    #: decoy -> (AUC, feature) separating it from the play it imitates. Low
    #: is good: no single feature tells the legitimate structure from the
    #: fraudulent one, so a detector has to combine evidence.
    decoy_separability: dict[str, tuple[float, str]] = field(default_factory=dict)

    @property
    def max_auc(self) -> float:
        values = [p.max_auc for p in self.plays if p.is_fraud and not np.isnan(p.max_auc)]
        return max(values) if values else float("nan")

    def as_dict(self) -> dict[str, Any]:
        return {
            "hardness": self.hardness,
            "max_auc": None if np.isnan(self.max_auc) else round(self.max_auc, 4),
            "plays": [
                {
                    "play": p.play,
                    "is_fraud": p.is_fraud,
                    "n_suppliers": p.n_suppliers,
                    "max_auc": None if np.isnan(p.max_auc) else round(p.max_auc, 4),
                    "best_feature": p.best_feature,
                    "best_family": p.best_family,
                    "feature_auc": {
                        k: None if np.isnan(v) else round(v, 4) for k, v in p.feature_auc.items()
                    },
                }
                for p in self.plays
            ],
            "decoy_separability": {
                k: {
                    "auc": None if np.isnan(v) else round(v, 4),
                    "feature": feature,
                    "twin": DECOY_TWIN.get(k),
                }
                for k, (v, feature) in self.decoy_separability.items()
            },
        }

    def summary(self) -> str:
        lines = [
            f"hardness: {self.hardness}",
            f"{'play':<24}{'kind':<14}{'n':>5}{'max AUC':>10}  best feature (family)",
        ]
        for p in sorted(self.plays, key=lambda p: -(0 if np.isnan(p.max_auc) else p.max_auc)):
            kind = "fraud" if p.is_fraud else "legitimate"
            value = "n/a" if np.isnan(p.max_auc) else f"{p.max_auc:.3f}"
            lines.append(
                f"{p.play:<24}{kind:<14}{p.n_suppliers:>5}{value:>10}  "
                f"{p.best_feature or '-'} ({p.best_family or '-'})"
            )
        if self.decoy_separability:
            lines.append("")
            lines.append("decoy separability from its twin (lower is better):")
            for decoy, (value, feature) in sorted(self.decoy_separability.items()):
                twin = DECOY_TWIN.get(decoy, "?")
                text = "n/a" if np.isnan(value) else f"{value:.3f}"
                lines.append(f"  {decoy:<22} vs {twin:<22}{text:>7}  by {feature}")
        return "\n".join(lines)


def hardness_report(run: GraphRun, features: pl.DataFrame | None = None) -> HardnessReport:
    """Score every play's separability from suppliers in no pattern."""
    features = supplier_features(run) if features is None else features
    truth = run.truth.get("suppliers")
    hardness = str(run.manifest.extra.get("supply_chain", {}).get("hardness", "?"))
    if truth is None or truth.height == 0:
        return HardnessReport(hardness=hardness, plays=[])

    names = [name for names in FEATURE_FAMILIES.values() for name in names]
    values = {name: features[name].to_numpy().astype(np.float64) for name in names}
    index = {sid: i for i, sid in enumerate(features["supplier_id"].to_list())}
    involved = {index[s] for s in truth["supplier_id"].to_list() if s in index}
    # Score against suppliers that trade, not against the dormant tail.
    # Most of a supplier base has no activity in any given period, and
    # including it would make "quiet" the normal thing and flatter every
    # feature: a pattern member with six invoices looks unremarkable next to
    # nine hundred suppliers with none. A procurement team ranks the
    # suppliers it is actually buying from, and so does this.
    activity = sum(
        features[column].to_numpy().astype(np.float64)
        for column in ("invoice_count", "order_count", "shipment_count", "intercompany_count")
    )
    uninvolved = np.array(
        [i for i in range(features.height) if i not in involved and activity[i] > 0],
        dtype=np.int64,
    )
    if not len(uninvolved):  # a network too small to have a comparison group
        uninvolved = np.array([i for i in range(features.height) if i not in involved], dtype=np.int64)

    plays: list[PlayHardness] = []
    by_play: dict[tuple[str, bool], list[int]] = {}
    for play, is_fraud, supplier in zip(
        truth["play"].to_list(), truth["is_fraud"].to_list(), truth["supplier_id"].to_list(), strict=False
    ):
        if supplier in index:
            by_play.setdefault((play, bool(is_fraud)), []).append(index[supplier])

    for (play, is_fraud), members in by_play.items():
        rows = np.array(sorted(set(members)), dtype=np.int64)
        scores = {
            name: auc(values[name][rows], values[name][uninvolved]) for name in names
        }
        plays.append(PlayHardness(play=play, is_fraud=is_fraud, n_suppliers=len(rows), feature_auc=scores))

    separability: dict[str, tuple[float, str]] = {}
    for decoy, twin in DECOY_TWIN.items():
        legit = by_play.get((twin, False))
        fraud = by_play.get((twin, True))
        if not legit or not fraud:
            continue
        legit_rows = np.array(sorted(set(legit)), dtype=np.int64)
        fraud_rows = np.array(sorted(set(fraud)), dtype=np.int64)
        best = max(
            ((abs(auc(values[n][fraud_rows], values[n][legit_rows]) - 0.5) + 0.5, n) for n in names),
            default=(float("nan"), "-"),
        )
        separability[decoy] = (best[0], best[1])

    return HardnessReport(hardness=hardness, plays=plays, decoy_separability=separability)


def realism_report(run: GraphRun) -> dict[str, float]:
    """Headline properties of the legitimate network, for comparing runs."""
    edges = run.tables.edges
    out: dict[str, float] = {}
    orders = edges.get(ORDERS)
    if orders is not None and orders.height:
        out["scheduled_share"] = float(orders["scheduled"].mean())
        out["order_amount_p90"] = float(orders["amount"].quantile(0.9) or 0.0)
    ships, invoices = edges.get(SHIPS), edges.get(INVOICES)
    if ships is not None and orders is not None and orders.height:
        out["ship_to_order_ratio"] = float(ships.height / orders.height)
    if invoices is not None and ships is not None and ships.height:
        out["invoice_to_ship_ratio"] = float(invoices.height / ships.height)
    deliveries = edges.get(DELIVERS)
    if deliveries is not None and deliveries.height:
        counts = deliveries.group_by(TARGET).len()["len"].to_numpy()
        share = np.sort(counts)[::-1][: max(1, len(counts) // 100)].sum() / counts.sum()
        out["top1pct_customer_share"] = float(share)
    supplies = edges.get("SUPPLIES")
    if supplies is not None and supplies.height:
        per_plant = supplies.group_by(TARGET).len()["len"].to_numpy()
        out["suppliers_per_plant"] = float(per_plant.mean())
    return out
