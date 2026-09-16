# Scaling and realism: notes from 0.6.0

Ten times faster, and why that was the easy part. Notes from making GraphFaker 0.6.0, with the measurements behind the claims.

We set out to make the fraud generator fast enough to be useful at a million accounts and above. It is now between 12 and 18 times faster than 0.5.0, depending on the size of the dataset, and the "depending on" turns out to be the interesting part. Along the way the work changed our mind about which properties of a synthetic fraud dataset are worth optimising. This is a record of what we measured and what we concluded.

Everything below is reproducible. The harness is in `benchmarks/`, both versions were installed side by side, and the raw results sit next to the tables in `benchmarks/scaling-m3pro.json`.

If you read one paragraph: below a million accounts 0.6.0 is a flat 12 to 13 times faster, from drawing attributes a column at a time; above it 0.5.0 turned superlinear and 0.6.0 stays linear, so the gap keeps widening; the bottleneck is now the single-threaded transaction process rather than node attributes, which makes `--workers` nearly redundant at mid scale; memory, not time, is what limits a full-size bank; and the speed matters because fraud is rare enough that only a large dataset gives a detector benchmark an error bar smaller than the effect it measures.

## What we measured

One machine, an Apple M3 Pro with 12 logical cores and 36 GB, macOS, Python 3.12. Both versions run from the same command with the same seed, generation timed on its own, best of three runs per point where a run took under 45 seconds:

```sh
graphfaker fraud --scale 0.1 --seed 42 --out ./bank
```

| scale | accounts | transactions | 0.5.0 | 0.6.0 | speed-up |
|---|---|---|---|---|---|
| 0.005 | 50K | 450K | 7.2 s | 0.6 s | 12.0x |
| 0.01 | 100K | 900K | 14.5 s | 1.1 s | 13.2x |
| 0.02 | 200K | 1.8M | 28.8 s | 2.3 s | 12.5x |
| 0.05 | 500K | 4.5M | 75.4 s | 5.9 s | 12.8x |
| 0.1 | 1M | 9M | 159.8 s | 12.2 s | 13.1x |
| 0.2 | 2M | 18M | 383.3 s | 24.8 s | 15.5x |
| 0.3 | 3M | 27M | 709.9 s | 39.8 s | 17.8x |

Thirty minutes of wall clock, nearly all of it 0.5.0 at the top two scales.

## One number cannot describe this change

We first reported 17x from a Windows laptop and 12x from the Mac, and spent a while assuming one of them was wrong. Neither was. They are measurements at different scales, and the speed-up is a function of scale.

There are two separate improvements in that table and they behave differently.

**A constant factor, from vectorising the attribute draws.** From `scale=0.005` to `scale=0.1` the ratio sits flat between 12x and 13x. Both versions grow linearly with the data over that range, so this is simply the price of drawing a column with numpy against drawing it with a Python loop. It does not grow with the dataset and it will not get better.

**An asymptotic one, from removing two superlinear costs.** Above `scale=0.1` the versions stop growing at the same rate. The clearest way to see it is the slope between adjacent points on a log-log plot, which is the local growth exponent:

| range | 0.5.0 | 0.6.0 |
|---|---|---|
| 0.005 to 0.01 | 1.01 | 0.87 |
| 0.01 to 0.02 | 0.99 | 1.06 |
| 0.02 to 0.05 | 1.05 | 1.03 |
| 0.05 to 0.1 | 1.08 | 1.05 |
| 0.1 to 0.2 | **1.26** | 1.02 |
| 0.2 to 0.3 | **1.52** | 1.17 |

A slope of 1.0 means time grows in step with the data, which is the best a generator can do when the output is proportional to the input. 0.5.0 holds near 1.0 up to a million accounts and then climbs to 1.5. Fitting each half separately, its exponent goes from 1.03 below `scale=0.1` to 1.35 at and above it, while 0.6.0 moves only from 1.01 to 1.07.

