"""A generated dataset written as Senzing records, with the answer kept aside.

The tests that matter here are the two that would make the output worthless:
an entity id leaking into the records, which hands a resolver the answer, and
a dataset so clean that resolving it is free. The rest check that what we
write is what our own loader reads, because an export nobody can load is a
file rather than a dataset.
"""

from __future__ import annotations

import collections
import json

import polars as pl
import pytest

from graphfaker.domains.supply_chain import generate
from graphfaker.engine.names import stem
from graphfaker.fetchers.senzing import SenzingFetcher
from graphfaker.resolve import evaluate_clusters, resolve_entities
from graphfaker.sinks.senzing import SAME_ADDRESS_RATE, read_gold, write_senzing

SCALE = 0.004


@pytest.fixture(scope="module")
def run():
    return generate(scale=SCALE, seed=7)


@pytest.fixture(scope="module")
def export(run, tmp_path_factory):
    out = tmp_path_factory.mktemp("senzing")
    return write_senzing(run, out), out


def records_of(directory, kind=None):
    path = directory / "records.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if kind is None:
        return records
    return [
        record
        for record in records
        if any(f.get("RECORD_TYPE") == kind for f in record["FEATURES"])
    ]


def test_the_records_never_contain_the_answer(export):
    """The file handed to a resolver has to be the file without the answer."""
    report, out = export
    text = (out / "records.jsonl").read_text(encoding="utf-8")
    assert "entity_id" not in text and "ENTITY_ID" not in text
    # Nor by another name: no feature should hold the id of the entity, which
    # is the node id the records were generated from.
    records = records_of(out)
    for record in records[:200]:
        features = {k: v for feature in record["FEATURES"] for k, v in feature.items()}
        assert record["RECORD_ID"].split("-")[0] not in set(map(str, features.values()))
    assert report.entities.name == "entities.parquet"


def test_the_answer_key_says_which_records_are_one_company(export):
    report, out = export
    frame = pl.read_parquet(out / "entities.parquet")
    assert set(frame.columns) == {"record_id", "entity_id", "node_type"}
    assert frame.height == report.record_count
    assert frame["entity_id"].n_unique() == report.entity_count
    assert frame["record_id"].n_unique() == report.record_count, "record ids are unique"

    clusters = read_gold(out)
    assert len(clusters) == report.multi_record_entities
    assert set(frame["node_type"].to_list()) >= {"Supplier", "Person"}
    assert all(len(c) > 1 for c in clusters), "a single record is not a cluster to find"
    assert sum(len(c) for c in clusters) < report.record_count


def test_what_our_own_loader_reads_back_is_what_we_wrote(export):
    """The format is the one the fetcher parses, and the pointers rebuild the
    answer: that is what makes this a Senzing dataset rather than a JSON file
    that happens to have the right field names."""
    report, out = export
    graph = SenzingFetcher.fetch_graph(out / "records.jsonl")
    assert graph.number_of_nodes() == report.record_count
    # A company's records after its first point at it; every person record
    # points at a company, including the first, because a person has no
    # anchor of their own in a register.
    companies = report.entity_count - report.people
    company_records = report.record_count - report.person_records
    assert graph.number_of_edges() == (company_records - companies) + report.person_records

    # Not dict(graph.edges()): an edge view keys on the edge tuple, so that
    # gives {(u, v): attrs} and groups nothing.
    parent = {source: target for source, target in graph.edges()}
    groups = collections.defaultdict(list)
    for node in graph.nodes():
        groups[parent.get(node, node)].append(node)
    rebuilt = {tuple(sorted(v)) for v in groups.values() if len(v) > 1}

    gold = read_gold(out)
    frame = pl.read_parquet(out / "entities.parquet")
    person_records = set(
        frame.filter(pl.col("node_type") == "Person")["record_id"].to_list()
    )
    # Every record of a company roots at that company. The group rooted there
    # holds the people attached to it as well, which is why this is a subset
    # rather than an equality.
    for cluster in (c for c in gold if not set(c) & person_records):
        roots = {parent.get(record, record) for record in cluster}
        assert roots == {cluster[0]}, cluster

    # The people are the part the pointers cannot rebuild: their records point
    # at the companies they are attached to, not at each other. That is the
    # resolution problem, and their answer exists only in the gold file.
    person_clusters = [c for c in gold if set(c) <= person_records]
    assert person_clusters, "the export has people in it"
    for cluster in person_clusters:
        roots = {parent.get(record, record) for record in cluster}
        assert len(roots) > 1 or roots & person_records == set(), cluster
    assert not any(tuple(sorted(c)) in rebuilt for c in person_clusters)


def test_one_company_is_written_several_ways(export):
    report, out = export
    companies = report.entity_count - report.people
    assert report.multi_record_entities > 0.4 * companies
    assert report.varied_names > 0, "a dataset of identical strings is not a resolution problem"
    assert report.moved_records > 0, "nor is one where every record shares an address"

    by_entity = collections.defaultdict(list)
    for record in records_of(out, "ORGANIZATION"):
        features = {k: v for feature in record["FEATURES"] for k, v in feature.items()}
        by_entity[record["RECORD_ID"].split("-")[0]].append(features)

    several = [v for v in by_entity.values() if len(v) > 2]
    assert several, "the measured shape has companies with three records"
    # Within one company: one stem, more than one spelling, more than one
    # address. A register separates a headquarters from a branch by address.
    spellings = {tuple(sorted({f["NAME_ORG"] for f in v})) for v in several}
    assert any(len(s) > 1 for s in spellings)
    assert all(len({stem(f["NAME_ORG"]) for f in v}) == 1 for v in several)
    moved = sum(1 for v in several if len({f.get("ADDR_LINE1") for f in v}) > 1)
    assert moved > len(several) * (1 - SAME_ADDRESS_RATE) / 2


