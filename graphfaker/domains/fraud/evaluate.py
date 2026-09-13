"""Score a detector's output against the ground truth.

Three granularities, following gen-fraud-graph's evaluator so results are
comparable, plus transactions:

* **account** — did the detector flag the accounts that take part in fraud?
* **transaction** — did it flag the injected transactions?
* **pattern** — is a whole pattern considered found? A pattern counts as
  detected when at least ``ring_threshold`` of its accounts are flagged.

Decoys are legitimate: flagging their accounts counts as a false positive,
which is exactly what they are there to measure.
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

    def as_dict(self) -> dict[str, float]:
        return {"precision": self.precision, "recall": self.recall, "f1": self.f1, "tp": self.tp, "fp": self.fp, "fn": self.fn}


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

    def summary(self) -> str:
        lines = [f"{'level':<14}{'precision':>10}{'recall':>8}{'f1':>7}{'tp':>7}{'fp':>7}{'fn':>7}"]
        for name, s in (("account", self.account), ("transaction", self.transaction), ("pattern", self.pattern)):
            lines.append(f"{name:<14}{s.precision:>10.3f}{s.recall:>8.3f}{s.f1:>7.3f}{s.tp:>7}{s.fp:>7}{s.fn:>7}")
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
