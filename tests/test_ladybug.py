"""The LadybugDB / Kùzu sink: load, ground truth, verification.

These run wherever the driver is installed (``pip install kuzu`` or
``ladybug``); the database is embedded, so there is nothing to start.
"""

from __future__ import annotations

import polars as pl
import pytest
from typer.testing import CliRunner

from graphfaker.cli import app
from graphfaker.domains import fraud, social
from graphfaker.engine import generate
from graphfaker.sinks.ladybug import (
    LadybugBackend,
    connect,
    execute_script,
    ladybug_script,
    load_directory,
    load_tables,
    verify_directory,
    write_ladybug,
)
from graphfaker.sinks.verify import MEMBER_REL, PATTERN_LABEL

driver = pytest.importorskip("kuzu")
runner = CliRunner()


@pytest.fixture(scope="module")
def run():
    return fraud.generate(scale=0.0005, seed=11, hardness="medium")


@pytest.fixture(scope="module")
def dataset(run, tmp_path_factory):
    root = tmp_path_factory.mktemp("bank")
    run.write(root)
    return root


@pytest.fixture
def loaded(dataset, tmp_path):
    db = tmp_path / "bank.lbdb"
    report = load_directory(dataset, db)
    return db, report


# ------------------------------------------------------------------ script


def test_script_declares_truth_only_when_given(run, dataset):
    with_truth = ladybug_script(run.tables, dataset, run.truth)
    blind = ladybug_script(run.tables, dataset)
    assert f"CREATE NODE TABLE `{PATTERN_LABEL}`" in with_truth
    assert f"CREATE REL TABLE `{MEMBER_REL}`(FROM `Account` TO `{PATTERN_LABEL}`" in with_truth
    assert "`is_fraud` BOOLEAN" in with_truth and "LOAD FROM" in with_truth
    assert "CREATE NODE TABLE `Region`" in with_truth
    assert PATTERN_LABEL not in blind and "is_fraud" not in blind and "LOAD FROM" not in blind


def test_the_script_and_the_in_memory_load_produce_the_same_database(run, dataset, tmp_path):
    """load.cypher reads Parquet paths; load_tables binds Arrow tables. Both
    must land the same graph, so the script stays a faithful record."""
    execute_script(connect(tmp_path / "script.lbdb"), ladybug_script(run.tables, dataset, run.truth))
    load_tables(run.tables, tmp_path / "memory.lbdb", run.truth)
    for db in ("script.lbdb", "memory.lbdb"):
        assert verify_directory(dataset, tmp_path / db).ok


# -------------------------------------------------------------------- load


def test_a_fresh_load_verifies(dataset, loaded):
    db, report = loaded
    assert report.truth[PATTERN_LABEL] == 33 and report.truth[MEMBER_REL] > 0
    result = verify_directory(dataset, db)
    assert result.ok, result.summary()
    assert len(result.checks) > 100


def test_truth_is_queryable_in_cypher(run, loaded):
    db, _ = loaded
    conn = connect(db)
    flagged = conn.execute("MATCH ()-[r:TRANSFERS]->() WHERE r.is_fraud RETURN count(r)").get_next()[0]
    expected = run.truth["transactions"].filter(pl.col("is_fraud")).join(run.tables.edges["TRANSFERS"], on="tx_id").height
    assert flagged == expected
    roles = conn.execute(
        f"MATCH (a:Account)-[m:{MEMBER_REL}]->(p:{PATTERN_LABEL}) WHERE p.typology = 'fan_in' AND p.is_fraud "
        "RETURN m.role, count(*) ORDER BY m.role"
    ).get_all()
    assert dict(roles).keys() == {"collector", "source"}


def test_membership_is_one_relationship_per_truth_row(run, loaded):
    db, _ = loaded
    conn = connect(db)
    rels, accounts = conn.execute(
        f"MATCH (a:Account)-[m:{MEMBER_REL}]->(:{PATTERN_LABEL}) RETURN count(m), count(DISTINCT a)"
    ).get_next()
    assert rels == run.truth["accounts"].height
    assert accounts == run.truth["accounts"]["account_id"].n_unique()


def test_blind_load_leaves_no_truth_in_the_graph(dataset, tmp_path):
    db = tmp_path / "blind.lbdb"
    report = load_directory(dataset, db, truth=False)
    assert report.truth == {}
    tables = {row[1] for row in connect(db).execute("CALL show_tables() RETURN *").get_all()}
    assert PATTERN_LABEL not in tables and "Region" not in tables and MEMBER_REL not in tables
    with pytest.raises(RuntimeError):  # the column does not even exist
        connect(db).execute("MATCH ()-[r:TRANSFERS]->() WHERE r.is_fraud RETURN count(r)")
    assert verify_directory(dataset, db, truth=False).ok


