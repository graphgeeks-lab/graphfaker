"""Measure how generation time grows with scale, and fit the growth.

A single speed-up number ("0.6.0 is 13x faster") is a measurement at one
size, and it moves with the size: the 0.5.0 code had two costs that grew
faster than the data, so the ratio widens as the dataset grows. What is
worth stating instead is the *exponent*: fit

    generate_seconds = a * scale ** b

and read off ``b``. Linear growth is ``b = 1``, which is the best a
generator can do when the output is proportional to the scale. ``b > 1``
means a superlinear cost, and the gap between the two versions' exponents
is the part that will keep growing on datasets nobody has run yet.

    python benchmarks/scaling.py \\
        --version 0.5.0=/tmp/gf050/bin/python \\
        --version 0.6.0=.venv/bin/python \\
        --scale 0.005 --scale 0.01 --scale 0.02 --scale 0.05 --scale 0.1

Each point is run ``--repeats`` times and the minimum is kept, which is the
usual choice for wall-clock benchmarks: noise only ever adds time, so the
fastest run is the closest to the machine's actual capability. Repeats are
skipped once a single run passes ``--max-repeat-seconds``, so adding an
expensive scale does not multiply the total by the repeat count.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run_one(python: str, label: str, scale: float, workers: int, seed: int) -> dict | None:
    command = [
        python, str(HERE / "bench_scale.py"),
        "--scale", str(scale), "--workers", str(workers), "--seed", str(seed),
        "--label", label, "--no-write",
    ]
    proc = subprocess.run(command, capture_output=True, text=True)
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("{")), None)
    if line is None:
        return {"ok": False, "error": (proc.stderr or "no output")[-300:]}
    return json.loads(line)


def fit_power_law(scales: list[float], seconds: list[float]) -> tuple[float, float, float]:
    """Least squares on log-log. Returns (exponent, prefactor, r_squared)."""
    n = len(scales)
    xs = [math.log(s) for s in scales]
    ys = [math.log(t) for t in seconds]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    predicted = [intercept + slope * x for x in xs]
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, predicted))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1.0 if ss_tot == 0 else 1 - ss_res / ss_tot
    return slope, math.exp(intercept), r2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="append", required=True, metavar="LABEL=PYTHON")
    parser.add_argument("--scale", action="append", type=float, required=True)
    parser.add_argument("--workers", type=int, default=1, help="Held constant across the curve.")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-repeat-seconds", type=float, default=45.0,
                        help="Stop repeating a point once one run takes longer than this.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--json", default="benchmarks/scaling.json")
    args = parser.parse_args()

    versions = []
    for spec in args.version:
        if "=" not in spec:
            parser.error(f"--version wants LABEL=PYTHON, got {spec!r}")
        versions.append(tuple(spec.split("=", 1)))

    scales = sorted(args.scale)
    results: dict[str, dict[float, dict]] = {label: {} for label, _ in versions}
    raw: list[dict] = []
    out_path = Path(args.json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started_all = time.perf_counter()

    for label, python in versions:
        print(f"\n{label}")
        for scale in scales:
            best: dict | None = None
            for attempt in range(args.repeats):
                row = run_one(python, label, scale, args.workers, args.seed)
                raw.append({"attempt": attempt, **(row or {})})
                out_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
                if not row or not row.get("ok"):
                    print(f"  scale {scale:<7} FAILED: {(row or {}).get('error', '')[:90]}")
                    best = None
                    break
                if best is None or row["generate_s"] < best["generate_s"]:
                    best = row
                if row["generate_s"] > args.max_repeat_seconds:
                    break
            if best is None:
                continue
            results[label][scale] = best
            runs = sum(1 for r in raw if r.get("ok") and r["label"] == label and r["scale"] == scale)
            print(f"  scale {scale:<7} {best['generate_s']:>8.1f}s  "
                  f"peak {best['peak_memory_gb']:>5.2f} GB  ({runs} run{'s' if runs > 1 else ''}, min kept)")

    # ------------------------------------------------------------------ report
    labels = [label for label, _ in versions]
    print(f"\n{'':>8}", end="")
    for label in labels:
        print(f"{label:>14}", end="")
    print(f"{'speed-up':>11}")
    common = [s for s in scales if all(s in results[label] for label in labels)]
    for scale in scales:
        print(f"{scale:>8}", end="")
        for label in labels:
            row = results[label].get(scale)
            cell = f"{row['generate_s']:.1f}s" if row else "-"
            print(f"{cell:>14}", end="")
        if scale in common and len(labels) == 2:
            a = results[labels[0]][scale]["generate_s"]
            b = results[labels[1]][scale]["generate_s"]
            print(f"{b and a / b:>10.1f}x")
        else:
            print(f"{'-':>11}")

    # A single fit over the whole range can hide a knee: a version that is
    # linear when small and superlinear when large fits a middling exponent
    # with a good r^2 and structured residuals. The slope between adjacent
    # points shows the shape instead of averaging it away.
    print("\nlocal growth rate, log-log slope between adjacent points:")
    print(f"{'range':>16}", end="")
    for label in labels:
        print(f"{label:>10}", end="")
    print()
    for i in range(len(common) - 1):
        lo, hi = common[i], common[i + 1]
        print(f"{f'{lo} -> {hi}':>16}", end="")
        for label in labels:
            a = results[label][lo]["generate_s"]
            b = results[label][hi]["generate_s"]
            print(f"{math.log(b / a) / math.log(hi / lo):>10.2f}", end="")
        print()
    print("  1.0 means time grows in step with the data. Above 1.0 the cost")
    print("  grows faster than the dataset, which is what caps the usable scale.")

    print("\nfit of generate_seconds = a * scale**b over the measured range:")
    print(f"{'version':>10} {'exponent b':>11} {'a':>10} {'r^2':>7}   reading")
    exponents, prefactors = {}, {}
    for label in labels:
        points = sorted(results[label])
        if len(points) < 3:
            print(f"{label:>10}   too few points to fit")
            continue
        b, a, r2 = fit_power_law(points, [results[label][s]["generate_s"] for s in points])
        exponents[label], prefactors[label] = b, a
        reading = "linear in the data" if b < 1.08 else ("mildly superlinear" if b < 1.25 else "superlinear")
        print(f"{label:>10} {b:>11.3f} {a:>10.2f} {r2:>7.4f}   {reading}")

    if len(exponents) == 2 and len(prefactors) == 2:
        first, second = labels
        delta = exponents[first] - exponents[second]
        print(f"\nexponent fell from {exponents[first]:.3f} to {exponents[second]:.3f} "
              f"(difference {delta:+.3f}).")
        print("predicted speed-up from the two fits, t_old/t_new = "
              f"(a_old/a_new) * scale**{delta:.3f}:")
        ratio = prefactors[first] / prefactors[second]
        for target in (0.01, 0.1, 0.3, 1.0, 10.0):
            measured = ""
            if target in common:
                a = results[first][target]["generate_s"]
                b = results[second][target]["generate_s"]
                measured = f"   (measured {a / b:.1f}x)"
            print(f"  scale {target:<6} {ratio * target ** delta:>7.1f}x{measured}")
        print("  Values beyond the measured range are extrapolation, not measurement,")
        print("  and are only meaningful if the growth rate above holds steady.")

        # Split the range in half and refit. Exponents that differ between the
        # halves mean the single fit above is an average of two regimes and
        # should not be extrapolated from.
        if len(common) >= 6:
            mid = len(common) // 2
            print(f"\nsplit fits, below and above scale {common[mid]}:")
            split = {}
            for label in labels:
                lo_pts, hi_pts = common[:mid + 1], common[mid:]
                blo, _, rlo = fit_power_law(lo_pts, [results[label][s]["generate_s"] for s in lo_pts])
                bhi, _, rhi = fit_power_law(hi_pts, [results[label][s]["generate_s"] for s in hi_pts])
                split[label] = (blo, bhi)
                print(f"  {label:>8}  {blo:.3f} (r2 {rlo:.4f}) below,  {bhi:.3f} (r2 {rhi:.4f}) above")
            spread = {k: abs(v[1] - v[0]) for k, v in split.items()}
            if max(spread.values()) > 0.15:
                worst = max(spread, key=spread.get)
                print(f"  {worst} changes regime across the range (exponent moves by "
                      f"{spread[worst]:.2f}), so the single fit above is an average of two")
                print("  behaviours. Quote the two halves, not the average.")

    print(f"\ntotal wall clock {time.perf_counter() - started_all:.0f}s")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
