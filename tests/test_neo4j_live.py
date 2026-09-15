"""The Bolt loader and its verifier.

The unit tests cover the query building and the report logic with no server.
The integration tests need a Neo4j and are marked ``neo4j``; they are skipped
unless one is reachable, and they run in their own database so they cannot
touch anything you care about::

    NEO4J_PASSWORD=... pytest -m neo4j

The interesting ones are the negative cases: a verifier that only ever
passes is not evidence of anything, so each class of silent corruption is
injected on purpose and the specific check that must catch it is named.
"""

from __future__ import annotations

import polars as pl
import pytest

from graphfaker.backends.tables import ID, GraphTables
from graphfaker.domains import fraud, social
from graphfaker.engine import generate
from graphfaker.sinks.neo4j_live import (
    MEMBER_REL,
    PATTERN_LABEL,
    Target,
    _edge_rows,
    _quote,
    load_tables,
)
from graphfaker.sinks.neo4j_verify import (
    Check,
    Verification,
    _equal,
    _truth_labels,
    verify_tables,
)

#: Neo4j rejects underscores in database names, hence the dash.
TEST_DATABASE = "graphfaker-pytest"


# ------------------------------------------------------------------- offline


def test_quote_escapes_backticks():
    assert _quote("Account") == "`Account`"
    assert _quote("Odd`Label") == "`Odd``Label`"


def test_edge_rows_carries_attributes_in_a_struct():
    frame = pl.DataFrame({"source": ["a"], "target": ["b"], "amount": [1.5]})
    rows, has_props = _edge_rows(frame)
    assert has_props
    assert rows.columns == ["source", "target", "props"]
    assert rows.to_dicts()[0]["props"] == {"amount": 1.5}


def test_edge_rows_without_attributes_emits_no_props():
    """Most social relationships have no properties, and polars has no empty
    struct; the loader has to leave the SET clause out instead."""
    frame = pl.DataFrame({"source": ["a"], "target": ["b"]})
    rows, has_props = _edge_rows(frame)
    assert not has_props
    assert rows.columns == ["source", "target"]


def test_target_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "neo4j://example:7687")
    monkeypatch.setenv("NEO4J_DATABASE", "bank")
    target = Target()
    assert target.uri == "neo4j://example:7687" and target.database == "bank"
    assert Target(database="other").database == "other"


def test_equal_tolerates_float_summation_order():
    assert _equal(1.0, 1.0 + 1e-12)
    assert not _equal(1.0, 1.01)
    assert _equal(["b", "a"], ["a", "b"])
    assert not _equal(None, 0.0)


def test_verification_reports_failures_only_unless_verbose():
    v = Verification(database="db")
    v.add("count/Account", 10, 10)
    v.add("count/Customer", 10, 9)
    assert not v.ok and len(v.failures) == 1
    assert "FAIL: 1/2 checks" in v.summary()
    assert "count/Account" not in v.summary()
    assert "count/Account" in v.summary(verbose=True)


def test_failed_check_line_states_both_sides():
    line = Check("count/Account", ok=False, expected=10, actual=9).line()
    assert "FAIL" in line and "expected 10" in line and "got 9" in line


def test_truth_labels_include_latent_factors():
    truth = {
        "patterns": pl.DataFrame({"pattern_id": ["p"]}),
        "accounts": pl.DataFrame({"account_id": ["a"]}),
        "region": pl.DataFrame({"group": [0]}),
    }
    assert _truth_labels(truth) == {PATTERN_LABEL, "Region"}
    assert _truth_labels(None) == set()


# --------------------------------------------------------------- integration


@pytest.fixture(scope="module")
def run():
    return fraud.generate(scale=0.0002, seed=3, hardness="low")