def test_the_dataset_is_worth_resolving(export):
    """Scored, because a benchmark nobody has scored is a guess.

    The name alone should mostly work and neither address rule should: a
    company's records sit at different addresses, which is why a register has
    a record per site, and other companies share those addresses, which is
    the registered agent. If this ever becomes easy, the generator has
    stopped reproducing the thing that makes resolution hard.
    """
    _, out = export
    whole = SenzingFetcher.fetch_graph(out / "records.jsonl")
    # A slice, because comparison inside a block is quadratic and this is a
    # statement about the shape of the problem rather than about scale. The
    # notebook scores the whole thing.
    keep = {n for n in whole.nodes() if n.split("-")[0] in {f"sup_{i}" for i in range(200)}}
    graph = whole.subgraph(keep).copy()
    for _, data in graph.nodes(data=True):
        data["stem"] = stem(data.get("NAME_ORG") or "")
    gold = [c for c in read_gold(out) if set(c) <= keep]

    def score(**kwargs):
        result = resolve_entities(
            graph, structural_weight=0.0, node_types=["ORGANIZATION"], **kwargs
        )
        return evaluate_clusters(result.clusters, gold)

    by_name = score(on=["NAME_ORG"], threshold=0.85, block_on=["stem"])
    assert by_name["pairwise_f1"] > 0.8, "the name should mostly work"
    assert by_name["pairwise_f1"] < 0.99, "and not be free"

    by_address = score(on=["ADDR_LINE1"], threshold=0.95, block_on=["ADDR_LINE1"])
    assert by_address["pairwise_precision"] < 0.3, "an address is not an identifier"


def test_the_export_is_reproducible(run, tmp_path):
    first = write_senzing(run, tmp_path / "a")
    second = write_senzing(run, tmp_path / "b")
    assert (tmp_path / "a" / "records.jsonl").read_bytes() == (
        tmp_path / "b" / "records.jsonl"
    ).read_bytes()
    assert first.record_count == second.record_count

    other = write_senzing(run, tmp_path / "c", seed=99)
    assert other.record_count != first.record_count or (
        tmp_path / "c" / "records.jsonl"
    ).read_bytes() != (tmp_path / "a" / "records.jsonl").read_bytes()


def test_the_cli_writes_the_sink(tmp_path):
    from typer.testing import CliRunner

    from graphfaker.cli import app

    out = tmp_path / "chain"
    result = CliRunner().invoke(
        app,
        ["generate", "supply_chain", "--scale", "0.002", "--seed", "3",
         "--out", str(out), "--sink", "senzing", "--quiet"],
    )
    assert result.exit_code == 0, result.output
    assert (out / "senzing" / "records.jsonl").is_file()
    assert (out / "senzing" / "entities.parquet").is_file()
    # The ordinary Parquet dataset is written too; the sink is an addition.
    assert (out / "nodes" / "Supplier.parquet").is_file()


def test_a_person_is_a_record_per_company(export):
    """A register holds no person, only person-company rows.

    Somebody attached to three companies is three records, written three ways,
    and nothing in the records links them to each other. Most companies have
    nobody at all, which is the part a generator usually gets wrong by not
    thinking about it: 4.5% of a register's companies have anybody.
    """
    report, out = export
    assert report.people > 0 and report.person_records > report.people

    people = [
        {k: v for feature in record["FEATURES"] for k, v in feature.items()}
        for record in records_of(out, "PERSON")
    ]
    assert len(people) == report.person_records
    # Every person record points at a company, with a register's own roles.
    assert all("REL_POINTER_KEY" in person for person in people)
    assert {person["REL_POINTER_ROLE"] for person in people} == {"Contact", "Executive"}
    assert all("REL_ANCHOR_KEY" not in person for person in people)

    # A register records the name parts on about a quarter of person rows and
    # an address on three quarters; a resolver with the parts has an easier
    # job than one without, and real data mostly withholds them.
    parts = sum(1 for person in people if "NAME_LAST" in person) / len(people)
    address = sum(1 for person in people if "ADDR_LINE1" in person) / len(people)
    assert 0.15 < parts < 0.32, parts
    assert 0.65 < address < 0.85, address

    # One person, several spellings of one name.
    frame = pl.read_parquet(out / "entities.parquet")
    multi = (
        frame.filter(pl.col("node_type") == "Person")
        .group_by("entity_id")
        .agg(pl.col("record_id"))
        .filter(pl.col("record_id").list.len() > 1)
    )
    assert multi.height > 0, "somebody sits on more than one board"


def test_most_companies_have_nobody(run):
    """The measured share, which is the easy half to get wrong."""
    links = sum(
        run.tables.edges[name].height
        for name in ("CONTACT_AT", "EXECUTIVE_AT")
        if name in run.tables.edges
    )
    assert links > 0
    attached = set()
    for name in ("CONTACT_AT", "EXECUTIVE_AT"):
        if name in run.tables.edges:
            attached |= set(run.tables.edges[name]["target"].to_list())
    companies = sum(
        run.tables.nodes[label].height
        for label in ("Supplier", "Plant", "Customer", "Carrier")
        if label in run.tables.nodes
    )
    share = len(attached) / companies
    assert 0.03 < share < 0.06, f"{share:.4f} against a measured 0.0447"