So the honest summary needs two sentences. Below a million accounts, 0.6.0 is a flat 12x to 13x faster. Above it, 0.5.0 degrades and 0.6.0 does not, so the gap keeps opening: 17.8x at three million accounts, and the figures we have at ten million (over two hours against eight minutes) are consistent with the trend continuing.

A methodological note, because we nearly published the wrong thing. Fitting a single power law across the whole range gives 1.10 for 0.5.0 and 1.03 for 0.6.0 with an r-squared above 0.996. It looks authoritative and it is misleading: the residuals are structured and the fit under-predicts `scale=0.3` by 15%, because it is averaging two regimes. A good fit statistic is not evidence that you have the right model. `benchmarks/scaling.py` now prints the per-interval slopes and a split fit, and says so out loud when the two halves disagree.

### Does 0.6.0 have a knee of its own?

Not one we can find. Running 0.6.0 alone from `scale=0.1` to `scale=0.8`, which is eight million accounts and 72 million transactions:

| scale | generate | peak memory | local slope |
|---|---|---|---|
| 0.1 | 12.3 s | 2.93 GB | |
| 0.2 | 25.9 s | 4.65 GB | 1.07 |
| 0.3 | 39.8 s | 5.62 GB | 1.06 |
| 0.4 | 55.3 s | 5.84 GB | 1.14 |
| 0.5 | 66.9 s | 7.83 GB | 0.85 |
| 0.6 | 80.5 s | 7.97 GB | 1.02 |
| 0.8 | 118.8 s | 8.16 GB | 1.35 |

The memory column is the Mac reading; see the limits section for why it undercounts and what the Windows laptop measured. Fitted across the whole range the time exponent is 1.072 with an r-squared of 0.9987. The slopes bounce around 1.0 with the noise you would expect from single runs at the expensive end, including one interval at 0.85 that cannot be real. The last interval at 1.35 is the only hint of a turn, and on one measurement we would not call it. Time, then, is linear in the data as far as we have looked.

Which makes `scale=1.0` a memory question rather than an algorithmic one, and that is the useful conclusion here.

## The same curve on a second machine

The Mac is fast hardware. To separate what the software does from what the hardware does, the same harness ran on a 2019 laptop: Intel Core i7-9850H (6 cores, 12 threads, 2.6 GHz base), 48 GB, NVMe SSD, Windows 11, Python 3.12.3. Same command, same seed, generation only, single process, best of three where a run took under 45 s. Results in `benchmarks/scaling-win-i7.json`.

| scale | accounts | M3 Pro | i7-9850H | ratio | i7 local slope |
|---|---|---|---|---|---|
| 0.005 | 50K | 0.6 s | 2.7 s | 4.5x | |
| 0.01 | 100K | 1.1 s | 4.9 s | 4.5x | 0.86 |
| 0.02 | 200K | 2.3 s | 9.2 s | 4.0x | 0.91 |
| 0.05 | 500K | 5.9 s | 20.1 s | 3.4x | 0.85 |
| 0.1 | 1M | 12.2 s | 40.9 s | 3.4x | 1.02 |
| 0.3 | 3M | 39.8 s | 121.9 s | 3.1x | 0.99 |

Two things to take from it. The absolute gap is the hardware: three to four times, closing slightly as the data grows and fixed costs matter less. The shape is the software: the i7's local slope sits at 1.0 across the range, exactly as the M3's does, with no knee. A generator that was linear on one machine and not on another would be telling you something about memory or the OS; this one is telling you the algorithm.

Memory is where the two machines disagree, and the disagreement is instructive rather than a discrepancy: the i7 reads 3.4 GB at `scale=0.1` and 9.4 GB at `scale=0.3` against the M3's 2.9 and 5.6 GB. The limits section takes that up.

## What actually changed in the code

Four things, in rough order of how much they mattered.