def test_loading_onto_an_existing_database_is_refused_unless_wiped(dataset, loaded):
    db, _ = loaded
    with pytest.raises(RuntimeError, match="already exists"):
        load_directory(dataset, db)
    report = load_directory(dataset, db, wipe_first=True)
    assert report.nodes["Account"] == 5000
    assert verify_directory(dataset, db).ok


@pytest.mark.parametrize(
    ("corruption", "check"),
    [
        ("MATCH ()-[r:TRANSFERS]->() WITH r LIMIT 3 DELETE r", "count/TRANSFERS"),
        ("MATCH (a:Account) WITH a LIMIT 2 SET a.balance = NULL", "content/Account.present:balance"),
        ("MATCH ()-[r:PAYS]->() WITH r LIMIT 1 SET r.amount = r.amount + 1000", "content/PAYS.sum:amount"),
        (f"MATCH (p:{PATTERN_LABEL}) WHERE p.is_fraud WITH p LIMIT 1 SET p.is_fraud = false", f"truth/{PATTERN_LABEL}.is_fraud"),
        ("MATCH ()-[r:TRANSFERS]->() WHERE r.is_fraud WITH r LIMIT 1 SET r.is_fraud = false", "truth/TRANSFERS.is_fraud"),
        (
            "MATCH (a:Account)-[r:TRANSFERS]->(b:Account) WITH a, b, r LIMIT 1 "
            "CREATE (a)-[:TRANSFERS {tx_id: r.tx_id, timestamp: r.timestamp, amount: r.amount, memo: r.memo, recurring: r.recurring}]->(b)",
            "structure/TRANSFERS.unique_tx_id",
        ),
        ("MATCH (a:Account) WITH a LIMIT 1 SET a.balance = a.balance * 3", "roundtrip/Account"),
    ],
)
def test_each_kind_of_silent_corruption_is_caught(dataset, loaded, corruption, check):
    """Break one thing, and assert the named check is the one that fails."""
    db, _ = loaded
    assert verify_directory(dataset, db).ok, "the fixture must start clean"
    connect(db).execute(corruption)
    result = verify_directory(dataset, db, sample=5000)
    assert not result.ok
    assert check in [c.name for c in result.failures], (
        f"{check} should have caught it; failures were {[c.name for c in result.failures]}"
    )


def test_social_graph_loads_with_its_community_truth(tmp_path):
    run = generate(social.schema(80, 300), seed=1)
    run.write(tmp_path / "social")
    db = tmp_path / "social.lbdb"
    report = load_directory(tmp_path / "social", db)
    assert report.truth == {"Community": run.truth["community"].height}
    assert verify_directory(tmp_path / "social", db).ok


def test_write_ladybug_with_truth_at_generation_time(run, tmp_path):
    write_ladybug(run.tables, tmp_path / "data", db_path=tmp_path / "g.lbdb", truth=run.truth)
    backend = LadybugBackend(connect(tmp_path / "g.lbdb"), "g")
    assert backend.count_nodes(PATTERN_LABEL) == run.truth["patterns"].height
    assert (tmp_path / "data" / "load.cypher").read_text().count("LOAD FROM") >= 3


# --------------------------------------------------------------------- CLI


def test_cli_load_and_verify(dataset, tmp_path):
    db = str(tmp_path / "cli.lbdb")
    result = runner.invoke(app, ["load", "ladybug", str(dataset), "--db", db])
    assert result.exit_code == 0, result.output
    assert "PASS:" in result.output and PATTERN_LABEL in result.output
    result = runner.invoke(app, ["verify", "ladybug", str(dataset), "--db", db, "--sample", "0"])
    assert result.exit_code == 0 and "PASS:" in result.output
    result = runner.invoke(app, ["load", "ladybug", str(dataset), "--db", db])
    assert result.exit_code == 1 and "already exists" in result.output


def test_cli_blind_load(dataset, tmp_path):
    db = str(tmp_path / "blind.lbdb")
    result = runner.invoke(app, ["load", "ladybug", str(dataset), "--db", db, "--blind"])
    assert result.exit_code == 0, result.output
    assert "truth          0" in result.output
