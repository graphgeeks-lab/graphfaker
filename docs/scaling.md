# Scaling: what was measured

How long a fraud run takes, how much memory it needs, and what `--workers` is worth, measured on two machines with the harness in `benchmarks/`. The reasoning behind the numbers, and what they mean for synthetic fraud data, is in [Speed, realism and hard negatives](scaling-and-realism.md). The per-interval slopes, the fits and the shard-level traces are in [`benchmarks/README.md`](https://github.com/graphgeeks-lab/graphfaker/blob/main/benchmarks/README.md).

## Time

One command, one seed, generation only, best of three runs where a run took under 45 s:

```sh
graphfaker fraud --scale 0.1 --seed 42 --out ./bank
```

| scale | accounts | transactions | 0.5.0, M3 Pro | 0.6.0, M3 Pro | 0.6.0, i7-9850H |
|---|---|---|---|---|---|
| 0.005 | 50K | 450K | 7.2 s | 0.6 s | 2.7 s |
| 0.01 | 100K | 900K | 14.5 s | 1.1 s | 4.9 s |
| 0.02 | 200K | 1.8M | 28.8 s | 2.3 s | 9.2 s |
| 0.05 | 500K | 4.5M | 75.4 s | 5.9 s | 20.1 s |
| 0.1 | 1M | 9M | 159.8 s | 12.2 s | 40.9 s |
| 0.2 | 2M | 18M | 383.3 s | 24.8 s | |
| 0.3 | 3M | 27M | 709.9 s | 39.8 s | 121.9 s |
| 1.0 | 10M | 90M | over 2 h (i7, 4 workers) | | 8 min (i7, 4 workers) |

The machines: an Apple M3 Pro (12 cores, 36 GB, macOS, Python 3.12) and a 2019 laptop with an Intel Core i7-9850H (6 cores, 12 threads, 48 GB, Windows 11, Python 3.12.3). The i7 is three to four times slower in absolute terms and follows the same curve.

Two things are in that table. Below a million accounts 0.6.0 is a flat 12 to 13 times faster than 0.5.0, the price of drawing a column with numpy against a Python loop per row. Above it 0.5.0 grows faster than the data (its log-log slope climbs from 1.0 to 1.5 between `scale=0.1` and `0.3`) and 0.6.0 does not (1.0 to 1.07 on the M3, 1.0 on the i7, up to `scale=0.8` on the M3), so the gap keeps widening: 17.8x at three million accounts, and over two hours against eight minutes at ten million.

## What changed in 0.6.0

Three things, in order of how much they mattered.

- **Attributes are drawn a column at a time.** `graphfaker.engine.fastfaker` reads Faker's locale tables off the Faker instance and draws from them with numpy, keeping the vocabulary and the weights; a call is a column, not a row. The simple samplers and foreign keys are vectorised the same way. This is the constant factor.
- **Pattern injection stopped scanning.** Recruiting a ring member was a set difference over every eligible account plus a weighted choice over all of them, once per member, with the number of patterns growing with the scale; decoys rebuilt their pools per pattern. That was the superlinear term. Recruitment is now an O(1) draw and the pools are built once.
- **Foreign-key indexes are int32 positions, shipped once.** The old form, lists of id strings pickled into every shard job, did not show in single-process runs but spent two hours of the first `scale=1.0` run with workers. The index now goes to a temp file each worker loads once.

Datasets changed as a result: a run is still a pure function of its seed and shard size, but the attribute values differ from 0.5.0's for the same seed, and edge counts move by about 0.15% because an account's transaction rate depends on values that changed. Structure and distributions are the same.

## Where the time goes

At `scale=0.1`, single process:

| phase | share | parallel |
|---|---|---|
| node attributes | 49 to 57% | yes, by `--workers` |
| transaction process | 38 to 40% | no |
| population index, merge | about 4% | no |
| pattern injection | about 1% | no |

Node generation used to dominate; it is now about half, and the single-threaded transaction process is the rest. That caps what workers can do, and it says where the next gain is: vectorising or streaming the transaction process, not adding processes.

## Workers

Only the node phase is parallel, so Amdahl's law bounds four workers at about 1.6x. The useful check is the Karp-Flatt metric: from a measured speed-up `S` on `p` workers, `e = (1/S - 1/p) / (1 - 1/p)` is the fraction of the run that behaves as serial. It should equal the measured serial share (0.43 to 0.51); anything above that is overhead.

| machine, scale | four workers | Amdahl bound | Karp-Flatt `e` |
|---|---|---|---|
| i7, 0.1, 0.6.0 | 1.05x | 1.6x | 0.94 |
| i7, 0.1, 0.6.1 | 1.45x | 1.6x | 0.59 |
| i7, 0.3, 0.6.0 | 1.13x | 1.6x | 0.85 |
| i7, 0.3, 0.6.1 | 1.52x | 1.6x | 0.54 |
| M3, 0.1, 0.6.0 | 1.19x | 1.75x | 0.78 |

In 0.6.0 the run behaved 80 to 90% serial because of start-up: each worker imported the whole package (5 to 7 s on Windows) before its first shard, and a node type with foreign keys got a pool of its own and paid that again. 0.6.1 uses one pool for the run, has workers load the index file on demand, keeps node types under 250,000 rows in process, and imports the package lazily (a worker's import fell from 2.9 s to 1.5 s warm). The effective serial fraction is now within a few points of the phase split. What remains is that a shard runs 1.5 to 2.5 times slower when four processes are busy on a laptop (the clock drops under load), which is hardware; on the M3 the 0.6.1 gain should be larger and has not been measured yet.

In practice: `--workers 4` is worth about 1.5x from `scale=0.1` upward, and nothing below it, where the pool is not used.

## Memory

Peak resident size of the process tree, sampled every 0.2 s, generation and write:

| scale | i7, Windows | M3 Pro, macOS |
|---|---|---|
| 0.1 | 3.7 GB | 3.4 GB |
| 0.3 | 10.1 GB | 6.3 GB |
| 1.0 | 32 GB (4 workers) | not run: it would swap |

On Windows the peak is linear, about 33 GB per unit of scale and roughly five times the size of the final tables (6.6 GB at `scale=1.0`), because every table is held in memory until the write and the transaction assembly briefly holds a channel twice. The two machines agree at `scale=0.1` and diverge above it because resident size is not the same measurement on the two systems: macOS compresses idle pages out of the resident set and returns freed memory sooner, so its figure undercounts a peak made of transient frames. Size a machine from the Windows column; treat the Mac column as a floor. Parquet goes out in 2M-row row groups, so the write itself adds little.

Streaming the transaction process to disk as it goes is the next step and would bring `scale=1.0` under 16 GB.

## Reproducing it

```sh
uv venv --python 3.12 /tmp/gf050
uv pip install --python /tmp/gf050/bin/python graphfaker==0.5.0 psutil

python benchmarks/scaling.py \
    --version 0.5.0=/tmp/gf050/bin/python \
    --version 0.6.0=.venv/bin/python \
    --scale 0.005 --scale 0.01 --scale 0.02 --scale 0.05 --scale 0.1
```

`benchmarks/bench_scale.py` measures one run and prints a line of JSON; `benchmarks/compare.py` runs a set of scales across versions; `benchmarks/scaling.py` fits the growth and reports the per-interval slopes. Raw results: `benchmarks/scaling-m3pro.json`, `scaling-m3pro-high.json`, `scaling-win-i7.json`.
