"""Score a detector's output against the ground truth.

Three granularities, mirroring the fraud pack's evaluator so the two are read
the same way:

* **account**: did the detector flag the accounts taking part in a campaign?
* **event**: did it flag the posts, reshares and replies a campaign created?
* **campaign**: is a whole campaign considered found? A campaign counts as
  detected when at least ``cluster_threshold`` of its accounts are flagged.

**Organic structures are legitimate: flagging their accounts is a false
positive.** That is what they are there to measure, and it is the difference
between this harness and a synchrony detector's own scorecard. A detector that
fires on every burst gets recall 1.0 and precision near the coordinated share,
and this is where that shows up.

``organic_false_positive_rate`` is reported separately from overall precision,
because it is the number that decides whether a detector is deployable: on a
real platform the cost of suspending a fan club is not symmetric with the cost
of missing one amplification ring.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from graphfaker.engine.run import GraphRun

#: Share of a campaign's accounts that must be flagged for it to count as found.
DEFAULT_CLUSTER_THRESHOLD = 0.5


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
    account: Scores = field(default_factory=Scores)
    event: Scores = field(default_factory=Scores)
    campaign: Scores = field(default_factory=Scores)
    #: Share of organic (``is_coordinated=False``) accounts that were flagged.
    organic_false_positive_rate: float = 0.0
    #: Per-playbook recall, so a detector that only finds bursts is visible as
    #: one: it will score near zero on ``follow_farm``.
    recall_by_playbook: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "account": self.account.as_dict(),
            "event": self.event.as_dict(),
            "campaign": self.campaign.as_dict(),
            "organic_false_positive_rate": round(self.organic_false_positive_rate, 4),
            "recall_by_playbook": {
                k: round(v, 4) for k, v in sorted(self.recall_by_playbook.items())
            },
        }

    def summary(self) -> str:
        lines = [f"{'level':<12}{'precision':>11}{'recall':>9}{'f1':>8}{'tp':>8}{'fp':>8}{'fn':>8}"]
        for name, scores in (
            ("account", self.account),
            ("event", self.event),
            ("campaign", self.campaign),
        ):
            lines.append(
                f"{name:<12}{scores.precision:>11.3f}{scores.recall:>9.3f}"
                f"{scores.f1:>8.3f}{scores.tp:>8}{scores.fp:>8}{scores.fn:>8}"
            )
        lines.append("")
        lines.append(
            f"organic accounts flagged: {self.organic_false_positive_rate:.1%} "
            "(these are legitimate; flagging them is the deployment cost)"
        )
        if self.recall_by_playbook:
            lines.append("")
            lines.append("recall by playbook:")
            for playbook, value in sorted(
                self.recall_by_playbook.items(), key=lambda kv: -kv[1]
            ):
                lines.append(f"  {playbook:<24}{value:>7.3f}")
        return "\n".join(lines)


def _flagged(values: Iterable[str] | None) -> set[str]:
    return set() if values is None else {str(v) for v in values}


def evaluate(
    run: GraphRun,
    flagged_accounts: Iterable[str] | None = None,
    flagged_events: Iterable[str] | None = None,
    cluster_threshold: float = DEFAULT_CLUSTER_THRESHOLD,
) -> Evaluation:
    """Score flagged accounts and events against the run's truth.

    Args:
        run: The generated run, with ``truth`` present. A ``--blind`` dataset
            has no truth and cannot be scored; score against the run that wrote
            it, or a truth-loaded copy.
        flagged_accounts: Account ids the detector considers coordinated.
        flagged_events: Event ids the detector considers part of a campaign.
        cluster_threshold: Share of a campaign's accounts that must be flagged
            for the campaign to count as found.
    """
    accounts_truth = run.truth.get("accounts")
    if accounts_truth is None:
        raise ValueError(
            "this run has no ground truth; evaluate against the run that generated "
            "the dataset, or a copy loaded without --blind"
        )

    flagged_acc = _flagged(flagged_accounts)
    flagged_ev = _flagged(flagged_events)
    evaluation = Evaluation()

    # ------------------------------------------------------------- accounts
    # An account in any coordinated campaign is positive. An account only in
    # organic structures is negative, and deliberately so.
    coordinated: set[str] = set()
    organic: set[str] = set()
    for account, is_coordinated in zip(
        accounts_truth["account_id"].to_list(), accounts_truth["is_coordinated"].to_list()
    ):
        (coordinated if is_coordinated else organic).add(str(account))
    organic -= coordinated

    all_accounts = {str(a) for a in run.tables.nodes["Account"][run.tables.nodes["Account"].columns[0]].to_list()}
    negatives = all_accounts - coordinated

    evaluation.account.tp = len(flagged_acc & coordinated)
    evaluation.account.fp = len(flagged_acc & negatives)
    evaluation.account.fn = len(coordinated - flagged_acc)
    evaluation.account.tn = len(negatives - flagged_acc)

    if organic:
        evaluation.organic_false_positive_rate = len(flagged_acc & organic) / len(organic)

    # --------------------------------------------------------------- events
    events_truth = run.truth.get("events")
    if events_truth is not None and events_truth.height:
        coordinated_events = {
            str(e)
            for e, flag in zip(
                events_truth["event_id"].to_list(), events_truth["is_coordinated"].to_list()
            )
            if flag
        }
        total_events = sum(
            frame.height for name, frame in run.tables.edges.items() if "event_id" in frame.columns
        )
        evaluation.event.tp = len(flagged_ev & coordinated_events)
        evaluation.event.fp = len(flagged_ev - coordinated_events)
        evaluation.event.fn = len(coordinated_events - flagged_ev)
        evaluation.event.tn = max(
            0, total_events - len(coordinated_events) - evaluation.event.fp
        )

    # ------------------------------------------------------------ campaigns
    campaigns_truth = run.truth.get("campaigns")
    if campaigns_truth is not None and campaigns_truth.height:
        found_by_playbook: dict[str, list[bool]] = {}
        for row in campaigns_truth.iter_rows(named=True):
            members = [str(a) for a in (row.get("accounts") or [])]
            if not members:
                continue
            share = len(flagged_acc & set(members)) / len(members)
            found = share >= cluster_threshold
            if row["is_coordinated"]:
                found_by_playbook.setdefault(row["playbook"], []).append(found)
                if found:
                    evaluation.campaign.tp += 1
                else:
                    evaluation.campaign.fn += 1
            elif found:
                # An organic structure the detector decided was a campaign.
                evaluation.campaign.fp += 1
            else:
                evaluation.campaign.tn += 1
        evaluation.recall_by_playbook = {
            playbook: sum(results) / len(results)
            for playbook, results in found_by_playbook.items()
            if results
        }

    return evaluation


def read_flagged(path: str | Path, column: str) -> list[str]:
    """Read a detector's output from CSV or Parquet.

    Accepts either a single-column file or one with a named column, so a
    detector can hand over whatever it already writes.
    """
    target = Path(path)
    frame = (
        pl.read_parquet(target)
        if target.suffix in {".parquet", ".pq"}
        else pl.read_csv(target)
    )
    if column in frame.columns:
        return [str(v) for v in frame[column].to_list()]
    if frame.width == 1:
        return [str(v) for v in frame[frame.columns[0]].to_list()]
    raise ValueError(
        f"{target} has no column {column!r}; found {frame.columns}"
    )
