"""A tour of the fraud pack from a script: generate, explore, draw a pattern,
measure hardness, score a naive detector, write, read back, query.

    python examples/fraud_tour.py --scale 0.002 --hardness medium --out ./fraud_tour

The notebook version with commentary and charts is
``docs/notebooks/graphfaker_tour.ipynb``. Needs ``pip install graphfaker[examples]``.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import matplotlib
import networkx as nx
import polars as pl

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from graphfaker import GraphTables, Manifest
from graphfaker.domains import fraud
from graphfaker.domains.fraud.evaluate import evaluate
from graphfaker.domains.fraud.hardness import (
    hardness_report,
    realism_report,
)
from graphfaker.logger import logger
from graphfaker.sinks import write_ladybug, write_neo4j_admin

LEGIT, FRAUD, ACCENT = "#9aa5b1", "#d64545", "#2a6f97"


def draw_pattern(run, pattern_id: str, path: Path) -> None:
    """The pattern's own transactions in red, the members' other activity in grey."""
    G = run.to_networkx()
    patterns = run.truth["patterns"]
    row = patterns.filter(pl.col("pattern_id") == pattern_id).row(0, named=True)
    members = set(row["accounts"])
    tx = pl.concat([run.tables.edges[c] for c in ("PAYS", "TRANSFERS", "WIRES")])
    own = tx.join(run.truth["transactions"].filter(pl.col("pattern_id") == pattern_id), on="tx_id")
    own_edges = set(zip(own["source"].to_list(), own["target"].to_list()))
    neighbours = {
        n for m in members for n in list(G.predecessors(m)) + list(G.successors(m)) if G.nodes[n]["type"] == "Account"
    }
    S = G.subgraph(members | set(list(neighbours)[:60]))
    pos = nx.spring_layout(S, seed=3, k=0.9)
    fig, ax = plt.subplots(figsize=(7, 6))
    nx.draw_networkx_edges(S, pos, edgelist=[e for e in S.edges() if e not in own_edges], ax=ax, edge_color=LEGIT, alpha=0.5, arrows=False)
    nx.draw_networkx_edges(S, pos, edgelist=[e for e in S.edges() if e in own_edges], ax=ax, edge_color=FRAUD, width=2, arrowsize=12)
    nx.draw_networkx_nodes(S, pos, nodelist=[n for n in S if n not in members], ax=ax, node_color=LEGIT, node_size=40, linewidths=0)
    nx.draw_networkx_nodes(S, pos, nodelist=list(members), ax=ax, node_color=FRAUD, node_size=140, linewidths=0)
    ax.set_title(f"{pattern_id}: {row['typology']} — {len(members)} accounts, {row['n_transactions']} transactions")
    ax.axis("off")
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scale", type=float, default=0.002, help="1.0 = ~10M accounts / ~90M transactions")
    parser.add_argument("--hardness", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--out", default="fraud_tour")
    args = parser.parse_args()
    logger.setLevel(logging.WARNING)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # 1. Generate
    run = fraud.generate(scale=args.scale, hardness=args.hardness, seed=args.seed, workers=args.workers)
    print("nodes:", run.manifest.node_counts)
    print("edges:", run.manifest.edge_counts)

    # 2. Explore with Polars
    tx = pl.concat([run.tables.edges[c].with_columns(pl.lit(c).alias("channel")) for c in ("PAYS", "TRANSFERS", "WIRES")])
    print("\nby channel:")
    print(
        tx.group_by("channel").agg(
            pl.len().alias("transactions"),
            pl.col("recurring").mean().round(3).alias("recurring_share"),
            pl.col("amount").median().round(2).alias("median_amount"),
        ).sort("transactions", descending=True)
    )
    print("\nrealism:", {k: round(v, 3) for k, v in realism_report(run).items()})

    # 3. Ground truth, drawn
    patterns = run.truth["patterns"]
    print("\npatterns:")
    print(patterns.group_by(["typology", "is_fraud"]).len().sort(["typology", "is_fraud"]))
    for typology in ("fan_in", "cycle", "mule_network"):
        pid = patterns.filter((pl.col("typology") == typology) & pl.col("is_fraud"))["pattern_id"][0]
        draw_pattern(run, pid, out / f"pattern_{typology}.png")
    print(f"\ndrew patterns to {out}/pattern_*.png")

    # 4. Hardness
    print("\n" + hardness_report(run).summary())

    # 5. A naive detector, scored
    transfers = run.tables.edges["TRANSFERS"]
    hubs = transfers.group_by("target").agg(pl.col("source").n_unique().alias("k")).filter(pl.col("k") >= 6)["target"].to_list()
    print("\nnaive detector (>= 6 distinct senders):")
    print(evaluate(run, hubs, ring_threshold=0.5).summary())

    # 6. Write, read back
    run.write(out / "data")
    tables = GraphTables.read_parquet(out / "data")
    manifest = Manifest.read(out / "data" / "manifest.json")
    print(f"\nwrote {out / 'data'}: {tables.node_count} nodes, {tables.edge_count} edges, seed {manifest.seed}")

    # 7. Sinks: Neo4j admin import files; LadybugDB/Kùzu database when a driver is installed
    write_neo4j_admin(tables, out / "neo4j")
    try:
        write_ladybug(tables, out / "data", db_path=out / "graph.db")
        print(f"loaded {out / 'graph.db'} — query it with Cypher (see the notebook)")
    except ImportError as exc:
        print(f"wrote {out / 'data' / 'load.cypher'}; {exc}")


if __name__ == "__main__":
    main()