**Attributes are drawn a column at a time.** Faker is the right source of names, addresses and emails: its locale tables are large, weighted and maintained by people who care. It is also about a millisecond per call, because every call re-parses a format string and re-normalises a weight table. A bank at `scale=1.0` has seven million customers with six such columns each, which is a lot of milliseconds. `graphfaker.engine.fastfaker` reads the same tables off the Faker instance it is given and draws from them with numpy, a column per call, keeping the vocabulary and the weights. It works the way Faker's providers work underneath: pick a format, resolve each token from an element table or another format list, fill the digit and letter placeholders. Providers that do more than that, like `iban` with its checksum, are reported as unsupported and still drawn row by row. The simple samplers (constant, category, uniform, gaussian, lognormal, poisson, bernoulli, reference) and foreign keys are vectorised too.

**Pattern recruitment stopped scanning.** Drawing each member of an injected pattern was a set difference over every eligible account followed by a weighted choice over all of them, and the number of patterns grows with the scale, so injection cost grew with the square of the data. Decoys rebuilt their account pools with a pass over every account per pattern, bystanders were drawn by permuting every account, and the income factor recomputed a mean over every customer per transaction. This is the superlinear term in the single-process table above, and it is why the knee sits where it does: patterns become numerous enough to matter from about a million accounts upward. Recruitment is now an O(1) draw, the pools are built once, and the mean is cached.

**Foreign-key indexes stopped being lists of strings.** They are now int32 positions per group, and a worker pool receives them once through a file in its initialiser rather than with every shard. This one does not show in the single-process table, because a single process never pickles anything; it is the cost that made `--workers` worse than useless at scale: the old form spent two hours of the first `scale=1.0` run pickling seven million customer ids into a thousand shard jobs. A file rather than `initargs`, because on Windows a few megabytes of initialiser arguments make every worker start wait for the previous one's import. Nothing about the output changed, only how the work was handed to the workers.

**Memory got quieter.** Transaction frames are built with polars columns rather than numpy arrays of Python strings. The channels are merged one at a time, with the time ordering computed on a narrow frame instead of a concatenation of everything. Parquet is written in 2M-row row groups, so only one slice is ever copied into Arrow memory. `GraphTables.to_arrow` hands frames to sinks without a copy at all, which is how the LadybugDB and DuckDB loaders avoid staging files entirely.

## The bottleneck moved, and that is the real result

This is the part we did not expect and the part we would lead with if we had to pick one finding.

At `scale=0.1`, where the time goes now:

| phase | seconds | share | parallelised by `--workers` |
|---|---|---|---|
| entities (node attributes) | 5.5 s | 57% | yes |
| legitimate transactions | 3.7 s | 38% | no |
| population index | 0.4 s | 4% | no |
| pattern injection | 0.1 s | 1% | no |

Node generation used to dominate a run. It is now a little over half, with a single-threaded transaction process making up most of the rest. Which means the `--workers` flag, the thing we built to make large runs tractable, has very little left to divide. Best of three runs at `scale=0.1`:

| workers | M3 Pro | i7-9850H |
|---|---|---|
| 1 | 12.4 s | 40.9 s |
| 2 | | 37.5 s |
| 4 | 10.4 s | 39.0 s |
| 8 | 10.7 s | 48.0 s |

About 1.2x from four processes on the Mac, nothing on the i7, and eight workers are slower than one on the i7. At `scale=0.3` the i7 gets 1.13x from four workers (121.9 s to 107.8 s).

### Does it obey Amdahl's law?

Amdahl's law says that if a fraction `f` of a run is parallel, `p` workers give a speed-up of `1 / ((1 - f) + f / p)`. The phase split above puts `f` at 0.57 on the Mac and 0.49 on the i7 (node attributes are the parallel part; the transaction process, the population index and the merge are not). So the law predicts 1.75x from four workers on the Mac and 1.58x on the i7, and it caps the Mac at 2.3x however many workers you add.

