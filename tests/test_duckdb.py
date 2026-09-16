"""The DuckDB sink: tables, the SQL/PGQ property graph, ground truth, verification.

These run wherever ``duckdb`` is installed. The property graph needs the
DuckPGQ community extension, which is downloaded on first use; the tests
that query through it skip when it cannot be installed (no network, or no
build for this DuckDB release).
"""

from __future__ import annotations

import polars as pl
import pytest
from typer.testing import CliRunner

from graphfaker.cli import app
from graphfaker.domains import fraud, social
from graphfaker.engine import generate
from graphfaker.sinks.duckdb import (
    DuckDBBackend,
    connect,
    duckdb_script,
    ensure_extension,
    execute_script,
    load_directory,
    load_tables,
    property_graph,
    verify_directory,
    write_duckdb,
)
from graphfaker.sinks.verify import MEMBER_REL, PATTERN_LABEL

duckdb = pytest.importorskip("duckdb")
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
    db = tmp_path / "bank.duckdb"
    report = load_directory(dataset, db)
    return db, report


@pytest.fixture(scope="module")
def pgq():
    """Skip the tests that need the extension when it is not available."""
    if not ensure_extension(duckdb.connect()):
        pytest.skip("duckpgq extension not available for this DuckDB")


# ------------------------------------------------------------------ script


def test_script_declares_truth_only_when_given(run, dataset):
    with_truth = duckdb_script(run.tables, dataset, run.truth, graph="fraud")
    blind = duckdb_script(run.tables, dataset)
    assert f'CREATE TABLE "{PATTERN_LABEL}"' in with_truth
    assert f'CREATE TABLE "{MEMBER_REL}" ("source" VARCHAR, "target" VARCHAR' in with_truth
    assert '"is_fraud" BOOLEAN' in with_truth and "UPDATE" in with_truth
    assert 'CREATE TABLE "Region"' in with_truth
    assert 'CREATE OR REPLACE PROPERTY GRAPH "fraud"' in with_truth
    assert f'"{MEMBER_REL}" SOURCE KEY ("source") REFERENCES "Account" ("id") DESTINATION KEY ("target") REFERENCES "{PATTERN_LABEL}" ("id")' in with_truth
    assert PATTERN_LABEL not in blind and "is_fraud" not in blind and "UPDATE" not in blind
    assert 'PROPERTY GRAPH "graph"' in blind


def test_property_graph_lists_every_table_with_its_endpoints(run):
    ddl = property_graph(run.tables, run.truth, "fraud")
    for node in run.tables.nodes:
        assert f'"{node}"' in ddl
    assert '"TRANSFERS" SOURCE KEY ("source") REFERENCES "Account" ("id") DESTINATION KEY ("target") REFERENCES "Account" ("id")' in ddl
    assert '"PAYS" SOURCE KEY ("source") REFERENCES "Account" ("id") DESTINATION KEY ("target") REFERENCES "Merchant" ("id")' in ddl


def test_the_script_and_the_in_memory_load_produce_the_same_database(run, dataset, tmp_path):
    """load.sql reads Parquet paths; load_tables registers Arrow tables. Both
    must land the same tables, so the script stays a faithful record."""
    script = duckdb_script(run.tables, dataset, run.truth)
    tables_only = script.split("INSTALL duckpgq")[0]  # the extension needs the network
    execute_script(connect(tmp_path / "script.duckdb"), tables_only)
    load_tables(run.tables, tmp_path / "memory.duckdb", run.truth)
    for db in ("script.duckdb", "memory.duckdb"):
        result = verify_directory(dataset, tmp_path / db)
        assert result.ok, result.summary()


# -------------------------------------------------------------------- load


def test_a_fresh_load_verifies(dataset, loaded):
    db, report = loaded
    assert report.truth[PATTERN_LABEL] == 33 and report.truth[MEMBER_REL] > 0
    result = verify_directory(dataset, db)
    assert result.ok, result.summary()
    assert len(result.checks) > 100
    names = [c.name for c in result.checks]
    assert "structure/constraints" in names and "structure/TRANSFERS.endpoints" in names


def test_truth_is_queryable_in_sql(run, loaded):
    db, _ = loaded
    conn = connect(db)
    flagged = conn.execute('SELECT count(*) FROM "TRANSFERS" WHERE is_fraud').fetchone()[0]
    expected = run.truth["transactions"].filter(pl.col("is_fraud")).join(run.tables.edges["TRANSFERS"], on="tx_id").height
    assert flagged == expected
    roles = conn.execute(
        f'SELECT m.role, count(*) FROM "{MEMBER_REL}" m JOIN "{PATTERN_LABEL}" p ON m.target = p.id '
        "WHERE p.typology = 'fan_in' AND p.is_fraud GROUP BY m.role ORDER BY m.role"
    ).fetchall()
    assert dict(roles).keys() == {"collector", "source"}


