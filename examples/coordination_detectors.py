"""Score simple coordination detectors against the ground truth.

Regenerates the table in ``docs/domains/coordination.md``. Every rule here is
one a platform would actually try first, and each is scored at three tradecraft
levels so the reader can see where it stops working.

The conclusion is the same one the fraud pack reached with Cypher rules:
single-signal detectors do badly, and the signal they do have collapses as the
operation gets more careful. The difference here is the organic decoys, which
put a number on what the detector costs when it is wrong.

    python examples/coordination_detectors.py
"""

from __future__ import annotations

import polars as pl

from graphfaker.domains.coordination import (
    account_features,
    evaluate,
    generate,
    hardness_report,
)
from graphfaker.domains.coordination.process import POSTED

SCALE = 0.0006
SEED = 7


def co_occurrence(run, features, *, window: str = "1h", min_accounts: int = 6) -> list[str]:
    """Flag accounts that post about one topic in the same hour as many others.

    The standard first pass. It is looking for synchrony, which is exactly what
    a fan club and a news event also produce.
    """
    posted = run.tables.edges[POSTED]
    if posted.height == 0:
        return []
    buckets = (
        posted.with_columns(pl.col("timestamp").dt.truncate(window).alias("bucket"))
        .group_by(["target", "bucket"])
        .agg(pl.col("source").unique().alias("accounts"))
        .filter(pl.col("accounts").list.len() >= min_accounts)
    )
    return sorted({str(a) for row in buckets["accounts"].to_list() for a in row})


def duplicate_text(run, features, *, threshold: float = 0.9) -> list[str]:
    """Flag accounts whose posts mostly reuse a template another account used."""
    return features.filter(pl.col("template_reuse") >= threshold)["account_id"].to_list()


def fresh_and_active(run, features, *, max_age: int = 30) -> list[str]:
    """Flag young accounts that post a lot. The oldest heuristic there is."""
    busy = features["event_count"].quantile(0.8) or 0
    return features.filter(
        (pl.col("account_age_days") <= max_age) & (pl.col("event_count") > busy)
    )["account_id"].to_list()


def dense_reciprocal(run, features, *, threshold: float = 0.9) -> list[str]:
    """Flag accounts whose follows are almost all mutual: the follow-farm rule."""
    return features.filter(
        (pl.col("reciprocity") >= threshold) & (pl.col("following") >= 5)
    )["account_id"].to_list()


def shared_device(run, features, *, min_sharers: int = 4) -> list[str]:
    """Flag accounts sharing a device with several others."""
    return features.filter(pl.col("device_shared_with") >= min_sharers)[
        "account_id"
    ].to_list()


DETECTORS = {
    "co-occurrence (1h)": co_occurrence,
    "duplicate text": duplicate_text,
    "fresh + active": fresh_and_active,
    "dense reciprocity": dense_reciprocal,
    "shared device": shared_device,
}


def main() -> None:
    header = (
        f"{'detector':<22}{'tradecraft':<12}{'precision':>10}{'recall':>8}"
        f"{'F1':>7}{'organic FP':>12}"
    )
    print(header)
    print("-" * len(header))
    for name, detector in DETECTORS.items():
        for level in ("low", "medium", "high"):
            run = generate(scale=SCALE, tradecraft=level, seed=SEED)
            features = account_features(run)
            flagged = detector(run, features)
            scores = evaluate(run, flagged_accounts=flagged)
            print(
                f"{name:<22}{level:<12}{scores.account.precision:>10.3f}"
                f"{scores.account.recall:>8.3f}{scores.account.f1:>7.3f}"
                f"{scores.organic_false_positive_rate:>11.1%}"
            )
        print()

    print("Hardness at each level (mean of per-playbook max single-feature AUC):")
    for level in ("low", "medium", "high"):
        report = hardness_report(generate(scale=SCALE, tradecraft=level, seed=SEED))
        coordinated = [p for p in report.playbooks if p.is_coordinated]
        mean_auc = sum(p.max_auc for p in coordinated) / len(coordinated)
        families = sorted({p.best_family for p in coordinated if p.best_family})
        print(f"  {level:<8}{mean_auc:.3f}   evidence families: {', '.join(families)}")


if __name__ == "__main__":
    main()