@pytest.fixture(scope="session")
def neo4j():
    """One connection and one throwaway database for the whole session.

    Skips rather than fails when there is no server, so `pytest` stays green
    on a machine that has never run Neo4j.
    """
    target = Target(database=TEST_DATABASE)
    try:
        driver = target.connect()
    except Exception as exc:  # unreachable, wrong password, driver missing
        pytest.skip(f"no Neo4j at {target.uri}: {exc}")
    from graphfaker.sinks.neo4j_live import create_database

    try:
        create_database(driver, TEST_DATABASE)
    except RuntimeError as exc:
        driver.close()
        pytest.skip(f"cannot create a test database: {exc}")
    try:
        yield driver, target
    finally:
        driver.close()


@pytest.fixture
def loaded(run, neo4j):
    """The fraud run and its truth, freshly loaded."""
    driver, target = neo4j
    load_tables(run.tables, target, run.truth, wipe_first=True, driver=driver)
    return driver, target


def _verify(run, driver, target, truth=True):
    return verify_tables(
        run.tables, target, run.truth if truth else None, sample=10, driver=driver
    )


@pytest.mark.neo4j
def test_a_fresh_load_verifies(run, loaded):
    driver, target = loaded
    result = _verify(run, driver, target)
    assert result.ok, result.summary()
    assert len(result.checks) > 50, "the report should be more than a couple of counts"


@pytest.mark.neo4j
def test_membership_is_one_relationship_per_truth_row(run, loaded):
    """Every row of ``truth/accounts.parquet`` becomes its own relationship,
    carrying the role the account plays in that pattern."""
    driver, target = loaded
    memberships = run.truth["accounts"]
    records, _, _ = driver.execute_query(
        f"MATCH (:Account)-[r:{MEMBER_REL}]->(:{PATTERN_LABEL}) "
        "RETURN count(r) AS rels, count(DISTINCT startNode(r)) AS accounts, "
        "count(r.role) AS roles",
        database_=target.database,
    )
    assert records[0]["rels"] == memberships.height
    assert records[0]["accounts"] == memberships["account_id"].n_unique()
    assert records[0]["roles"] == memberships.height, "the role must survive the load"


@pytest.mark.neo4j
def test_an_account_can_be_in_several_patterns(run, neo4j):
    """Why membership is a relationship and not a column on the account: at
    larger scales patterns share accounts, and flattening would lose rows.
    Asserted against a truth frame built to have an overlap, rather than
    hoping the fixture happens to contain one."""
    driver, target = neo4j
    account = run.tables.nodes["Account"][ID][0]
    truth = {
        "patterns": pl.DataFrame(
            {"pattern_id": ["pat_a", "pat_b"], "typology": ["cycle", "fan_in"], "is_fraud": [True, True]}
        ),
        "accounts": pl.DataFrame(
            {
                "account_id": [account, account],
                "pattern_id": ["pat_a", "pat_b"],
                "role": ["source", "collector"],
                "is_fraud": [True, True],
            }
        ),
    }
    load_tables(run.tables, target, truth, wipe_first=True, driver=driver)
    records, _, _ = driver.execute_query(
        f"MATCH (a:Account {{{ID}: $id}})-[r:{MEMBER_REL}]->(p:{PATTERN_LABEL}) "
        "RETURN collect(r.role) AS roles, collect(p.id) AS patterns",
        id=account,
        database_=target.database,
    )
    assert sorted(records[0]["roles"]) == ["collector", "source"]
    assert sorted(records[0]["patterns"]) == ["pat_a", "pat_b"]
    assert verify_tables(run.tables, target, truth, sample=5, driver=driver).ok


@pytest.mark.neo4j
def test_loading_twice_is_refused(run, loaded):
    """The loader uses CREATE, so a second pass would double the graph."""
    driver, target = loaded
    with pytest.raises(RuntimeError, match="already holds"):
        load_tables(run.tables, target, run.truth, driver=driver)