def test_truth_is_queryable_in_pgq(run, loaded, pgq):
    db, _ = loaded
    conn = connect(db)
    conn.execute("LOAD duckpgq")
    rows = conn.execute(
        "FROM GRAPH_TABLE (fraud MATCH (a:Account)-[t:TRANSFERS]->(b:Account) WHERE t.is_fraud COLUMNS (a.id AS payer)) SELECT count(*)"
    ).fetchone()
    expected = run.truth["transactions"].filter(pl.col("is_fraud")).join(run.tables.edges["TRANSFERS"], on="tx_id").height
    assert rows[0] == expected
    typologies = conn.execute(
        f"FROM GRAPH_TABLE (fraud MATCH (a:Account)-[m:{MEMBER_REL}]->(p:{PATTERN_LABEL}) WHERE p.is_fraud COLUMNS (p.typology)) "
        "SELECT DISTINCT typology"
    ).fetchall()
    assert {"fan_in", "cycle"} <= {row[0] for row in typologies}


def test_membership_is_one_row_per_truth_row(run, loaded):
    db, _ = loaded
    rels, accounts = connect(db).execute(f'SELECT count(*), count(DISTINCT source) FROM "{MEMBER_REL}"').fetchone()
    assert rels == run.truth["accounts"].height
    assert accounts == run.truth["accounts"]["account_id"].n_unique()


def test_blind_load_leaves_no_truth_in_the_database(dataset, tmp_path):
    db = tmp_path / "blind.duckdb"
    report = load_directory(dataset, db, truth=False)
    assert report.truth == {}
    conn = connect(db)
    tables = {row[0] for row in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    assert PATTERN_LABEL not in tables and "Region" not in tables and MEMBER_REL not in tables
    with pytest.raises(duckdb.Error):  # the column does not even exist
        conn.execute('SELECT count(*) FROM "TRANSFERS" WHERE is_fraud')
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
        ('DELETE FROM "TRANSFERS" WHERE rowid IN (SELECT rowid FROM "TRANSFERS" LIMIT 3)', "count/TRANSFERS"),
        ('UPDATE "Account" SET balance = NULL WHERE id IN (SELECT id FROM "Account" LIMIT 2)', "content/Account.present:balance"),
        ('UPDATE "PAYS" SET amount = amount + 1000 WHERE tx_id = (SELECT tx_id FROM "PAYS" LIMIT 1)', "content/PAYS.sum:amount"),
        (
            f'UPDATE "{PATTERN_LABEL}" SET is_fraud = false WHERE id = (SELECT id FROM "{PATTERN_LABEL}" WHERE is_fraud LIMIT 1)',
            f"truth/{PATTERN_LABEL}.is_fraud",
        ),
        ('UPDATE "TRANSFERS" SET is_fraud = false WHERE tx_id = (SELECT tx_id FROM "TRANSFERS" WHERE is_fraud LIMIT 1)', "truth/TRANSFERS.is_fraud"),
        ('INSERT INTO "TRANSFERS" SELECT * FROM "TRANSFERS" LIMIT 1', "structure/TRANSFERS.unique_tx_id"),
        ('UPDATE "TRANSFERS" SET target = \'acc_does_not_exist\' WHERE tx_id = (SELECT tx_id FROM "TRANSFERS" LIMIT 1)', "structure/TRANSFERS.endpoints"),
        ('UPDATE "Account" SET balance = balance * 3 WHERE id = (SELECT id FROM "Account" LIMIT 1)', "roundtrip/Account"),
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
    db = tmp_path / "social.duckdb"
    report = load_directory(tmp_path / "social", db)
    assert report.truth == {"Community": run.truth["community"].height}
    assert verify_directory(tmp_path / "social", db).ok


def test_write_duckdb_with_truth_at_generation_time(run, tmp_path):
    write_duckdb(run.tables, tmp_path / "data", db_path=tmp_path / "g.duckdb", truth=run.truth, graph="fraud")
    backend = DuckDBBackend(connect(tmp_path / "g.duckdb"), "g")
    assert backend.count_nodes(PATTERN_LABEL) == run.truth["patterns"].height
    assert backend.constraints([]) >= {"gf_Account_id", f"gf_{PATTERN_LABEL}_id"}
    assert (tmp_path / "data" / "load.sql").read_text().count("INSERT INTO") >= 8


# --------------------------------------------------------------------- CLI


def test_cli_load_and_verify(dataset, tmp_path):
    db = str(tmp_path / "cli.duckdb")
    result = runner.invoke(app, ["load", "duckdb", str(dataset), "--db", db])
    assert result.exit_code == 0, result.output
    assert "PASS:" in result.output and PATTERN_LABEL in result.output
    result = runner.invoke(app, ["verify", "duckdb", str(dataset), "--db", db, "--sample", "0"])
    assert result.exit_code == 0 and "PASS:" in result.output
    result = runner.invoke(app, ["load", "duckdb", str(dataset), "--db", db])
    assert result.exit_code == 1 and "already exists" in result.output


def test_cli_blind_load(dataset, tmp_path):
    db = str(tmp_path / "blind.duckdb")
    result = runner.invoke(app, ["load", "duckdb", str(dataset), "--db", db, "--blind"])
    assert result.exit_code == 0, result.output
    assert "truth          0" in result.output


def test_cli_generate_with_the_duckdb_sink(tmp_path):
    out = str(tmp_path / "social")
    result = runner.invoke(app, ["generate", "social", "--out", out, "--sink", "duckdb", "--seed", "3", "--total-nodes", "60", "--total-edges", "200"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "social" / "load.sql").exists() and (tmp_path / "social" / "graph.duckdb").exists()
    assert verify_directory(tmp_path / "social", tmp_path / "social" / "graph.duckdb").ok
