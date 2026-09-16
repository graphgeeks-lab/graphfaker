# Training a GNN on the bank

A generated graph exports to a PyTorch Geometric `HeteroData` in one call, with features encoded, the ground truth as labels, and train, validation and test masks already drawn. This page shows the export, then a baseline that answers the question the dataset exists to ask: how much does the graph help a detector, and how fast does that help fade as the fraud gets harder?

```bash
pip install "graphfaker[pyg]"
graphfaker generate fraud --scale 0.002 --hardness medium --seed 42 --out ./bank --sink pyg   # writes ./bank/graph.pt
```

```python
import torch
data = torch.load("bank/graph.pt", weights_only=False)
```

Or straight from a run, or from a dataset directory:

```python
from graphfaker.domains import fraud
from graphfaker.sinks.pyg import to_hetero_data, from_directory

run = fraud.generate(scale=0.002, seed=42, hardness="medium")
data = to_hetero_data(run.tables, run.truth, seed=42)
data = from_directory("bank")            # the same, from what `graphfaker generate` wrote
```

## What is in the `HeteroData`

One node store per node type and one edge store per `(source type, relationship, target type)`:

```
Account={ x=[20000, 9], y=[20000], decoy=[20000], train_mask, val_mask, test_mask, region=[20000], node_id, feature_names, feature_stats }
Customer={ x=[14286, 7], region=[14286], ... }
(Account, TRANSFERS, Account)={ edge_index=[2, 49174], edge_attr=[49174, 14], edge_time=[49174], y=[49174] }
(Account, PAYS, Merchant)={ edge_index=[2, 123389], edge_attr=[123389, 3], edge_time=[123389], y=[123389] }
```

Features (`x`) are built per table from the columns a model can use: numeric columns standardised to mean 0 and standard deviation 1, booleans as 0 and 1, dates as days since the earliest value, and strings with a limited number of repeating values one-hot encoded (`account_type=savings`, `memo=salary`). Identifiers, names, emails, free text and foreign keys are left out; `feature_names` says exactly which column each position is, and `feature_stats` holds the mean and standard deviation used, so a prediction can be explained in the dataset's own terms.

Latent factors are treated differently. `region` (and `community` in the social graph) is the hidden variable the generator drew attributes and edges from; putting it in `x` would hand a model the answer to the very correlations it is supposed to learn. It is attached as its own tensor (`data["Account"].region`) so it can serve as a label for community recovery and never as a feature.

Labels come from the truth:

- `data["Account"].y` is 1 for an account that takes part in a fraud pattern, 0 otherwise. `decoy` is 1 for an account whose only patterns are legitimate look-alikes (a business paying salaries has the shape of a fan-out); flagging it is a false positive, and the decoys are there so that a model is measured on that.
- Every relationship with a `tx_id` carries `y` (1 for an injected fraud transaction) and `edge_time` in seconds, so edge-level and temporal experiments have what they need.
- `train_mask`, `val_mask` and `test_mask` split the labelled node type 60/20/20, stratified so the rare positive class is present in every part, from the `seed` you pass. The same seed gives the same split on any machine.

`--blind` (or `truth=None`) produces the same tensors without `y`, `decoy` or the masks, for a dataset that is handed to someone else to score.

The dataset's own relationships are directed. Message passing usually wants both directions, so most models start with `T.ToUndirected(merge=False)(data)`, which adds a `rev_TRANSFERS` edge type rather than losing the direction.

## The baseline

[examples/pyg_baseline.py](https://github.com/graphgeeks-lab/graphfaker/blob/main/examples/pyg_baseline.py) trains two models on the account labels and scores both on the held-out accounts: a logistic regression on the account features alone (no graph), and a two-layer heterogeneous GraphSAGE that also sees the neighbourhood.

```bash
python examples/pyg_baseline.py --scale 0.002 --hardness medium --seed 42
```

At scale 0.002 (20,000 accounts), seed 42, 60 epochs on a laptop CPU:

| hardness | fraud accounts | features only, AUC | features plus graph, AUC | AP |
|---|---|---|---|---|
| low | 172 (0.86%) | 0.60 | 0.99 | 0.78 |
| medium | 121 (0.60%) | 0.48 | 0.85 | 0.09 |
| high | 71 (0.36%) | 0.49 | 0.56 | 0.01 |

Three things to read off this table. Account attributes on their own say nothing about who launders money (AUC at chance), which is by construction: the fraud pack recruits ordinary accounts, so the signal is in what they do, not who they are. The graph carries that signal, and at low hardness a plain GraphSAGE finds nearly all of it from structure alone. And hardness does what it is meant to: the same model, the same size, the same seed, goes from a near-perfect detector to one that is barely better than chance as ring sizes shrink, amounts blend into the legitimate tail and timing spreads out. Average precision falls faster than AUC, which is the number that matters at a 0.4% base rate.

The `high` row is also a reminder to read small numbers carefully: 71 positive accounts leaves 14 in the test split, and the validation AUC during training sat near 0.77, so a single run at that size says "hard", not "0.56". For a paper, repeat over seeds and report the spread; the generator makes that cheap.

## What to try next

- Edge features. `SAGEConv` ignores `edge_attr`; a model that reads amounts, memos and `edge_time` (`GATv2Conv` with `edge_dim`, or a temporal model) has more to work with, and the transaction-level `y` lets it be trained on transactions rather than accounts.
- Temporal splits. `edge_time` supports training on the first weeks and testing on the last, which is how a detector is used.
- Scale. `--scale 0.01` is 100,000 accounts and a million transactions; full-batch training still fits in memory on a laptop, and `NeighborLoader` takes it further.
- The verifier does not apply to a `.pt` file, but the same dataset can be loaded into [Neo4j](neo4j.md), [LadybugDB](ladybug.md) or [DuckDB](duckdb.md) to look at what the model flagged.
