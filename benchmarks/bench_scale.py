"""Measure one fraud run: generation time, write time, peak memory.

Deliberately dependency-light and free of any import that only exists in one
version of GraphFaker, so the same file can be run against an old release in
one virtualenv and the working tree in another. One run, one line of JSON on
stdout.

    python benchmarks/bench_scale.py --scale 0.01 --seed 42 --label 0.6.0

Peak memory is sampled rather than derived. ``resource.getrusage`` reports
only this process, and node sampling happens in a worker pool, so the
sampler walks the process tree every ``--interval`` seconds and keeps the
largest total resident size it sees. That is the number a machine has to be
able to hold, which is what anyone sizing a run wants to know. It is a
sampled maximum, so a spike shorter than the interval can be missed; the
default 0.2 s matches the figures quoted in the README.

``compare.py`` drives this across versions and scales and prints the table.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
import threading
import time
from pathlib import Path


def _tree_rss(process) -> int:
    """Resident bytes of this process and every child, workers included."""
    import psutil

    total = 0
    try:
        total = process.memory_info().rss
        for child in process.children(recursive=True):
            try:
                total += child.memory_info().rss
            except psutil.Error:
                continue
    except psutil.Error:
        pass
    return total


class PeakMemory:
    """Sample the process tree in the background, keep the maximum."""

    def __init__(self, interval: float = 0.2):
        import psutil

        self.interval = interval
        self.process = psutil.Process()
        self.peak = _tree_rss(self.process)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, _tree_rss(self.process))
            self._stop.wait(self.interval)

    def __enter__(self) -> PeakMemory:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self.peak = max(self.peak, _tree_rss(self.process))


def _directory_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def machine() -> dict[str, object]:
    """Enough about the host to make a number mean something."""
    import psutil

    return {
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
        "logical_cores": psutil.cpu_count(logical=True),
        "physical_cores": psutil.cpu_count(logical=False),
        "total_memory_gb": round(psutil.virtual_memory().total / 1024**3, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", type=float, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--hardness", default="medium")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--label", default="", help="Version label for the output row.")
    parser.add_argument("--out", default=None, help="Where to write. A temp dir by default.")
    parser.add_argument("--interval", type=float, default=0.2, help="Memory sampling interval.")
    parser.add_argument("--keep", action="store_true", help="Do not delete the output.")
    parser.add_argument("--no-write", action="store_true", help="Time generation only.")
    args = parser.parse_args()

    import graphfaker
    from graphfaker.domains import fraud

    out = Path(args.out) if args.out else Path("_bench_out") / f"s{args.scale}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    row: dict[str, object] = {
        "label": args.label or graphfaker.__version__,
        "version": graphfaker.__version__,
        "scale": args.scale,
        "workers": args.workers,
        "seed": args.seed,
        "hardness": args.hardness,
    }

    try:
        with PeakMemory(args.interval) as memory:
            started = time.perf_counter()
            run = fraud.generate(
                scale=args.scale, seed=args.seed, hardness=args.hardness, workers=args.workers
            )
            row["generate_s"] = round(time.perf_counter() - started, 1)

            if args.no_write:
                row["write_s"] = None
            else:
                started = time.perf_counter()
                run.write(out)
                row["write_s"] = round(time.perf_counter() - started, 1)

            row["nodes"] = run.tables.node_count
            row["edges"] = run.tables.edge_count
        row["peak_memory_gb"] = round(memory.peak / 1024**3, 2)
        row["output_gb"] = None if args.no_write else round(_directory_bytes(out) / 1024**3, 2)
        row["ok"] = True
    except Exception as exc:
        # Recorded rather than raised: at large scales the expected failure is
        # MemoryError, and "did not fit on this machine" is a result worth
        # keeping in the table next to the scales that did.
        row["ok"] = False
        row["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if not args.keep and out.exists():
            shutil.rmtree(out, ignore_errors=True)

    row["machine"] = machine()
    print(json.dumps(row))
    return 0 if row.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
