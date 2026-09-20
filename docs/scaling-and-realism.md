# Speed, realism and hard negatives

Notes from making GraphFaker 0.6.0. The measurements are on the [scaling page](scaling.md); this is what we concluded from them, and it changed our mind about which properties of a synthetic fraud dataset are worth optimising.

We set out to make the fraud generator fast enough to be useful at a million accounts and above. It is now between 12 and 18 times faster than 0.5.0, depending on the size of the dataset, and a full-size bank of ten million accounts and ninety million transactions takes eight minutes instead of two hours. That turned out to be the easy part.

## One number cannot describe the change

We first reported 17x from a Windows laptop and 12x from the Mac, and spent a while assuming one of them was wrong. Neither was. The speed-up is a function of scale, and there are two separate improvements in it that behave differently.

One is a constant factor, from drawing attributes a column at a time instead of a row at a time. Below a million accounts the ratio sits flat between 12x and 13x, both versions grow linearly with the data, and that ratio will not get better: it is the price of a Python loop against numpy.

The other is asymptotic. Above a million accounts 0.5.0 stops growing in step with the data and its log-log slope climbs from 1.0 to 1.5, because recruiting the members of an injected pattern scanned every account and the number of patterns grows with the scale. 0.6.0 stays at 1.0 as far as we have looked, so the gap keeps opening.

A methodological note, because we nearly published the wrong thing. Fitting a single power law across the whole range gives 1.10 for 0.5.0 and 1.03 for 0.6.0 with an r-squared above 0.996. It looks authoritative and it is misleading: the residuals are structured and the fit under-predicts the top scale by 15%, because it is averaging two regimes. A good fit statistic is not evidence that you have the right model. The harness now prints the per-interval slopes and says so out loud when the two halves disagree.

## The bottleneck moved, and that is the real result

This is the part we did not expect. Node generation used to dominate a run. It is now a little over half, with a single-threaded transaction process making up most of the rest. Which means the `--workers` flag, the thing we built to make large runs tractable, has very little left to divide: Amdahl's law bounds four workers at about 1.6x whatever we do.

Measuring against that bound taught us something too. In 0.6.0 the run behaved as if 80 to 90% of it were serial, against a measured 43 to 51%, and the difference was not work but start-up: every worker imported the whole package before its first shard, and node types with foreign keys paid for a pool of their own. 0.6.1 fixed those and the effective serial fraction dropped to within a few points of the phase split. The optimisation was successful enough to make its own parallelism close to redundant at mid scale: what is left to gain is in the transaction process, not in adding processes. We would not have known that without measuring by phase, and we would have happily kept telling people to add workers.

## Speed is what makes the statistics usable

Fraud is rare. In the fraud pack at `--hardness medium`, 110 accounts out of 100,000 belong to a laundering pattern at `scale=0.01`, a base rate of about 0.11%. That is realistic and it is brutal for anyone trying to measure a detector.

The analysis in [`docs/notebooks/neo4j_fraud_analysis.ipynb`](notebooks/neo4j_fraud_analysis.ipynb) builds a gradient-boosted model on thirty features from a `scale=0.01` dataset and cross-validates it. Average precision comes out at 0.069 with a standard deviation of 0.022 across five folds. The error bar is a third of the estimate. You cannot compare two detectors on a dataset that size and claim the better one is better, because the measurement noise swamps any plausible difference between them.

The fix is not a better model or a cleverer metric. It is more positives, and the only way to get more positives is a bigger dataset. At `scale=1.0` there are roughly eleven thousand fraud accounts instead of a hundred and ten, and the same experiment becomes conclusive.

So generation cost is not a developer convenience. It is the binding constraint on whether a synthetic benchmark can answer the question it was built to answer. A generator that takes over two hours to produce the only scale at which your experiment is statistically meaningful is a generator nobody runs, which means the experiment does not happen. That, more than any throughput figure, is what the eight minutes buys.

## Realism is the harder axis, and it does not show up in a benchmark table

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

## You cannot measure precision without hard negatives

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
| timing | every transaction carries the same constant timestamp | recurring flows on their days, seasonality by hour and weekday, patterns spread over a window that hardness widens |
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

Memory was the binding constraint in 0.6.0, at 32 GB for a full-size bank. 0.6.1 builds the transaction process a block of accounts at a time and assembles the channels without a second copy, which brought that to 15 GB; the [scaling page](scaling.md) has the table and the reasons. Time and memory are both linear in the data now, and a full-size bank generates in seven minutes on a 16 GB laptop. The social topology model is still sequential and suits graphs up to about a million edges. Balances are not tracked as a running ledger.

And one thing changed in 0.6.0 that anyone depending on reproducibility should know. A run is still a pure function of its seed and shard size, but the attribute values differ from 0.5.0's for the same seed, because columns now come from the shard's numpy stream rather than from Faker's call sequence, and edge counts move by about 0.15% as a consequence. We had originally written that counts were unchanged, and measuring showed that was not quite true.
