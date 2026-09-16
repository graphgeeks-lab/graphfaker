"""Run ``bench_scale.py`` across interpreters and scales, print the table.

Each version lives in its own virtualenv, so nothing has to import two
versions of GraphFaker at once. Build the baseline once::

    uv venv --python 3.12 /tmp/gf050
    uv pip install --python /tmp/gf050/bin/python graphfaker==0.5.0 psutil

Then compare it against the working tree::

    python benchmarks/compare.py \\
        --version 0.5.0=/tmp/gf050/bin/python \\
        --version 0.6.0=.venv/bin/python \\
        --scale 0.01 --scale 0.1

Runs are sequential and each is a fresh process, so one version's memory
never overlaps another's. Results are written to ``--json`` as they finish,
so a run that dies at a larger scale still leaves the smaller rows behind.
A row that fails (out of memory, most likely) is recorded as a failure
rather than dropped, because "did not fit" is a result.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run_one(python: str, label: str, scale: float, workers: int, seed: int, extra: list[str]) -> dict:
    command = [
        python, str(HERE / "bench_scale.py"),
        "--scale", str(scale), "--workers", str(workers), "--seed", str(seed),
        "--label", label, *extra,
    ]
    print(f"  {label:8} scale={scale:<6} workers={workers} ... ", end="", flush=True)
    proc = subprocess.run(command, capture_output=True, text=True)
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("{")), None)
    if line is None:
        print("no result")
        return {"label": label, "scale": scale, "workers": workers, "ok": False,
                "error": (proc.stderr or proc.stdout or "no output")[-400:]}
    row = json.loads(line)
    if row.get("ok"):
        print(f"generate {row['generate_s']}s  write {row['write_s']}s  peak {row['peak_memory_gb']} GB")
    else:
        print(f"FAILED: {row.get('error', '')[:120]}")
    return row


def table(rows: list[dict]) -> str:
    """One line per (scale, version), with the speed-up where both ran."""
    by_scale: dict[float, dict[str, dict]] = {}
    for row in rows:
        by_scale.setdefault(row["scale"], {})[row["label"]] = row
    labels = list(dict.fromkeys(r["label"] for r in rows))

    head = f"{'scale':>7} {'nodes':>12} {'edges':>12} {'version':>9} {'workers':>8} {'generate':>10} {'write':>8} {'peak GB':>9}"
    out = [head, "-" * len(head)]
    for scale in sorted(by_scale):
        group = by_scale[scale]
        for label in labels:
            row = group.get(label)
            if row is None:
                continue
            if not row.get("ok"):
                out.append(f"{scale:>7} {'':>12} {'':>12} {label:>9} {row.get('workers', ''):>8}   {row.get('error','failed')[:40]}")
                continue
            out.append(
                f"{scale:>7} {row['nodes']:>12,} {row['edges']:>12,} {label:>9} {row['workers']:>8} "
                f"{row['generate_s']:>9}s {row['write_s']:>7}s {row['peak_memory_gb']:>9}"
            )
        ok = {k: v for k, v in group.items() if v.get("ok")}
        if len(ok) == 2:
            first, second = [ok[label] for label in labels if label in ok]
            if second["generate_s"] > 0:
                speedup = first["generate_s"] / second["generate_s"]
                total_first = first["generate_s"] + (first["write_s"] or 0)
                total_second = second["generate_s"] + (second["write_s"] or 0)
                out.append(
                    f"{'':>7} {'':>12} {'':>12} {'speed-up':>9} {'':>8} "
                    f"{speedup:>8.1f}x {'':>8} end to end {total_first / total_second:.1f}x"
                )
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="append", required=True, metavar="LABEL=PYTHON",
                        help="Repeatable. e.g. 0.5.0=/tmp/gf050/bin/python")
    parser.add_argument("--scale", action="append", type=float, required=True, help="Repeatable.")
    parser.add_argument("--workers", action="append", type=int, default=None,
                        help="Repeatable, paired with --scale in order. Defaults to 1 for all.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--json", default="benchmarks/results.json")
    parser.add_argument("--only", default=None, help="Run only this label (e.g. to redo one row).")
    args, extra = parser.parse_known_args()

    versions = []
    for spec in args.version:
        if "=" not in spec:
            parser.error(f"--version wants LABEL=PYTHON, got {spec!r}")
        label, python = spec.split("=", 1)
        if args.only and label != args.only:
            continue
        versions.append((label, python))

    workers = args.workers or [1] * len(args.scale)
    if len(workers) != len(args.scale):
        parser.error("--workers must be given once per --scale, or not at all")

    results_path = Path(args.json)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for scale, n in zip(args.scale, workers):
        print(f"scale {scale}:")
        for label, python in versions:
            rows.append(run_one(python, label, scale, n, args.seed, extra))
            results_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    print()
    print(table(rows))
    print(f"\nwrote {results_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
