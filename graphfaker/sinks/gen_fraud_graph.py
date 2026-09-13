"""gen-fraud-graph compatible output.

Writes the ``accounts/``, ``transactions/``, ``fraud/`` layout that
SantanderAI/gen-fraud-graph produces, so pipelines built on it can switch
generators without changing a loader. Only account-to-account transfers fit
that schema; card payments, wires and the customer/device layers are not
representable there and are left to the native sinks.

Columns without a counterpart are filled sensibly: ``risk_score`` is derived
from the owner's KYC tier; ``embedding`` is omitted (gen-fraud-graph itself
makes it optional).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from graphfaker.engine.run import GraphRun

RISK_BY_KYC_TIER = {1: 0.7, 2: 0.4, 3: 0.2}


def write_gen_fraud_graph(run: GraphRun, directory: str | Path) -> Path:
    root = Path(directory)
    for sub in ("accounts", "transactions", "fraud"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    accounts = run.tables.nodes["Account"]
    customers = run.tables.nodes["Customer"].select(["id", "name", "kyc_tier"]).rename({"id": "customer"})
    accounts.join(customers, on="customer", how="left").select(
        pl.col("id").alias("account_id"),
        pl.col("name").alias("customer_name"),
        pl.col("balance"),
        pl.col("kyc_tier").replace_strict(RISK_BY_KYC_TIER, default=0.5).alias("risk_score"),
        pl.col("opened_at").cast(pl.String).alias("creation_date"),
    ).write_csv(root / "accounts" / "accounts_0_0.csv")

    transfers = run.tables.edges["TRANSFERS"]
    tx_truth = run.truth["transactions"]
    fraud_ids = tx_truth.filter(pl.col("is_fraud"))["tx_id"]
    shaped = transfers.select(
        pl.col("tx_id"),
        pl.col("source").alias("src_id"),
        pl.col("target").alias("dst_id"),
        pl.col("amount"),
        pl.col("timestamp").dt.strftime("%Y-%m-%dT%H:%M:%S"),
        pl.col("memo").alias("description"),
    )
    is_fraud = shaped["tx_id"].is_in(fraud_ids.implode())
    shaped.filter(~is_fraud).write_csv(root / "transactions" / "transactions_0_0.csv")
    shaped.filter(is_fraud).write_csv(root / "fraud" / "transactions_fraud.csv")

    patterns = run.truth["patterns"].filter(pl.col("is_fraud"))
    patterns.select(
        pl.col("pattern_id"),
        pl.col("accounts").list.first().alias("start_acc_id"),
        pl.col("typology").alias("pattern_type"),
        pl.col("n_accounts").alias("depth"),
        pl.col("accounts").list.join("|").alias("involved_accounts"),
    ).write_csv(root / "fraud" / "fraud_cases.csv")
    return root
