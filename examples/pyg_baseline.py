"""A GNN baseline on the synthetic bank: does structure help find the fraud?

Generates a bank at a chosen hardness, exports it as a PyG ``HeteroData``,
then trains two models on the Account labels and scores them on the held-out
accounts:

* a logistic regression on the account features alone (no graph), and
* a two-layer heterogeneous GraphSAGE that also sees the neighbourhood.

The gap between the two is what the graph is worth to a detector, and how
that gap moves across ``--hardness low|medium|high`` is the number a paper
about this dataset wants. Run::

    python examples/pyg_baseline.py --scale 0.002 --hardness medium --seed 42

Needs ``pip install "graphfaker[pyg]"``.
"""

from __future__ import annotations

import argparse
import time

import numpy as np
import torch
import torch.nn.functional as F
import torch_geometric.transforms as T
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from torch_geometric.nn import SAGEConv, to_hetero

from graphfaker.domains import fraud
from graphfaker.sinks.pyg import to_hetero_data


class SAGE(torch.nn.Module):
    def __init__(self, hidden: int, out: int):
        super().__init__()
        self.conv1 = SAGEConv((-1, -1), hidden)
        self.conv2 = SAGEConv((-1, -1), out)

    def forward(self, x, edge_index):
        x = F.relu(self.conv1(x, edge_index))
        return self.conv2(x, edge_index)


def scores(y_true: np.ndarray, y_score: np.ndarray) -> str:
    return f"AUC {roc_auc_score(y_true, y_score):.3f}  AP {average_precision_score(y_true, y_score):.3f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scale", type=float, default=0.002)
    parser.add_argument("--hardness", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--hidden", type=int, default=64)
    args = parser.parse_args()
    torch.manual_seed(args.seed)

    run = fraud.generate(scale=args.scale, seed=args.seed, hardness=args.hardness)
    data = to_hetero_data(run.tables, run.truth, seed=args.seed)
    # message passing needs both directions; the export keeps the dataset's
    data = T.ToUndirected(merge=False)(data)
    account = data["Account"]
    y = account.y.numpy()
    train, val, test = account.train_mask.numpy(), account.val_mask.numpy(), account.test_mask.numpy()
    print(f"{y.size:,} accounts, {int(y.sum())} in fraud patterns ({100 * y.mean():.2f}%), hardness={args.hardness}")

    # 1. features only, no graph
    x = account.x.numpy()
    clf = LogisticRegression(max_iter=2000, class_weight="balanced").fit(x[train], y[train])
    print(f"features only (logistic regression): {scores(y[test], clf.predict_proba(x[test])[:, 1])}")

    # 2. features plus neighbourhood
    model = to_hetero(SAGE(args.hidden, 2), data.metadata(), aggr="sum")
    optimizer = torch.optim.Adam(model.parameters(), lr=0.005, weight_decay=5e-4)
    weight = torch.tensor([1.0, float((y[train] == 0).sum() / max(1, (y[train] == 1).sum()))])
    started = time.perf_counter()
    best_val, best_state = -1.0, None
    for epoch in range(1, args.epochs + 1):
        model.train()
        optimizer.zero_grad()
        out = model(data.x_dict, data.edge_index_dict)["Account"]
        loss = F.cross_entropy(out[account.train_mask], account.y[account.train_mask], weight=weight)
        loss.backward()
        optimizer.step()
        model.eval()
        with torch.no_grad():
            prob = F.softmax(model(data.x_dict, data.edge_index_dict)["Account"], dim=1)[:, 1].numpy()
        val_auc = roc_auc_score(y[val], prob[val])
        if val_auc > best_val:
            best_val, best_state = val_auc, {k: v.clone() for k, v in model.state_dict().items()}
        if epoch % 10 == 0:
            print(f"  epoch {epoch:3d}  loss {loss.item():.3f}  val AUC {val_auc:.3f}")
    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        prob = F.softmax(model(data.x_dict, data.edge_index_dict)["Account"], dim=1)[:, 1].numpy()
    print(f"features plus graph (GraphSAGE): {scores(y[test], prob[test])}  ({time.perf_counter() - started:.0f}s)")


if __name__ == "__main__":
    main()
