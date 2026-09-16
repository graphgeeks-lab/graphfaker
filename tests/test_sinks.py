"""Sinks: Neo4j admin import, LadybugDB/Kùzu, gen-fraud-graph compatibility."""

import csv

import polars as pl
import pytest

from graphfaker.domains import fraud, social
from graphfaker.engine import generate
from graphfaker.sinks import (
    infer_endpoints,
    ladybug_script,
    write_gen_fraud_graph,
    write_ladybug,
    write_neo4j_admin,
)
from graphfaker.sinks.ladybug import _driver


@pytest.fixture(scope="module")
def run():
    return fraud.generate(scale=0.0003, seed=5, hardness="low")


def test_endpoints_are_inferred(run):
    endpoints = infer_endpoints(run.tables)
    assert endpoints["OWNS"] == ("Customer", "Account")
    assert endpoints["PAYS"] == ("Account", "Merchant")
    assert endpoints["TRANSFERS"] == ("Account", "Account")
    assert endpoints["WIRES"] == ("Account", "Counterparty")


def test_neo4j_admin_layout(tmp_path, run):
    root = write_neo4j_admin(run.tables, tmp_path)
    with open(root / "nodes_Account.csv", newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert header[0] == "id:ID(Account)" and header[-1] == ":LABEL"
    assert "balance:double" in header and "opened_at:date" in header
    with open(root / "rels_TRANSFERS.csv", newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header, first = next(reader), next(reader)
    assert header[:2] == [":START_ID(Account)", ":END_ID(Account)"] and header[-1] == ":TYPE"
    assert first[-1] == "TRANSFERS"
    assert "timestamp:datetime" in header
    script = (root / "import.sh").read_text()
    assert "--nodes=Customer=nodes_Customer.csv" in script
    assert "--relationships=PAYS=rels_PAYS.csv" in script


def test_neo4j_admin_works_for_the_social_graph(tmp_path):
    social_run = generate(social.schema(60, 200), seed=1)
    root = write_neo4j_admin(social_run.tables, tmp_path)
    assert (root / "nodes_Person.csv").exists() and (root / "rels_FRIENDS_WITH.csv").exists()


def test_ladybug_script_declares_every_table(run):
    script = ladybug_script(run.tables, "data")
    assert "CREATE NODE TABLE `Account`(`id` STRING PRIMARY KEY" in script
    assert "CREATE REL TABLE `TRANSFERS`(FROM `Account` TO `Account`" in script
    assert "`timestamp` TIMESTAMP" in script and "`opened_at` DATE" in script
    assert "COPY `TRANSFERS` FROM \"" in script


def test_ladybug_write_without_driver(tmp_path, run):
    root = write_ladybug(run.tables, tmp_path)
    assert (root / "load.cypher").exists() and (root / "edges" / "PAYS.parquet").exists()


def test_ladybug_load(tmp_path, run):
    try:
        driver = _driver()
    except ImportError as exc:
        pytest.skip(str(exc))
    write_ladybug(run.tables, tmp_path / "data", db_path=tmp_path / "graph.db")
    conn = driver.Connection(driver.Database(str(tmp_path / "graph.db")))
    assert conn.execute("MATCH (a:Account) RETURN count(a)").get_next()[0] == run.tables.nodes["Account"].height
    assert conn.execute("MATCH ()-[t:TRANSFERS]->() RETURN count(t)").get_next()[0] == run.tables.edges["TRANSFERS"].height
    assert conn.execute("MATCH ()-[t:TRANSFERS]->() RETURN t.timestamp LIMIT 1").get_next()[0].year >= 2024


def test_gen_fraud_graph_layout(tmp_path, run):
    root = write_gen_fraud_graph(run, tmp_path)
    accounts = pl.read_csv(root / "accounts" / "accounts_0_0.csv")
    assert accounts.columns == ["account_id", "customer_name", "balance", "risk_score", "creation_date"]
    tx = pl.read_csv(root / "transactions" / "transactions_0_0.csv")
    assert tx.columns == ["tx_id", "src_id", "dst_id", "amount", "timestamp", "description"]
    fraud_tx = pl.read_csv(root / "fraud" / "transactions_fraud.csv")
    cases = pl.read_csv(root / "fraud" / "fraud_cases.csv")
    assert cases.columns == ["pattern_id", "start_acc_id", "pattern_type", "depth", "involved_accounts"]
    assert cases.height == run.truth["patterns"].filter(pl.col("is_fraud")).height
    truth_transfers = run.truth["transactions"].filter(pl.col("is_fraud")).join(run.tables.edges["TRANSFERS"], on="tx_id")
    assert fraud_tx.height == truth_transfers.height
    assert "|" in cases["involved_accounts"][0]
