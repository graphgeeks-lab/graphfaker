"""Scoring a detector against the truth, at three levels.

Suppliers, events and patterns, because the three answer different
questions: whether you suspected the right company, whether you picked out
the right transactions, and whether you reconstructed the scheme. A rule can
do well on one and badly on the others, and knowing which is the useful part.

``legitimate_false_positive_rate`` is reported on its own, because the cost
of accusing a supplier that is running a blanket agreement is not the mirror
image of missing one kiting ring. One is a procurement team's afternoon; the
other is a relationship with a company that did nothing wrong.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from graphfaker.engine.run import GraphRun

#: Share of a pattern's suppliers that must be flagged for it to count as found.
DEFAULT_PATTERN_THRESHOLD = 0.5


@dataclass
class Scores:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


@dataclass
class Evaluation:
    supplier: Scores = field(default_factory=Scores)
    event: Scores = field(default_factory=Scores)
    pattern: Scores = field(default_factory=Scores)
    #: Share of suppliers in a legitimate structure that were flagged.
    legitimate_false_positive_rate: float = 0.0
    #: Per-play recall, so a detector that only checks the approval threshold
    #: shows up as one: it will score near zero on everything but split
    #: orders.
    recall_by_play: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "supplier": self.supplier.as_dict(),
            "event": self.event.as_dict(),
            "pattern": self.pattern.as_dict(),
            "legitimate_false_positive_rate": round(self.legitimate_false_positive_rate, 4),
            "recall_by_play": {k: round(v, 4) for k, v in sorted(self.recall_by_play.items())},
        }

    def summary(self) -> str:
        lines = [f"{'level':<12}{'precision':>11}{'recall':>9}{'f1':>8}{'tp':>8}{'fp':>8}{'fn':>8}"]
        for name, scores in (
            ("supplier", self.supplier),
            ("event", self.event),
            ("pattern", self.pattern),
        ):
            lines.append(
                f"{name:<12}{scores.precision:>11.3f}{scores.recall:>9.3f}"
                f"{scores.f1:>8.3f}{scores.tp:>8}{scores.fp:>8}{scores.fn:>8}"
            )
        lines.append("")
        lines.append(
            f"legitimate suppliers flagged: {self.legitimate_false_positive_rate:.1%} "
            "(these did nothing wrong; flagging them is the deployment cost)"
        )
        if self.recall_by_play:
            lines.append("")
            lines.append("recall by play:")
            for play, value in sorted(self.recall_by_play.items(), key=lambda kv: -kv[1]):
                lines.append(f"  {play:<24}{value:>7.3f}")
        return "\n".join(lines)


def _flagged(values: Iterable[str] | None) -> set[str]:
    return set() if values is None else {str(v) for v in values}


def evaluate(
    run: GraphRun | str | Path,
    flagged_suppliers: Iterable[str] | None = None,
    flagged_events: Iterable[str] | None = None,
    pattern_threshold: float = DEFAULT_PATTERN_THRESHOLD,
) -> Evaluation:
    """Score flagged suppliers and events against what was planted.

    ``run`` is a :class:`GraphRun` or the directory one was written to, so a
    detector that works from the files can be scored without regenerating.
    """
    truth = _truth(run)
    suppliers = truth.get("suppliers")
    events = truth.get("events")
    patterns = truth.get("patterns")
    result = Evaluation()
    if suppliers is None or patterns is None:
        return result

    flagged_s, flagged_e = _flagged(flagged_suppliers), _flagged(flagged_events)

    guilty = set(suppliers.filter(pl.col("is_fraud"))["supplier_id"].to_list())
    innocent = set(suppliers.filter(~pl.col("is_fraud"))["supplier_id"].to_list()) - guilty
    result.supplier = Scores(
        tp=len(flagged_s & guilty),
        fp=len(flagged_s - guilty),
        fn=len(guilty - flagged_s),
    )
    if innocent:
        result.legitimate_false_positive_rate = len(flagged_s & innocent) / len(innocent)

    if events is not None and events.height:
        guilty_events = set(events.filter(pl.col("is_fraud"))["event_id"].to_list())
        result.event = Scores(
            tp=len(flagged_e & guilty_events),
            fp=len(flagged_e - guilty_events),
            fn=len(guilty_events - flagged_e),
        )

    # A pattern counts as found when enough of its suppliers were flagged,
    # because naming one member of a ring is not the same as finding it.
    members = suppliers.group_by("pattern_id").agg(
        pl.col("supplier_id").alias("members"), pl.col("is_fraud").first().alias("is_fraud")
    )
    found = 0
    missed = 0
    false_patterns = 0
    for row in members.iter_rows(named=True):
        share = len(set(row["members"]) & flagged_s) / max(1, len(row["members"]))
        if row["is_fraud"]:
            if share >= pattern_threshold:
                found += 1
            else:
                missed += 1
        elif share >= pattern_threshold:
            false_patterns += 1
    result.pattern = Scores(tp=found, fp=false_patterns, fn=missed)

    by_play = suppliers.filter(pl.col("is_fraud")).group_by("play").agg(
        pl.col("supplier_id").alias("members")
    )
    for row in by_play.iter_rows(named=True):
        unique = set(row["members"])
        result.recall_by_play[row["play"]] = len(unique & flagged_s) / max(1, len(unique))
    return result


def _truth(run: GraphRun | str | Path) -> dict[str, pl.DataFrame]:
    if isinstance(run, GraphRun):
        return run.truth
    root = Path(run) / "truth"
    if not root.is_dir():
        raise FileNotFoundError(f"no truth/ directory under {run}")
    return {path.stem: pl.read_parquet(path) for path in root.glob("*.parquet")}


def read_flagged(path: str | Path, column: str | None = None) -> list[str]:
    """Ids from a text file (one per line) or a Parquet/CSV column."""
    path = Path(path)
    if path.suffix in (".parquet", ".csv"):
        frame = pl.read_parquet(path) if path.suffix == ".parquet" else pl.read_csv(path)
        name = column or frame.columns[0]
        return [str(v) for v in frame[name].to_list()]
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
