# Benchmarks

The harness behind [docs/scaling.md](../docs/scaling.md), the raw results, and the detail that does not belong on a documentation page: per-interval slopes, the fit caveat, worker traces, and the before/after of the 0.6.1 worker fixes.

## Files

- `bench_scale.py`: one run, one line of JSON (generation time, write time, sampled peak memory of the process tree, machine). Dependency-light so it runs against an old release in another venv.
- `compare.py`: a set of scales across versions, prints the comparison.
- `scaling.py`: fits the growth exponent, prints per-interval log-log slopes and a split fit, and says when the two halves disagree.
- `scaling-m3pro.json`: 0.5.0 against 0.6.0, scales 0.005 to 0.3, Apple M3 Pro.
- `scaling-m3pro-high.json`: 0.6.0 alone, scales 0.1 to 0.8, M3 Pro.
- `scaling-win-i7.json`: 0.6.0 and 0.6.1, Intel Core i7-9850H laptop, single process and by worker count.

## Growth exponents

Local log-log slope between adjacent points; 1.0 means time grows in step with the data.

| range | 0.5.0, M3 | 0.6.0, M3 | 0.6.0, i7 |
|---|---|---|---|
| 0.005 to 0.01 | 1.01 | 0.87 | 0.86 |
| 0.01 to 0.02 | 0.99 | 1.06 | 0.91 |
| 0.02 to 0.05 | 1.05 | 1.03 | 0.85 |
| 0.05 to 0.1 | 1.08 | 1.05 | 1.02 |
| 0.1 to 0.2 | 1.26 | 1.02 | |
| 0.2 to 0.3 | 1.52 | 1.17 | 0.99 |

Fitting each half separately, 0.5.0 goes from an exponent of 1.03 below `scale=0.1` to 1.35 at and above it; 0.6.0 moves from 1.01 to 1.07. A single power law across the whole range gives 1.10 and 1.03 with r-squared above 0.996 and is misleading: the residuals are structured and it under-predicts `scale=0.3` by 15%, because it averages two regimes. `scaling.py` prints both.

0.6.0 alone from `scale=0.1` to `0.8` on the M3 (single runs at the expensive end, generation only, write skipped):

| scale | generate | local slope | peak memory (Mac reading) |
|---|---|---|---|
| 0.1 | 12.3 s | | 2.93 GB |
| 0.2 | 25.9 s | 1.07 | 4.65 GB |
| 0.3 | 39.8 s | 1.06 | 5.62 GB |
| 0.4 | 55.3 s | 1.14 | 5.84 GB |
| 0.5 | 66.9 s | 0.85 | 7.83 GB |
| 0.6 | 80.5 s | 1.02 | 7.97 GB |
| 0.8 | 118.8 s | 1.35 | 8.16 GB |

Exponent across the range 1.072, r-squared 0.9987. The 0.85 and the 1.35 are single-run noise; the memory column is the macOS resident size, which undercounts for the reasons on the scaling page (the i7 reads 10.1 GB at `scale=0.3` against 5.6 here).

## Phase split

`scale=0.1`, single process.

| phase | M3 | i7 |
|---|---|---|
| node attributes | 5.5 s (57%) | 20.2 s (49%) |
| transaction process | 3.7 s (38%) | 11 s (27%) |
| population index, merge, apply | 0.4 s | 9 s |
| pattern injection | 0.1 s | 0.4 s |

## Workers

Generation only, best of three, `scale=0.1` unless stated.

| machine, version | 1 | 2 | 4 | 8 |
|---|---|---|---|---|
| M3, 0.6.0 | 12.4 s | | 10.4 s | 10.7 s |
| i7, 0.6.0 | 40.9 s | 37.5 s | 39.0 s | 48.0 s |
| i7, 0.6.1 (later session, laptop warm) | 49.5 s | | 34.2 s | |
| i7, 0.6.0, scale 0.3 | 121.9 s | | 107.8 s | |
| i7, 0.6.1, scale 0.3 | 141.4 s | | 92.8 s | |

