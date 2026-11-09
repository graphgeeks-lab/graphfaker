"""Hardness and realism reports for the fraud bank at each hardness level: the generator paper's table.

    python examples/hardness_realism_table.py --scale 0.01 --seed 1 --out hardness_realism.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from graphfaker.domains import fraud
from graphfaker.domains.fraud.hardness import hardness_report, realism_report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scale", type=float, default=0.01)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, default=Path("hardness_realism.json"))
    args = ap.parse_args()
    out = {}
    for hardness in ("low", "medium", "high"):
        run = fraud.generate(scale=args.scale, seed=args.seed, hardness=hardness)
        h = hardness_report(run)
        out[hardness] = {"max_single_feature_auc": h.max_auc, "hardness": h.as_dict(),
                         "realism": realism_report(run)}
        print(hardness, round(h.max_auc, 3), flush=True)
    args.out.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