The measurements fall well short of that, and the useful question is not whether the law holds (it always does; it is a bound) but how much of the run is behaving as serial in practice. The Karp-Flatt metric answers it: from a measured speed-up `S` on `p` workers, `e = (1/S - 1/p) / (1 - 1/p)` is the serial fraction the run *acts* as if it had. If overhead were zero, `e` would equal `1 - f` at every `p`. If `e` grows with `p`, the overhead grows with the worker count.

| machine, scale | workers | measured | Amdahl predicts | Karp-Flatt `e` | `1 - f` |
|---|---|---|---|---|---|
| M3 Pro, 0.1 | 4 | 1.19x | 1.75x | 0.78 | 0.43 |
| M3 Pro, 0.1 | 8 | 1.16x | 2.00x | 0.84 | 0.43 |
| i7, 0.1 | 2 | 1.09x | 1.32x | 0.83 | 0.51 |
| i7, 0.1 | 4 | 1.05x | 1.58x | 0.94 | 0.51 |
| i7, 0.1 | 8 | 0.85x | 1.75x | 1.20 | 0.51 |
| i7, 0.3 | 4 | 1.13x | 1.77x | 0.85 | 0.42 |

The run behaves as if 80 to 90% of it were serial, against a measured 43 to 51%, and `e` rises with `p` on both machines. That is the signature of overhead rather than of a wrong phase split, and we can see what the overhead is because the node phase was timed on its own on the i7:

| workers | node phase | of which Customer (72 shards) | of which Account (100 shards, has a foreign key) |
|---|---|---|---|
| 1 | 20.2 s | 13.8 s | 1.9 s |
| 2 | 22.9 s | 13.5 s | 5.9 s |
| 4 | 20.6 s | 11.5 s | 6.5 s |
| 8 | 26.7 s | 13.2 s | 11.1 s |

Three costs, none of them in Amdahl's model. A worker process has to import graphfaker before it can do anything, which is 5 to 7 s on Windows and 2 to 3 s on the Mac; with the shards traced individually, the first Customer shard starts 7 s after the pool is created and the rest run at 3.8x parallelism for the remaining 7 s, which is why 13.8 s becomes 11.5 s and not 4 s. Node types with a foreign key get a pool of their own so the index can be installed once per worker, so Account pays that start-up again for 1.9 s of work and gets slower with every worker added. And the work itself slows down when the processes run together: an in-worker shard takes 1.5 to 1.7 times longer with four processes busy than alone, which on a laptop is the clock dropping once several cores are loaded, and past six workers the i7 is sharing physical cores between hyperthreads.

So the parallel part is real, and it does scale, but only once it is large enough to amortise a start-up cost that is fixed per pool and a slowdown that is proportional to the work. At `scale=1.0` the Customer table alone is about 140 s of single-process work, the start-up is noise, and four workers bring the node phase from an estimated 300 s to the 150 to 200 s we measured. Below `scale=0.3` on Windows the flag should not be used at all. Two fixes follow directly: reuse one pool across node types and have workers load the index file on demand, which removes the repeated start-up, and skip the pool for a node type whose work is smaller than the start-up cost. Neither changes the output. Both are in 0.6.1, along with a third that came out of looking at what a worker spends its first seconds on: `import graphfaker` was pulling in networkx, pandas, the fetchers and every domain, so the package now imports its public names lazily and a worker's import fell from 2.9 s to 1.5 s warm.

Same laptop, same harness, four workers, measured after those changes (the single-process times were about 20% slower in this session than in the table above, the laptop having warmed up, so read the ratios rather than the seconds):

| scale | 0.6.0 speed-up | 0.6.1 speed-up | Amdahl bound | Karp-Flatt `e`, 0.6.0 | Karp-Flatt `e`, 0.6.1 |
|---|---|---|---|---|---|
| 0.1 | 1.05x | 1.45x | 1.6x | 0.94 | 0.59 |
| 0.3 | 1.13x | 1.52x | 1.6x | 0.85 | 0.54 |

