"""The truth layout, and the databases reading it in a domain's own words.

A pack names its truth after its own subject: the fraud pack writes
``patterns`` with ``pattern_id``, ``typology`` and ``is_fraud``, the
coordination pack writes ``campaigns`` with ``campaign_id``, ``playbook`` and
``is_coordinated``. The sinks used to read those names literally, which meant
a coordination dataset could not be loaded into DuckDB or LadybugDB at all,
and loaded into Neo4j with its ground truth silently missing. These tests are
the ones that would have caught it.
"""

from __future__ import annotations

import polars as pl
import pytest

from graphfaker.backends.tables import GraphTables
from graphfaker.sinks.truth import MEMBER_REL, PATTERN_LABEL, layout

BANK = {
    "patterns": pl.DataFrame(
        {"pattern_id": ["pat_0"], "typology": ["fan_in"], "is_fraud": [True],
         "accounts": [["acc_1"]], "roles": [["collector"]]}
    ),
    "accounts": pl.DataFrame(
        {"account_id": ["acc_1"], "pattern_id": ["pat_0"], "typology": ["fan_in"],
         "role": ["collector"], "is_fraud": [True]}
    ),
    "transactions": pl.DataFrame(
        {"tx_id": ["tx_1"], "pattern_id": ["pat_0"], "typology": ["fan_in"], "is_fraud": [True]}
    ),
    "region": pl.DataFrame({"group": [0], "log_income": [10.2]}),
}

PLATFORM = {
    "campaigns": pl.DataFrame(
        {"campaign_id": ["copypasta_0"], "playbook": ["copypasta"], "is_coordinated": [True],
         "accounts": [["acc_1"]], "roles": [["poster"]]}
    ),
    "accounts": pl.DataFrame(
        {"account_id": ["acc_1"], "campaign_id": ["copypasta_0"], "playbook": ["copypasta"],
         "role": ["poster"], "is_coordinated": [True]}
    ),
    "events": pl.DataFrame(
        {"event_id": ["ev_1"], "campaign_id": ["copypasta_0"], "playbook": ["copypasta"],
         "is_coordinated": [True]}
    ),
    "community": pl.DataFrame({"group": [0], "log_activity": [1.5]}),
}


def test_both_vocabularies_resolve_to_the_same_layout():
    bank, platform = layout(BANK), layout(PLATFORM)
    assert (bank.file("patterns"), bank.pattern_id) == ("patterns.parquet", "pattern_id")
    assert (platform.file("patterns"), platform.pattern_id) == ("campaigns.parquet", "campaign_id")
    assert (bank.file("events"), bank.event_id) == ("transactions.parquet", "tx_id")
    assert (platform.file("events"), platform.event_id) == ("events.parquet", "event_id")
    assert (bank.member_id, platform.member_id) == ("account_id", "account_id")
    assert (bank.label, platform.label) == ("is_fraud", "is_coordinated")
    assert (list(bank.latent), list(platform.latent)) == (["Region"], ["Community"])


def test_event_columns_are_what_gets_copied_onto_the_edges():
    # Everything except the id used to find the edge, in the domain's words.
    assert layout(BANK).event_columns == ["pattern_id", "typology", "is_fraud"]
    assert layout(PLATFORM).event_columns == ["campaign_id", "playbook", "is_coordinated"]


def test_a_run_without_patterns_has_an_empty_layout():
    empty = layout(None)
    assert not (empty.has_patterns or empty.has_members or empty.has_events)
    assert layout({"region": BANK["region"]}).latent == {"Region": BANK["region"]}
    # A latent frame is keyed by group and labels nothing, so it is never
    # mistaken for the patterns.
    assert layout({"region": BANK["region"]}).patterns is None


def test_the_member_label_comes_from_the_node_tables():
    tables = GraphTables(
        nodes={
            "Account": pl.DataFrame({"id": ["acc_1", "acc_2"]}),
            "Topic": pl.DataFrame({"id": ["top_1"]}),
        },
        edges={},
    )
    assert layout(PLATFORM, tables).member_label == "Account"
    # Without tables to match against, the column name is the fallback.
    assert layout(PLATFORM).member_label == "Account"


def test_patterns_are_found_even_without_a_roles_column():
    """Both packs denormalise roles onto the pattern frame; a pack that does
    not is still found, because the memberships point at it."""
    truth = {
        "rings": pl.DataFrame({"ring_id": ["r0"], "is_bad": [True]}),
        "members": pl.DataFrame({"party_id": ["p0"], "ring_id": ["r0"], "role": ["hub"]}),
    }
    found = layout(truth)
    assert found.pattern_id == "ring_id" and found.member_id == "party_id"
    assert found.label == "is_bad" and found.member_label == "Party"


# ------------------------------------------------------- against a database


duckdb = pytest.importorskip("duckdb")


@pytest.fixture(scope="module")
def platform_run():
    from graphfaker.domains.coordination import generate

    return generate(scale=0.0006, tradecraft="medium", seed=5)


def test_a_coordination_dataset_loads_and_verifies_in_duckdb(platform_run, tmp_path_factory):
    from graphfaker.sinks.duckdb import load_directory, verify_directory

    root = tmp_path_factory.mktemp("platform")
    platform_run.write(root)
    db = root / "graph.duckdb"
    report = load_directory(root, db)

    # The truth lands under the universal labels with the platform's own
    # properties on them, not a bank's.
    assert report.truth[PATTERN_LABEL] == platform_run.truth["campaigns"].height
    assert report.truth[MEMBER_REL] == platform_run.truth["accounts"].height
    assert any(key.endswith(".is_coordinated") for key in report.truth)
    assert report.truth["Community"] == platform_run.truth["community"].height

    verification = verify_directory(root, db)
    assert verification.ok, verification.summary()
    # And the checks are named in the platform's words, which is the thing
    # that was wrong: a fraud vocabulary on a dataset that has no fraud.
    names = {check.name for check in verification.checks}
    assert f"truth/{PATTERN_LABEL}.is_coordinated" in names
    assert not any("is_fraud" in name for name in names)


def test_the_property_graph_names_the_right_member_label(platform_run):
    from graphfaker.sinks.duckdb import property_graph

    statement = property_graph(platform_run.tables, platform_run.truth, "coordination")
    assert f'"{MEMBER_REL}" SOURCE KEY ("source") REFERENCES "Account"' in statement
    assert f'"{PATTERN_LABEL}"' in statement


def test_a_coordination_dataset_loads_and_verifies_in_ladybug(platform_run, tmp_path_factory):
    from graphfaker.sinks.ladybug import _driver, load_directory, verify_directory

    try:
        _driver()
    except ImportError as exc:  # pragma: no cover - driver is optional
        pytest.skip(str(exc))

    root = tmp_path_factory.mktemp("platform-lbdb")
    platform_run.write(root)
    report = load_directory(root, root / "graph.lbdb")
    assert report.truth[PATTERN_LABEL] == platform_run.truth["campaigns"].height
    assert verify_directory(root, root / "graph.lbdb").ok
