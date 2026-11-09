"""Does the graph still help when the fraud hides? A study across hardness levels and seeds.

Extends ``pyg_baseline.py`` into the experiment a paper needs. For each hardness level and seed
it generates a bank, then trains three detectors on the same Account labels and split:

* **logistic** -- logistic regression on the account's own features (no graph, linear);
* **mlp** -- a two-layer MLP on the same features (no graph, non-linear), so the gain from
  the graph is not confused with the gain from a non-linear model;
* **graphsage** -- a two-layer heterogeneous GraphSAGE that also sees the neighbourhood;
* **edge_gnn** -- a two-layer heterogeneous GINE network that also reads every edge's attributes
  (timestamp, amount, recurring, memo);
* **edge_gnn_nomemo** -- the same without the memo. In graphfaker 1.4 the memo gave fraud away
  ("deposit" appeared only on fraud transfers); 1.5.0 fixed that, and this arm checks the fix:
  with and without the memo should now score about the same.

``--patterns-per-20k`` scales the fraud with the bank: that many patterns per typology for every
20,000 accounts (2 matches the prevalence of a 20,000-account bank). Without it, small banks
share a floor of 2 patterns per typology, so fraud gets rarer as the bank grows.

Beyond AUC and average precision, every detector is **priced**: the alert threshold is chosen
on the validation split to minimise ``missed launderers x miss_cost + false alerts``, a declared
policy (default: missing one account in a fraud pattern costs as much as 20 false alerts), and
that threshold is applied to the test split. Results go to JSON.

    python examples/gnn_hardness_study.py --scale 0.002 --seeds 1,2,3 --out gnn_hardness.json

Needs ``pip install "graphfaker[pyg]"``.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
import torch_geometric.transforms as T
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from torch_geometric.nn import GINEConv, HeteroConv, SAGEConv, to_hetero

from graphfaker.domains import fraud
from graphfaker.domains.fraud.config import TYPOLOGIES
from graphfaker.sinks.pyg import to_hetero_data


class SAGE(torch.nn.Module):
    def __init__(self, hidden: int, out: int):
        super().__init__()
        self.conv1 = SAGEConv((-1, -1), hidden)
        self.conv2 = SAGEConv((-1, -1), out)

    def forward(self, x, edge_index):
        return self.conv2(F.relu(self.conv1(x, edge_index)), edge_index)


class EdgeGNN(torch.nn.Module):
    """Project every node type to ``hidden``, then two GINE layers that read edge attributes."""

    def __init__(self, data, hidden: int, edge_dims: dict):
        super().__init__()
        self.proj = torch.nn.ModuleDict({
            nt: torch.nn.Linear(data[nt].x.shape[1], hidden) for nt in data.node_types})

        def layer():
            return HeteroConv({
                et: GINEConv(torch.nn.Sequential(torch.nn.Linear(hidden, hidden), torch.nn.ReLU(),
                                                 torch.nn.Linear(hidden, hidden)),
                             edge_dim=edge_dims[et])
                for et in data.edge_types}, aggr="sum")

        self.conv1, self.conv2 = layer(), layer()
        self.out = torch.nn.Linear(hidden, 2)

    def forward(self, x_dict, edge_index_dict, edge_attr_dict):
        h = {nt: F.relu(self.proj[nt](x)) for nt, x in x_dict.items()}
        h = {k: F.relu(v) for k, v in self.conv1(h, edge_index_dict, edge_attr_dict).items()}
        h = self.conv2(h, edge_index_dict, edge_attr_dict)
        return self.out(h["Account"])


def edge_features(data, drop_memo: bool) -> dict:
    """Standardised edge attributes per edge type, optionally without the memo columns."""
    out = {}
    for et in data.edge_types:
        store = data[et]
        names = list(getattr(store, "feature_names", []) or [])
        if not names and et[1].startswith("rev_"):
            names = list(getattr(data[(et[2], et[1][4:], et[0])], "feature_names", []) or [])
        x = store.edge_attr.float()
        if drop_memo and names:
            keep = [i for i, n in enumerate(names) if not n.startswith("memo")]
            x = x[:, keep]
        out[et] = (x - x.mean(dim=0)) / (x.std(dim=0) + 1e-6)
    return out


def priced(y_val, p_val, y_test, p_test, miss_cost: float) -> dict:
    """Pick the alert threshold on validation by declared cost; report it on test."""
    grid = np.unique(np.quantile(p_val, np.linspace(0, 1, 401)))

    def cost(y, p, t):
        alert = p >= t
        return float(miss_cost * np.sum(~alert & (y == 1)) + np.sum(alert & (y == 0)))

    t = min(grid, key=lambda g: cost(y_val, p_val, g))
    alert = p_test >= t
    return {"threshold": float(t), "missed": int(np.sum(~alert & (y_test == 1))),
            "false_alerts": int(np.sum(alert & (y_test == 0))),
            "cost": cost(y_test, p_test, t)}


MISS_COSTS = (5.0, 10.0, 20.0, 50.0, 100.0)  # a curve, not one weight


def summary(name, y, p, val, test, miss_cost, seconds) -> dict:
    return {"model": name, "auc": float(roc_auc_score(y[test], p[test])),
            "ap": float(average_precision_score(y[test], p[test])), "seconds": round(seconds, 1),
            **priced(y[val], p[val], y[test], p[test], miss_cost),
            "by_miss_cost": {str(c): priced(y[val], p[val], y[test], p[test], c)
                             for c in MISS_COSTS},
            "alert_nothing_cost": {str(c): float(c * y[test].sum()) for c in MISS_COSTS}}


def train_torch(model, forward, y_t, masks, weight, epochs, lr) -> np.ndarray:
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=5e-4)
    train, val = masks
    y = y_t.numpy()
    best_val, best_state = -1.0, None
    for _ in range(epochs):
        model.train()
        optimizer.zero_grad()
        loss = F.cross_entropy(forward()[train], y_t[train], weight=weight)
        loss.backward()
        optimizer.step()
        model.eval()
        with torch.no_grad():
            prob = F.softmax(forward(), dim=1)[:, 1].numpy()
        auc = roc_auc_score(y[val.numpy()], prob[val.numpy()])
        if auc > best_val:
            best_val, best_state = auc, {k: v.clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        return F.softmax(forward(), dim=1)[:, 1].numpy()


def one(scale: float, hardness: str, seed: int, miss_cost: float, epochs: int,
        hidden: int, per_20k: float | None = None, models: tuple[str, ...] = ()) -> list[dict]:
    torch.manual_seed(seed)
    kwargs = {}
    if per_20k:
        accounts = fraud.FraudConfig(scale=scale).num_accounts
        n = max(1, round(per_20k * accounts / 20_000))
        kwargs["patterns"] = {t: n for t in TYPOLOGIES}
    run = fraud.generate(scale=scale, seed=seed, hardness=hardness, **kwargs)
    data = T.ToUndirected(merge=False)(to_hetero_data(run.tables, run.truth, seed=seed))
    acc = data["Account"]
    y = acc.y.numpy()
    tr, va, te = acc.train_mask.numpy(), acc.val_mask.numpy(), acc.test_mask.numpy()
    x = acc.x.numpy()
    weight = torch.tensor([1.0, float((y[tr] == 0).sum() / max(1, (y[tr] == 1).sum()))])
    out = []

    started = time.perf_counter()
    clf = LogisticRegression(max_iter=2000, class_weight="balanced").fit(x[tr], y[tr])
    out.append(summary("logistic", y, clf.predict_proba(x)[:, 1], va, te, miss_cost,
                       time.perf_counter() - started))

    started = time.perf_counter()
    xt = torch.tensor(x, dtype=torch.float32)
    mlp = torch.nn.Sequential(torch.nn.Linear(x.shape[1], hidden), torch.nn.ReLU(),
                              torch.nn.Linear(hidden, hidden), torch.nn.ReLU(),
                              torch.nn.Linear(hidden, 2))
    p = train_torch(mlp, lambda: mlp(xt), acc.y, (acc.train_mask, acc.val_mask), weight,
                    epochs * 3, 0.005)
    out.append(summary("mlp", y, p, va, te, miss_cost, time.perf_counter() - started))

    if "graphsage" in models:
        started = time.perf_counter()
        gnn = to_hetero(SAGE(hidden, 2), data.metadata(), aggr="sum")
        p = train_torch(gnn, lambda: gnn(data.x_dict, data.edge_index_dict)["Account"], acc.y,
                        (acc.train_mask, acc.val_mask), weight, epochs, 0.005)
        out.append(summary("graphsage", y, p, va, te, miss_cost, time.perf_counter() - started))
    for name, drop in (("edge_gnn", False), ("edge_gnn_nomemo", True)):
        if name not in models:
            continue
        started = time.perf_counter()
        edges = edge_features(data, drop_memo=drop)
        net = EdgeGNN(data, hidden, {et: e.shape[1] for et, e in edges.items()})
        p = train_torch(net, lambda net=net, edges=edges: net(data.x_dict, data.edge_index_dict,
                                                               edges),
                        acc.y, (acc.train_mask, acc.val_mask), weight, epochs, 0.005)
        out.append(summary(name, y, p, va, te, miss_cost, time.perf_counter() - started))
    base = {"scale": scale, "hardness": hardness, "seed": seed, "accounts": int(y.size),
            "fraud_accounts": int(y.sum()), "test_fraud": int(y[te].sum())}
    return [{**base, **r} for r in out]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scale", type=float, default=0.002)
    ap.add_argument("--hardness", default="low,medium,high")
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--miss-cost", type=float, default=20.0)
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--patterns-per-20k", type=float, default=None)
    ap.add_argument("--models", default="graphsage,edge_gnn,edge_gnn_nomemo")
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--out", type=Path, default=Path("gnn_hardness.json"))
    args = ap.parse_args()
    if args.threads:
        torch.set_num_threads(args.threads)
    rows = []
    for hardness in args.hardness.split(","):
        for seed in (int(s) for s in args.seeds.split(",")):
            for r in one(args.scale, hardness, seed, args.miss_cost, args.epochs, args.hidden,
                         args.patterns_per_20k, tuple(args.models.split(","))):
                rows.append(r)
                print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v)
                                  for k, v in r.items() if not isinstance(v, dict)}), flush=True)
    args.out.write_text(json.dumps({"miss_cost": args.miss_cost,
                                    "patterns_per_20k": args.patterns_per_20k, "rows": rows},
                                   indent=1),
                        encoding="utf-8")


if __name__ == "__main__":
    main()