@pytest.mark.neo4j
@pytest.mark.parametrize(
    ("corruption", "check"),
    [
        ("MATCH ()-[r:TRANSFERS]->() WITH r LIMIT 3 DELETE r", "count/TRANSFERS"),
        ("MATCH (a:Account) WITH a LIMIT 2 REMOVE a.balance", "content/Account.present:balance"),
        ("MATCH ()-[r:PAYS]->() WITH r LIMIT 1 SET r.amount = r.amount + 1000", "content/PAYS.sum:amount"),
        ("MATCH (m:Merchant) WITH m LIMIT 1 REMOVE m:Merchant SET m:Customer", "structure/PAYS.endpoints"),
        ("MATCH (p:Pattern) WHERE p.is_fraud WITH p LIMIT 1 SET p.is_fraud = false", "truth/Pattern.is_fraud"),
        ("MATCH ()-[r:TRANSFERS]->() WHERE r.is_fraud WITH r LIMIT 1 SET r.is_fraud = false", "truth/TRANSFERS.is_fraud"),
    ],
)
def test_each_kind_of_silent_corruption_is_caught(run, loaded, corruption, check):
    """Break one thing, and assert the named check is the one that fails.
    Each case is a way a real load goes wrong without raising."""
    driver, target = loaded
    assert _verify(run, driver, target).ok, "the fixture must start clean"
    driver.execute_query(corruption, database_=target.database)
    result = _verify(run, driver, target)
    assert not result.ok
    assert check in [c.name for c in result.failures], (
        f"{check} should have caught it; failures were {[c.name for c in result.failures]}"
    )


@pytest.mark.neo4j
def test_blind_load_leaves_no_truth_in_the_graph(run, neo4j):
    """The point of --blind: a copy of the dataset a detector can be scored
    against honestly."""
    driver, target = neo4j
    report = load_tables(run.tables, target, truth=None, wipe_first=True, driver=driver)
    assert report.truth == {}
    records, _, _ = driver.execute_query(
        "MATCH (n) WHERE n:Pattern OR n:Region WITH count(n) AS nodes "
        "CALL () { MATCH ()-[r]->() WHERE r.is_fraud IS NOT NULL RETURN count(r) AS flagged } "
        "RETURN nodes, flagged",
        database_=target.database,
    )
    assert records[0]["nodes"] == 0 and records[0]["flagged"] == 0
    assert _verify(run, driver, target, truth=False).ok


@pytest.mark.neo4j
def test_social_graph_loads_and_verifies(neo4j):
    """A second domain, with many relationship types and most of them
    carrying no properties at all."""
    driver, target = neo4j
    social_run = generate(social.schema(400, 1200), seed=2)
    load_tables(social_run.tables, target, social_run.truth, wipe_first=True, driver=driver)
    result = verify_tables(social_run.tables, target, social_run.truth, sample=5, driver=driver)
    assert result.ok, result.summary()


@pytest.mark.neo4j
def test_round_trip_through_parquet_matches(run, neo4j, tmp_path):
    """Loading from a written directory must give the same graph as loading
    the in-memory run, which is what the documented workflow does."""
    driver, target = neo4j
    from graphfaker.sinks.neo4j_live import load_directory
    from graphfaker.sinks.neo4j_verify import verify_directory

    run.write(tmp_path)
    load_directory(tmp_path, target, wipe_first=True, driver=driver)
    assert verify_directory(tmp_path, target, sample=10, driver=driver).ok
    assert GraphTables.read_parquet(tmp_path).edge_count == run.tables.edge_count


def test_missing_driver_is_explained(monkeypatch):
    """The driver is an extra; the error has to say how to get it."""
    import builtins

    real_import = builtins.__import__

    def no_neo4j(name, *args, **kwargs):
        if name == "neo4j":
            raise ImportError("no module named neo4j")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_neo4j)
    with pytest.raises(ImportError, match=r"graphfaker\[neo4j\]"):
        Target().connect()


def test_illegal_database_names_are_rejected_before_a_round_trip():
    """Neo4j allows no underscores, which is the first thing anyone types."""
    from graphfaker.sinks.neo4j_live import check_database_name

    with pytest.raises(ValueError, match="no underscores"):
        check_database_name("fraud_data")
    with pytest.raises(ValueError):
        check_database_name("ab")
    check_database_name("fraud")
    check_database_name("graphfaker-pytest")
