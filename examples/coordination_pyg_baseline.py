"""A GNN baseline on the synthetic platform: does structure find the campaigns?

Generates a platform at a chosen tradecraft level, exports it as a PyG
``HeteroData``, then trains two models on the Account labels and scores them on
held-out accounts:

* a logistic regression on the account features alone (no graph), and
* a two-layer heterogeneous GraphSAGE that also sees the neighbourhood.

The gap between them is what the graph is worth. Coordination should show a
larger gap than fraud does, because a campaign is *defined* by who acts with
whom: a lone account's own attributes barely say anything, which is why the
features-only baseline is the interesting control here.

A third number this reports that the fraud baseline does not: the share of
**organic** accounts flagged at the chosen operating point. Fan clubs and news
reactions are in the graph, labelled legitimate, and a model that finds
campaigns by firing on synchrony will take them with it. That column is the
deployment cost.

    python examples/coordination_pyg_baseline.py --scale 0.002 --tradecraft medium

Needs ``pip install "graphfaker[pyg]"``.
"""

from __future__ import annotations

import argparse
import time
import warnings

import numpy as np
import polars as pl
import torch
import torch.nn.functional as F
import torch_geometric.transforms as T
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from torch_geometric.nn import SAGEConv, to_hetero

from graphfaker.backends.tables import ID
from graphfaker.domains.coordination import generate
from graphfaker.sinks.pyg import to_hetero_data

warnings.filterwarnings("ignore")


class SAGE(torch.nn.Module):
    def __init__(self, hidden: int, out: int):
        super().__init__()
        self.conv1 = SAGEConv((-1, -1), hidden)
        self.conv2 = SAGEConv((-1, -1), out)

    def forward(self, x, edge_index):
        x = F.relu(self.conv1(x, edge_index))
        return self.conv2(x, edge_index)


def organic_flag_rate(
    probability: np.ndarray, organic: np.ndarray, positives: int
) -> float:
    """Share of organic accounts inside the model's top-``positives`` scores.

    A fixed operating point rather than a threshold, so the comparison between
    two models with different score distributions is fair: both are asked for
    as many accounts as there are real campaign members.
    """
    if not organic.any() or positives <= 0:
        return float("nan")
    flagged = np.zeros(len(probability), dtype=bool)
    flagged[np.argsort(-probability)[:positives]] = True
    return float(flagged[organic].mean())


def report(
    label: str,
    y: np.ndarray,
    probability: np.ndarray,
    test: np.ndarray,
    organic: np.ndarray,
) -> None:
    auc = roc_auc_score(y[test], probability[test])
    ap = average_precision_score(y[test], probability[test])
    organic_rate = organic_flag_rate(
        probability[test], organic[test], int(y[test].sum())
    )
    organic_text = "n/a" if np.isnan(organic_rate) else f"{organic_rate:6.1%}"
    print(f"  {label:<42}AUC {auc:.3f}  AP {ap:.3f}  organic flagged {organic_text}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scale", type=float, default=0.002)
    parser.add_argument(
        "--tradecraft", default="all", choices=["low", "medium", "high", "all"]
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--hidden", type=int, default=64)
    args = parser.parse_args()

    levels = (
        ["low", "medium", "high"] if args.tradecraft == "all" else [args.tradecraft]
    )
    for level in levels:
        torch.manual_seed(args.seed)
        run = generate(scale=args.scale, seed=args.seed, tradecraft=level)
        data = to_hetero_data(run.tables, run.truth, seed=args.seed)
        # Message passing needs both directions; the export keeps the dataset's.
        data = T.ToUndirected(merge=False)(data)
        account = data["Account"]
        y = account.y.numpy()
        train = account.train_mask.numpy()
        val = account.val_mask.numpy()
        test = account.test_mask.numpy()

        # Organic accounts: in a decoy structure and in no campaign. The export
        # already separates them, which is what `decoy` is for.
        organic = account.decoy.numpy().astype(bool)

        print(
            f"\ntradecraft={level}: {y.size:,} accounts, {int(y.sum())} in campaigns "
            f"({100 * y.mean():.2f}%), {int(organic.sum())} organic"
        )

        x = account.x.numpy()
        classifier = LogisticRegression(max_iter=2000, class_weight="balanced").fit(
            x[train], y[train]
        )
        report(
            "features only (logistic regression)",
            y,
            classifier.predict_proba(x)[:, 1],
            test,
            organic,
        )

        model = to_hetero(SAGE(args.hidden, 2), data.metadata(), aggr="sum")
        optimizer = torch.optim.Adam(model.parameters(), lr=0.005, weight_decay=5e-4)
        weight = torch.tensor(
            [1.0, float((y[train] == 0).sum() / max(1, (y[train] == 1).sum()))]
        )
        started = time.perf_counter()
        best_val, best_state = -1.0, None
        for _ in range(args.epochs):
            model.train()
            optimizer.zero_grad()
            out = model(data.x_dict, data.edge_index_dict)["Account"]
            loss = F.cross_entropy(
                out[account.train_mask], account.y[account.train_mask], weight=weight
            )
            loss.backward()
            optimizer.step()
            model.eval()
            with torch.no_grad():
                probability = (
                    F.softmax(model(data.x_dict, data.edge_index_dict)["Account"], dim=1)[:, 1]
                    .numpy()
                )
            val_auc = roc_auc_score(y[val], probability[val])
            if val_auc > best_val:
                best_val = val_auc
                best_state = {k: v.clone() for k, v in model.state_dict().items()}
        model.load_state_dict(best_state)
        model.eval()
        with torch.no_grad():
            probability = (
                F.softmax(model(data.x_dict, data.edge_index_dict)["Account"], dim=1)[:, 1]
                .numpy()
            )
        report("features plus graph (GraphSAGE)", y, probability, test, organic)
        print(f"  ({time.perf_counter() - started:.0f}s training)")


if __name__ == "__main__":
    main()