The effective serial fraction is now within a few points of the measured phase split, which is as close to Amdahl as a laptop gets: what remains is the per-shard slowdown when four processes run together (0.15 s alone, 0.22 s with two, 0.37 s with four, and the same with polars pinned to one thread, so it is the clock and the caches rather than thread oversubscription). The Customer table is still the case that gains least, because it is the first type to reach the pool and absorbs the start-up; on the Mac, where a process starts in 2 s, the gain should be larger, and that is a measurement worth repeating there.

The larger point stands. The optimisation was successful enough to make its own parallelism close to redundant at mid scale. The next real gain is vectorising or streaming the transaction process, not adding processes, and we would not have known that without measuring by phase.

## What this means for synthetic data of this nature

Three conclusions, and the first is the reason any of this matters.

### Speed is what makes the statistics usable

Fraud is rare. In the fraud pack at `--hardness medium`, 110 accounts out of 100,000 belong to a laundering pattern at `scale=0.01`, a base rate of about 0.11%. That is realistic and it is brutal for anyone trying to measure a detector.

The analysis in [`docs/notebooks/neo4j_fraud_analysis.ipynb`](notebooks/neo4j_fraud_analysis.ipynb) builds a gradient-boosted model on thirty features from a `scale=0.01` dataset and cross-validates it. Average precision comes out at 0.069 with a standard deviation of 0.022 across five folds. The error bar is a third of the estimate. You cannot compare two detectors on a dataset that size and claim the better one is better, because the measurement noise swamps any plausible difference between them.

The fix is not a better model or a cleverer metric. It is more positives, and the only way to get more positives is a bigger dataset. At `scale=1.0` there are roughly eleven thousand fraud accounts instead of a hundred and ten, and the same experiment becomes conclusive.

So generation cost is not a developer convenience. It is the binding constraint on whether a synthetic benchmark can answer the question it was built to answer. A generator that takes over two hours to produce the only scale at which your experiment is statistically meaningful is a generator nobody runs, which means the experiment does not happen. That, more than any throughput figure, is what the eight minutes buys.

### Realism is the harder axis, and it does not show up in a benchmark table

Having made the thing fast, the honest observation is that speed was the tractable problem. The difficult property of a synthetic fraud dataset is that the fraud should be hard to find for the same reasons real fraud is hard to find.

We loaded the dataset into Neo4j and tried the obvious detectors, scoring each against the ground truth. A fan-in rule with no time window ran at 0.3% precision. A three-hop cycle query found no fraud at all, because the injected cycles are four and five hops long. The best single rule reached 66.7% precision at 3.3% recall, and only after two rounds of refinement guided by the scores. A thirty-feature model reached 32% precision in its top 25 accounts.

Those are low numbers. They are supposed to be. A generator whose fraud can be found by a one-line rule is not measuring detection, it is measuring whether you wrote the one line.