Karp-Flatt effective serial fraction, `e = (1/S - 1/p) / (1 - 1/p)`, against the phase-measured serial share of 0.43 (M3) and 0.51 (i7):

| machine, scale, version | p | speed-up | Amdahl bound | `e` |
|---|---|---|---|---|
| M3, 0.1, 0.6.0 | 4 | 1.19x | 1.75x | 0.78 |
| M3, 0.1, 0.6.0 | 8 | 1.16x | 2.00x | 0.84 |
| i7, 0.1, 0.6.0 | 2 | 1.09x | 1.32x | 0.83 |
| i7, 0.1, 0.6.0 | 4 | 1.05x | 1.58x | 0.94 |
| i7, 0.1, 0.6.0 | 8 | 0.85x | 1.75x | 1.20 |
| i7, 0.3, 0.6.0 | 4 | 1.13x | 1.77x | 0.85 |
| i7, 0.1, 0.6.1 | 4 | 1.45x | 1.6x | 0.59 |
| i7, 0.3, 0.6.1 | 4 | 1.52x | 1.6x | 0.54 |

Node phase alone on the i7 by worker count, which is where the 0.6.0 overhead was visible:

| workers | node phase, 0.6.0 | Customer (72 shards) | Account (100 shards, foreign key) | node phase, 0.6.1 |
|---|---|---|---|---|
| 1 | 20.2 s | 13.8 s | 1.9 s | 16.5 s |
| 2 | 22.9 s | 13.5 s | 5.9 s | |
| 4 | 20.6 s | 11.5 s | 6.5 s | 13.3 s |
| 8 | 26.7 s | 13.2 s | 11.1 s | 14.2 s |

Tracing the shards individually on the i7 with four workers, 0.6.0: the first Customer shard started 7.0 s after the pool was created (the workers' `import graphfaker`), then 72 shards ran at 3.8x parallelism for 7.2 s; in-worker busy time was 27 s for 13.8 s of single-process work, so a shard took 0.37 s in a worker against 0.15 s alone. With two workers 0.22 s. Pinning polars to one thread (`POLARS_MAX_THREADS=1`) changed nothing, so it is the clock under load, not thread oversubscription. Account, which had a pool of its own in 0.6.0 for the foreign-key index, started its shards at +5.2, +10.7, +15.8 and +21.3 s: one worker at a time, because the index travelled in `initargs` and on Windows the parent's write of those arguments blocks until the child has imported its main module. 0.6.1 moves the index to a file, uses one pool for the run, skips the pool under 250,000 rows, and imports the package lazily; the first shard now starts at 4.3 s, and a worker's import is 1.5 s warm instead of 2.9 s.

## Memory

Sampled peak of the process tree every 0.2 s.

| scale | i7 0.6.0, generation only | i7 0.6.0, with write | i7 0.6.1, with write | M3 0.6.0, generation only |
|---|---|---|---|---|
| 0.1 | 3.37 GB | 3.7 GB | 2.5 GB | 2.93 GB |
| 0.3 | 9.38 GB | 10.1 GB | 5.2 GB | 5.62 GB |
| 1.0 | | 32.4 GB (4 workers) | 15.1 GB (single process) | |

0.6.1 at `scale=1.0`, single process: nodes 244 s, patterns 27 s, transactions 129 s, generate 426 s, write 95 s, 2.23 GB of Parquet.

The i7 is linear in both versions: about 33 GB per unit of scale in 0.6.0, about 15 GB in 0.6.1. The M3 reads lower and increasingly so with scale; macOS compresses idle pages out of the resident set and its allocator returns freed memory sooner. A short spike between samples is invisible to both.

## Running it

```sh
uv venv --python 3.12 /tmp/gf050
uv pip install --python /tmp/gf050/bin/python graphfaker==0.5.0 psutil

python benchmarks/scaling.py \
    --version 0.5.0=/tmp/gf050/bin/python \
    --version 0.6.0=.venv/bin/python \
    --scale 0.005 --scale 0.01 --scale 0.02 --scale 0.05 --scale 0.1

python benchmarks/bench_scale.py --scale 0.1 --workers 4 --label 0.6.1 --no-write
```
