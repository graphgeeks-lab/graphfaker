"""Score a detector's output against the ground truth.

Three granularities, following gen-fraud-graph's evaluator so results are
comparable, plus transactions:

* **account**: did the detector flag the accounts that take part in fraud?
* **transaction**: did it flag the injected transactions?
* **pattern**: is a whole pattern considered found? A pattern counts as
  detected when at least ``ring_threshold`` of its accounts are flagged.

Decoys are legitimate: flagging their accounts counts as a false positive.
That is what they are there to measure.

**Priced scores.** Precision, recall and F1 (and AUC) treat a missed fraud and a false alert as
equally bad, or average over thresholds nobody would use. A detector can rank well and still be
worth nothing at the threshold a team can afford. ``Scores.cost(miss_cost)`` prices the errors:
``miss_cost x missed + false alerts``, where ``miss_cost`` is how many false alerts one missed
fraud is worth. That weight is a policy, so it has no default. ``value`` compares the cost with
raising no alerts at all, which costs ``miss_cost x`` every fraud.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from graphfaker.engine.run import GraphRun


@dataclass
class Scores:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def cost(self, miss_cost: float) -> float:
        """Missed fraud priced at ``miss_cost`` false alerts each, plus the false alerts."""
        return miss_cost * self.fn + self.fp

    def no_alert_cost(self, miss_cost: float) -> float:
        """What raising no alerts at all would cost: every fraud missed, no false alerts."""
        return miss_cost * (self.tp + self.fn)

    def value(self, miss_cost: float) -> float:
        """Cost saved against raising no alerts. Zero or less means the detector isn't worth running."""
        return self.no_alert_cost(miss_cost) - self.cost(miss_cost)

    def as_dict(self, miss_cost: float | None = None) -> dict[str, float]:
        out = {"precision": self.precision, "recall": self.recall, "f1": self.f1, "tp": self.tp, "fp": self.fp, "fn": self.fn}
        if miss_cost is not None:
            out |= {"miss_cost": miss_cost, "cost": self.cost(miss_cost),
                    "no_alert_cost": self.no_alert_cost(miss_cost), "value": self.value(miss_cost)}
        return out


@dataclass
class Evaluation:
    account: Scores
    transaction: Scores
    pattern: Scores
    per_typology: dict[str, Scores] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "account": self.account.as_dict(),
            "transaction": self.transaction.as_dict(),
            "pattern": self.pattern.as_dict(),
            "per_typology": {k: v.as_dict() for k, v in self.per_typology.items()},
        }

    def summary(self, miss_cost: float | None = None) -> str:
        priced = miss_cost is not None
        head = f"{'level':<14}{'precision':>10}{'recall':>8}{'f1':>7}{'tp':>7}{'fp':>7}{'fn':>7}"
        lines = [head + (f"{'cost':>10}{'no alerts':>11}{'value':>9}" if priced else "")]
        for name, s in (("account", self.account), ("transaction", self.transaction), ("pattern", self.pattern)):
            row = f"{name:<14}{s.precision:>10.3f}{s.recall:>8.3f}{s.f1:>7.3f}{s.tp:>7}{s.fp:>7}{s.fn:>7}"
            if priced:
                row += f"{s.cost(miss_cost):>10.0f}{s.no_alert_cost(miss_cost):>11.0f}{s.value(miss_cost):>9.0f}"
            lines.append(row)
        if priced:
            lines.append(f"(a missed fraud costs {miss_cost:g} false alerts; value <= 0 means no better than no alerts)")
        if self.per_typology:
            lines.append("")
            lines.append(f"{'pattern recall by typology':<30}")
            for typology, s in sorted(self.per_typology.items()):
                lines.append(f"  {typology:<20}{s.recall:>8.3f}  ({s.tp}/{s.tp + s.fn})")
        return "\n".join(lines)


def load_truth(source: GraphRun | str | Path) -> dict[str, pl.DataFrame]:
    if isinstance(source, GraphRun):
        return source.truth
    root = Path(source)
    root = root / "truth" if (root / "truth").is_dir() else root
    return {p.stem: pl.read_parquet(p) for p in root.glob("*.parquet")}


def evaluate(
    truth: GraphRun | str | Path | dict[str, pl.DataFrame],
    flagged_accounts: Iterable[str] = (),
    flagged_transactions: Iterable[str] = (),
    ring_threshold: float = 1.0,
) -> Evaluation:
    tables = truth if isinstance(truth, dict) else load_truth(truth)
    accounts = tables["accounts"].filter(pl.col("is_fraud"))
    transactions = tables["transactions"].filter(pl.col("is_fraud"))
    patterns = tables["patterns"].filter(pl.col("is_fraud"))

    flagged_acc = set(flagged_accounts)
    flagged_tx = set(flagged_transactions)

    fraud_acc = set(accounts["account_id"].to_list())
    account = Scores(
        tp=len(flagged_acc & fraud_acc),
        fp=len(flagged_acc - fraud_acc),
        fn=len(fraud_acc - flagged_acc),
    )
    fraud_tx = set(transactions["tx_id"].to_list())
    transaction = Scores(
        tp=len(flagged_tx & fraud_tx),
        fp=len(flagged_tx - fraud_tx),
        fn=len(fraud_tx - flagged_tx),
    )

    pattern = Scores()
    per_typology: dict[str, Scores] = {}
    for row in patterns.iter_rows(named=True):
        members = set(row["accounts"])
        found = members and len(members & flagged_acc) / len(members) >= ring_threshold
        scores = per_typology.setdefault(row["typology"], Scores())
        if found:
            pattern.tp += 1
            scores.tp += 1
        else:
            pattern.fn += 1
            scores.fn += 1
    return Evaluation(account=account, transaction=transaction, pattern=pattern, per_typology=per_typology)