The same axis shows up at the model level. [`examples/pyg_baseline.py`](https://github.com/graphgeeks-lab/graphfaker/blob/main/examples/pyg_baseline.py) trains a logistic regression on account features alone and a two-layer GraphSAGE on the exported `HeteroData`, at `scale=0.002`, seed 42:

| hardness | features only, AUC | features plus graph, AUC | average precision |
|---|---|---|---|
| low | 0.60 | 0.99 | 0.78 |
| medium | 0.48 | 0.85 | 0.09 |
| high | 0.49 | 0.56 | 0.01 |

Account attributes say nothing about who launders money, by construction: the pack recruits ordinary accounts. The graph carries the signal, and hardness moves the same model from a near-perfect detector to one barely above chance. [Training a GNN on the bank](pyg.md) has the details and the caveat about small positive counts.

### You cannot measure precision without hard negatives

The most useful thing in the fraud pack is not the fraud. It is the 47 accounts in eleven patterns that are shaped exactly like laundering and are entirely legitimate: legitimate cycles, legitimate fan-ins, legitimate fan-outs. Money genuinely does move in circles between honest people, and groups of friends genuinely do all pay one person.

When we scored the structural rules, they caught every decoy they could reach. The weekly fan-in rule flagged every decoy fan-in account in the dataset. The cycle detector flagged all twelve decoy cycle accounts and four of the nine real ones. Without those decoys, the fan-in rule would have passed a review that measured only recall, and would have gone live accusing group-holiday organisers of money laundering.

The corollary for anyone building a generator: a dataset whose false positives are random accounts gives flattering precision numbers, because random accounts are easy to dismiss. The false positives that cost an investigator a morning are the ones that look guilty. Those have to be in the data on purpose, and labelled as innocent.

## How this compares to gen-fraud-graph

Santander's [gen-fraud-graph](https://github.com/SantanderAI/gen-fraud-graph) is the closest published thing to our fraud pack, and the fraud pack was designed after reading it. Their hardness presets, their decoy legitimate cycles (whose stated purpose is "to defeat pure amount-thresholding (they look high-value) and pure cycle-topology (they are cycles) without being fraud"), their `evaluate` module scoring at account, transaction and pattern level, and their `verify` step all predate ours by two months, and we adopted every one of those ideas. Our `scale` follows theirs so datasets are comparable (`1.0` is about 10M accounts and 90M transactions), and `--sink gen-fraud-graph` writes their `accounts/`, `transactions/`, `fraud/` layout so a pipeline built on one can switch to the other. No code was copied, but the shape of the problem, hardness levels, hard negatives, a verification step, is theirs, and the fact that a bank's own team arrived at it first is the best argument that it is the right shape.

What we changed is what sits underneath those ideas. A hardness preset only means something relative to the background it hides in, so the fraud pack spends most of its effort on the legitimate process (salary and rent on their days, repeat partners, merchant popularity, seasonality, amounts that scale with income) and then draws the fraud's amounts and timing from that process rather than from a sentinel with jitter. That is the design decision every number below turns on.

| | gen-fraud-graph 0.1.0 | GraphFaker fraud pack 0.6.0 |
|---|---|---|
| entities | accounts | customers, accounts, merchants, devices, counterparties |
| relationships | transactions | ownership, device use, card payments, transfers, wires |
| typologies | cyclic rings, depth 4 to 7 | eleven, including fan-in, fan-out, cycles, scatter-gather, mule networks, structuring, bust-out, synthetic identity |
| hardness knobs | jitter on the fraud amount, ring overlap, decoy ratio | amounts and timing drawn from the legitimate process, pattern accounts also behave normally, decoys |
| output | CSV, Neptune bulk-load | Parquet, plus Neo4j, LadybugDB, DuckDB, PyTorch Geometric, gen-fraud-graph layout |
| extras | optional text embeddings per transaction | declarative schema, verification harness, ground truth as a queryable subgraph |

The sharpest difference is in the amounts, and it is worth being precise because it determines what each dataset can measure. In gen-fraud-graph, fraud edges carry a sentinel amount of 9999 while normal transactions are drawn from 10 to 500. We generated their data at all three hardness levels and asked how much of the fraud a single threshold on amount recovers:

| gen-fraud-graph hardness | normal amounts | fraud amounts | threshold at 500 |
|---|---|---|---|
| `low` (the default) | 10 to 500 | 9999 exactly | 100% recall, 100% precision |
| `medium` | 10 to 500 | 7,580 to 12,418 | 100% recall, 70% precision |
| `high` | 10 to 500 | 5,466 to 14,790 | 100% recall, 50% precision |

Even at `high`, where amounts are jittered by half, the fraud floor stays an order of magnitude above the normal ceiling, so `WHERE amount > 500` finds every fraudulent transaction. The decoy cycles are what pull precision down to 50%, and they do their job. Their own docstring is candid about the default: a jitter of `0.0` "keeps the exact sentinel on every edge, so a naive amount threshold catches all fraud."

The same question on our fraud pack at `--hardness medium`, `scale=0.01`, seed 42:

| | GraphFaker fraud pack |
|---|---|
| legitimate amounts | 1 to 109,838 |
| fraud amounts | 16 to 24,264, entirely inside the legitimate range |
| best single amount threshold | 0.50% precision at 21% recall |

Fraud amounts come from the same log-normal process as everything else, so amount on its own carries almost no signal. That is the property we were chasing, and it is expensive: it is why detection on our dataset tops out in the low tens of percent rather than at 100%.

None of which makes one generator better than the other. gen-fraud-graph is aimed at load-testing graph databases and training GNNs at 90 million transactions, with Neptune bulk-load output and per-transaction embeddings, and it does that with a smaller dependency footprint than ours. If you want a big graph with labelled rings in it, quickly, it is the simpler tool. If you want to measure whether a detector works, the separability of the fraud is the property to check first, in any generator, including ours.

## Limits, honestly

Memory is now the binding constraint rather than time, and our memory numbers are the weakest measurement in this document. The peak grows strongly sublinearly across the range we tested: 2.93 GB at `scale=0.1` rising to only 8.16 GB at `scale=0.8`, so 2.8 times the memory for eight times the data. Per unit of scale that is a fall from 29 GB to 10 GB. We do not fully believe it.

The same runs on the i7 laptop, same sampler, same interval, single process, tell a different story: 3.4 to 3.7 GB at `scale=0.1`, 9.4 to 10.1 GB at `scale=0.3`, 32 GB at `scale=1.0` (the lower figure of each pair is generation only, the higher includes the write). That is linear, about 33 GB per unit of scale, and the two machines agree at `scale=0.1` and then diverge (10.1 GB against 5.6 GB at `scale=0.3`). Resident size is not the same measurement on the two systems: macOS compresses pages that are not being touched and reports them outside the resident set, and its allocator returns freed memory to the system more readily than the Windows heap does, so the Mac figure under-counts a peak made of transient frames while the Windows figure counts memory that was freed but not yet returned. The truth is between them and closer to Windows for sizing purposes, because a machine has to hold the uncompressed working set at the moment of the peak. Until the transaction process streams to disk, treat the Windows numbers as the requirement and the Mac numbers as a floor.

The remaining limits are unchanged. Every table is held in memory until the write, and the transaction assembly briefly holds a channel twice. Streaming the transaction process to disk as it goes is the next piece of work. The social topology model is still sequential and suits graphs up to about a million edges. Balances are not tracked as a running ledger.

One more thing changed in 0.6.0 that anyone depending on reproducibility should know. A run is still a pure function of its seed and shard size, and node counts are identical to 0.5.0's for a given seed. But the attribute values differ, because columns now come from the shard's numpy stream rather than from Faker's call sequence, and edge counts move by about 0.15% as a consequence: an account's transaction rate depends on its owner's activity and its type, and those are among the values that changed. We had originally written that counts were unchanged, and measuring showed that was not quite true.

## Reproducing all of this

```sh
uv venv --python 3.12 /tmp/gf050
uv pip install --python /tmp/gf050/bin/python graphfaker==0.5.0 psutil

python benchmarks/scaling.py \
    --version 0.5.0=/tmp/gf050/bin/python \
    --version 0.6.0=.venv/bin/python \
    --scale 0.005 --scale 0.01 --scale 0.02 --scale 0.05 --scale 0.1
```

`benchmarks/bench_scale.py` measures one run and prints a line of JSON. `benchmarks/compare.py` runs a set of scales across versions and prints the comparison. `benchmarks/scaling.py` fits the growth and reports the per-interval slopes. Raw results for the tables above are in `benchmarks/scaling-m3pro.json`, `benchmarks/scaling-m3pro-high.json` and `benchmarks/scaling-win-i7.json`.
