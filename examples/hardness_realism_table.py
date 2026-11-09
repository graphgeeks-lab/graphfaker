"""Hardness and realism reports for the fraud bank at each hardness level: the generator paper's table.

    python examples/hardness_realism_table.py --scale 0.01 --seed 1 --out hardness_realism.json

``--patterns-per-20k`` scales the fraud with the bank exactly as ``gnn_hardness_study.py`` does
(that many patterns per typology for every 20,000 accounts), so the report describes the same
banks the detectors were trained on. Without it, the generator's default fraud count applies.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from graphfaker.domains import fraud
from graphfaker.domains.fraud.config import TYPOLOGIES
from graphfaker.domains.fraud.hardness import hardness_report, realism_report


def patterns_for(scale: float, per_20k: float | None) -> dict[str, int] | None:
    """Patterns per typology for ``per_20k`` patterns per 20,000 accounts, or None for the default."""
    if not per_20k:
        return None
    accounts = fraud.FraudConfig(scale=scale).num_accounts
    n = max(1, round(per_20k * accounts / 20_000))
    return {t: n for t in TYPOLOGIES}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scale", type=float, default=0.01)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--patterns-per-20k", type=float, default=None,
                    help="Scale the fraud with the bank, as gnn_hardness_study.py does.")
    ap.add_argument("--out", type=Path, default=Path("hardness_realism.json"))
    args = ap.parse_args()
    if args.patterns_per_20k is not None and args.patterns_per_20k <= 0:
        ap.error("--patterns-per-20k is a count of patterns per typology, so it is positive")
    patterns = patterns_for(args.scale, args.patterns_per_20k)
    kwargs = {"patterns": patterns} if patterns else {}
    out = {"patterns_per_20k": args.patterns_per_20k}
    for hardness in ("low", "medium", "high"):
        run = fraud.generate(scale=args.scale, seed=args.seed, hardness=hardness, **kwargs)
        h = hardness_report(run)
        out[hardness] = {"max_single_feature_auc": h.max_auc, "hardness": h.as_dict(),
                         "realism": realism_report(run)}
        print(hardness, round(h.max_auc, 3), flush=True)
    args.out.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
