"""Loading a register in Senzing's entity resolution format.

The fixture is written here rather than committed, because a register is
real people and GraphFaker ships none. The shape of it, anchors and pointers
over three record kinds, is copied from a real file.
"""

from __future__ import annotations

import collections
import json
import zipfile

import polars as pl
import pytest
from typer.testing import CliRunner

from graphfaker.backends.tables import ID, SOURCE, TARGET, GraphTables
from graphfaker.cli import app
from graphfaker.engine.run import Manifest
from graphfaker.fetchers.senzing import (
    SenzingFetcher,
    format_report,
    read_records,
    read_tables,
    report,
    sources,
    write_dataset,
)

runner = CliRunner()

RECORDS = [
    # Two companies, each an anchor other records point at.
    {"DATA_SOURCE": "OPENDATA", "RECORD_ID": "c1", "bq_dataset": "COMPANY", "FEATURES": [
        {"NAME_ORG": "ACME HOLDINGS, LLC", "NAME_TYPE": "PRIMARY"},
        {"RECORD_TYPE": "ORGANIZATION"},
        {"ADDR_LINE1": "1 Main St", "ADDR_CITY": "Las Vegas", "ADDR_STATE": "NV", "ADDR_POSTAL_CODE": "89101"},
        {"REL_ANCHOR_DOMAIN": "BQ", "REL_ANCHOR_KEY": 1001},
    ]},
    {"DATA_SOURCE": "OPENDATA", "RECORD_ID": "c2", "bq_dataset": "COMPANY", "FEATURES": [
        {"NAME_ORG": "BETA CORP", "NAME_TYPE": "PRIMARY"},
        {"RECORD_TYPE": "ORGANIZATION"},
        {"REL_ANCHOR_DOMAIN": "BQ", "REL_ANCHOR_KEY": 1002},
    ]},
    # A branch and a headquarters of the first.
    {"DATA_SOURCE": "OPENDATA", "RECORD_ID": "l1", "bq_dataset": "LOCATION", "FEATURES": [
        {"NAME_ORG": "ACME HOLDINGS LLC"},
        {"RECORD_TYPE": "ORGANIZATION"},
        {"ADDR_LINE1": "2 Second St", "ADDR_CITY": "Reno", "ADDR_STATE": "NV"},
        {"REL_POINTER_DOMAIN": "BQ", "REL_POINTER_KEY": 1001, "REL_POINTER_ROLE": "BRANCH"},
    ]},
    {"DATA_SOURCE": "OPENDATA", "RECORD_ID": "l2", "bq_dataset": "LOCATION", "FEATURES": [
        {"RECORD_TYPE": "ORGANIZATION"},
        {"REL_POINTER_DOMAIN": "BQ", "REL_POINTER_KEY": 1001, "REL_POINTER_ROLE": "HEADQUARTERS"},
    ]},
    # An officer of the first and a contact at the second.
    {"DATA_SOURCE": "OPENDATA", "RECORD_ID": "p1", "bq_dataset": "PEOPLE_BUSINESS", "FEATURES": [
        {"NAME_FULL": "ALEX STONE"},
        {"NAME_FIRST": "ALEX", "NAME_LAST": "STONE"},
        {"RECORD_TYPE": "PERSON"},
        {"REL_POINTER_DOMAIN": "BQ", "REL_POINTER_KEY": 1001, "REL_POINTER_ROLE": "Executive"},
    ]},
    {"DATA_SOURCE": "OPENDATA", "RECORD_ID": "p2", "bq_dataset": "PEOPLE_BUSINESS", "FEATURES": [
        {"NAME_FULL": "SAM REED"},
        {"RECORD_TYPE": "PERSON"},
        {"REL_POINTER_DOMAIN": "BQ", "REL_POINTER_KEY": 1002, "REL_POINTER_ROLE": "Contact"},
    ]},
    # A pointer at an anchor that is not in the file.
    {"DATA_SOURCE": "OPENDATA", "RECORD_ID": "p3", "bq_dataset": "PEOPLE_BUSINESS", "FEATURES": [
        {"NAME_FULL": "NO ONE"},
        {"RECORD_TYPE": "PERSON"},
        {"REL_POINTER_DOMAIN": "BQ", "REL_POINTER_KEY": 9999, "REL_POINTER_ROLE": "Contact"},
    ]},
]


#: An attribute a record carries twice: a company with an alias, which is
#: the shape the national export has on 552 companies in every half million.
ALIASED = {"DATA_SOURCE": "OPENDATA", "RECORD_ID": "c3", "bq_dataset": "COMPANY", "FEATURES": [
    {"NAME_ORG": "GAMMA TRADING COMPANY", "NAME_TYPE": "PRIMARY"},
    {"NAME_ORG": "GAMMA TRADING CO", "NAME_TYPE": "ALIAS"},
    {"RECORD_TYPE": "ORGANIZATION"},
    # Geo as strings, which is how the company shards write it while the
    # location shards write floats.
    {"GEO_LATITUDE": "36.17", "GEO_LONGITUDE": "-115.14"},
    {"REL_ANCHOR_DOMAIN": "BQ", "REL_ANCHOR_KEY": 1003},
]}


@pytest.fixture
def register(tmp_path):
    path = tmp_path / "register.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n", encoding="utf-8")
    return path


def _sharded(root, pointers_first=False):
    """A register as an export: one directory per kind of record.

    Named so the company shard sorts last, because the loader is not allowed
    to work it out from the file names: in the real archive every location
    shard is called ``bq_organization_locations_...``.
    """
    companies = [r for r in RECORDS if any("REL_ANCHOR_KEY" in f for f in r["FEATURES"])]
    rest = [r for r in RECORDS if r not in companies]
    first, second = (rest, companies) if pointers_first else (companies, rest)
    for name, records in (("zzz_companies", first), ("aaa_others", second)):
        shard = root / name
        shard.mkdir(parents=True, exist_ok=True)
        (shard / f"{name}_000.json").write_text(
            "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
        )
    return root


def test_records_stream_and_bad_lines_do_not_stop_the_load(tmp_path):
    path = tmp_path / "messy.jsonl"
    path.write_text(
        json.dumps(RECORDS[0]) + "\n\nnot json at all\n" + json.dumps(RECORDS[1]) + "\n",
        encoding="utf-8",
    )
    assert [r["RECORD_ID"] for r in read_records(path)] == ["c1", "c2"]
    assert len(list(read_records(path, limit=1))) == 1
    with pytest.raises(FileNotFoundError, match="no register"):
        list(read_records(tmp_path / "missing.jsonl"))


def test_pointers_become_edges_named_for_their_role(register):
    graph = SenzingFetcher.fetch_graph(register)
    assert graph.number_of_nodes() == len(RECORDS)
    kinds = collections.Counter(data["type"] for _, data in graph.nodes(data=True))
    assert kinds == {"ORGANIZATION": 4, "PERSON": 3}

    relationships = {
        (u, v): data["relationship"] for u, v, data in graph.edges(data=True)
    }
    assert relationships == {
        ("l1", "c1"): "BRANCH_OF",
        ("l2", "c1"): "HEADQUARTERS_OF",
        ("p1", "c1"): "EXECUTIVE_AT",
        ("p2", "c2"): "CONTACT_AT",
    }
    # A pointer at an anchor the file does not contain is dropped, not
    # invented: loading half a register is a normal thing to do.
    assert "p3" in graph.nodes and graph.degree("p3") == 0


def test_a_record_is_the_union_of_its_features(register):
    graph = SenzingFetcher.fetch_graph(register)
    acme = graph.nodes["c1"]
    assert acme["NAME_ORG"] == "ACME HOLDINGS, LLC"
    assert acme["ADDR_CITY"] == "Las Vegas" and acme["ADDR_POSTAL_CODE"] == "89101"
    assert graph.nodes["p1"]["NAME_LAST"] == "STONE"


def test_the_loader_can_keep_one_kind_of_record(register):
    graph = SenzingFetcher.fetch_graph(register, record_types=("ORGANIZATION",))
    assert graph.number_of_nodes() == 4
    assert all(data["type"] == "ORGANIZATION" for _, data in graph.nodes(data=True))


def test_the_cli_loads_a_register_and_exports_it(register, tmp_path):
    out = tmp_path / "register.graphml"
    result = runner.invoke(
        app, ["gen", "--fetcher", "senzing", "--path", str(register), "--export", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.exists()


def test_the_cli_says_what_is_missing(tmp_path):
    result = runner.invoke(app, ["gen", "--fetcher", "senzing"])
    assert result.exit_code != 0 and "--path" in result.output
    result = runner.invoke(
        app, ["gen", "--fetcher", "senzing", "--path", str(tmp_path / "nope.jsonl")]
    )
    assert result.exit_code != 0 and "no register" in result.output


# ------------------------------------------------------- sharded exports


def test_a_directory_of_shards_loads_as_one_register(tmp_path):
    graph = SenzingFetcher.fetch_graph(_sharded(tmp_path / "export"))
    assert graph.number_of_nodes() == len(RECORDS)
    assert graph.number_of_edges() == 4, "pointers resolve across shards"


def test_a_zip_loads_as_one_register(tmp_path):
    root = _sharded(tmp_path / "export")
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in sorted(root.rglob("*.json")):
            zf.write(path, str(path.relative_to(root)))
    graph = SenzingFetcher.fetch_graph(archive)
    assert graph.number_of_nodes() == len(RECORDS)
    assert graph.number_of_edges() == 4


def test_the_company_shards_are_read_first_whatever_they_are_called(tmp_path):
    """The ordering is read out of the data, not guessed from the names.

    A name heuristic put 1,988 pointer shards of the national export ahead
    of its 500 company shards, because every location shard in it is called
    ``bq_organization_locations_...``, and nothing resolved.
    """
    names = [name for name, _ in sources(_sharded(tmp_path / "export"))]
    assert names[0].startswith("zzz_companies"), names
    names = [name for name, _ in sources(_sharded(tmp_path / "flipped", pointers_first=True))]
    assert names[0].startswith("aaa_others"), names


def test_a_limit_is_spent_across_the_shards_by_the_report(tmp_path):
    data = report(_sharded(tmp_path / "export"), limit=4)
    assert data["shards_read"] == 2, "a sample that reads one shard says nothing about the format"
    assert set(data["datasets"]) == {"COMPANY", "LOCATION"}, "both kinds of shard were sampled"
    assert data["shard_kinds"] == {"anchor": 1, "pointer": 1}


# --------------------------------------------------- attributes and types


def test_a_repeated_attribute_keeps_its_other_values(tmp_path):
    path = tmp_path / "aliased.jsonl"
    path.write_text(json.dumps(ALIASED) + "\n", encoding="utf-8")
    node = SenzingFetcher.fetch_graph(path).nodes["c3"]
    assert node["NAME_ORG"] == "GAMMA TRADING COMPANY"
    assert node["NAME_ORG_ALSO"] == "GAMMA TRADING CO", "an alias is the problem, not noise"
    # Geo arrives as a string in the company shards and a float in the
    # location shards. One property, one type, or the export is broken.
    assert node["GEO_LATITUDE"] == pytest.approx(36.17)
    assert isinstance(node["GEO_LONGITUDE"], float)


def test_a_null_attribute_is_absent_rather_than_empty(tmp_path):
    path = tmp_path / "null.jsonl"
    path.write_text(json.dumps({
        "DATA_SOURCE": "OPENDATA", "RECORD_ID": "p9", "FEATURES": [
            {"NAME_FULL": None}, {}, {"RECORD_TYPE": "PERSON"},
            {"REL_POINTER_KEY": 1001, "REL_POINTER_ROLE": "Contact"},
        ]}) + "\n", encoding="utf-8")
    node = SenzingFetcher.fetch_graph(path).nodes["p9"]
    assert "NAME_FULL" not in node, "a column that is present and empty flatters coverage"


# ------------------------------------------------------------- filtering


def test_a_filter_keeps_a_company_and_the_records_that_point_at_it(register):
    graph = SenzingFetcher.fetch_graph(register, states=("NV",))
    # Only ACME is in Nevada, so Beta and its contact go.
    assert set(graph.nodes) == {"c1", "l1", "l2", "p1"}
    assert graph.number_of_edges() == 3
    # The officer is kept because the company is, not because of their own
    # address: a Nevada company's director can live anywhere.
    assert "ADDR_STATE" not in graph.nodes["p1"]

    assert set(SenzingFetcher.fetch_graph(register, cities=("Las Vegas",)).nodes) == {
        "c1", "l1", "l2", "p1"
    }
    assert SenzingFetcher.fetch_graph(register, states=("CA",)).number_of_nodes() == 0


# ---------------------------------------------------------- conformance


def test_the_report_names_what_a_load_would_not_keep(tmp_path):
    path = tmp_path / "odd.jsonl"
    path.write_text("\n".join([
        json.dumps(ALIASED),
        json.dumps({"DATA_SOURCE": "OPENDATA", "RECORD_ID": "x1", "FEATURES": [
            {"RECORD_TYPE": "ORGANIZATION"},
            {"SOMETHING_NEW": "value"},
            {"REL_POINTER_KEY": 1003, "REL_POINTER_ROLE": "AFFILIATE"},
        ]}),
        "not json at all",
    ]) + "\n", encoding="utf-8")

    data = report(path)
    assert data["counts"]["records"] == 2 and data["counts"]["bad_lines"] == 1
    assert data["pointer_roles"] == {"AFFILIATE": 1}
    assert data["repeated_attributes"]["NAME_ORG"] == 1
    problems = " | ".join(data["problems"])
    assert "SOMETHING_NEW" in problems, "an attribute nobody has seen is dropped silently"
    assert "AFFILIATE" in problems, "a role with no mapping becomes an edge named after itself"
    assert "not JSON" in problems

    text = format_report(data)
    assert "pointer roles" in text and "AFFILIATE" in text
    assert "attributes on ORGANIZATION records" in text


def test_the_register_command_reports_and_can_print_json(register):
    result = runner.invoke(app, ["register", str(register)])
    assert result.exit_code == 0, result.output
    assert "record types:" in result.output and "ORGANIZATION 4" in result.output

    result = runner.invoke(app, ["register", str(register), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["counts"]["records"] == len(RECORDS)

    result = runner.invoke(app, ["register", str(register.parent / "nope.jsonl")])
    assert result.exit_code != 0 and "no register" in result.output


# ------------------------------------------------------------- as tables


def test_a_register_becomes_node_and_edge_tables(register):
    tables = read_tables(register)
    assert set(tables.nodes) == {"Organization", "Person"}
    assert set(tables.edges) == {"BRANCH_OF", "HEADQUARTERS_OF", "EXECUTIVE_AT", "CONTACT_AT"}
    assert tables.node_count == len(RECORDS)
    assert tables.edge_count == 4

    organisations = tables.nodes["Organization"]
    assert ID in organisations.columns and "record_type" in organisations.columns
    assert "RECORD_TYPE" not in organisations.columns, "the type is the table, and the column"
    # A register's records do not all carry the same attributes, so a column
    # one record lacks has to be null rather than missing.
    assert organisations.filter(pl.col(ID) == "c2")["ADDR_CITY"].to_list() == [None]
    assert set(tables.edges["BRANCH_OF"].columns) == {SOURCE, TARGET, "role"}


def test_the_dataset_a_register_writes_is_one_the_sinks_read(register, tmp_path):
    out = tmp_path / "register_ds"
    tables = read_tables(register)
    write_dataset(tables, out, source=register, label="a test register")

    # The shape every `graphfaker load` and `verify` command expects, minus
    # the truth a register does not have.
    assert (out / "nodes" / "Organization.parquet").is_file()
    assert (out / "edges" / "CONTACT_AT.parquet").is_file()
    assert not (out / "truth").exists() and not (out / "schema.yaml").exists()

    read_back = GraphTables.read_parquet(out)
    assert read_back.node_count == tables.node_count
    assert read_back.edge_count == tables.edge_count

    manifest = Manifest.read(out / "manifest.json")
    assert manifest.schema_name == "register" and manifest.seed is None
    assert manifest.node_counts == {"Organization": 4, "Person": 3}
    assert manifest.extra["register"]["label"] == "a test register"
    # The digest is of the shape that was loaded, so the same cut matches and
    # a different cut does not.
    again = tmp_path / "again"
    write_dataset(read_tables(register), again, source=register)
    assert Manifest.read(again / "manifest.json").schema_digest == manifest.schema_digest
    cut = tmp_path / "cut"
    write_dataset(read_tables(register, states=("NV",)), cut, source=register)
    assert Manifest.read(cut / "manifest.json").schema_digest != manifest.schema_digest


def test_the_tables_and_the_graph_say_the_same_thing(register):
    graph = SenzingFetcher.fetch_graph(register)
    tables = read_tables(register)
    assert tables.node_count == graph.number_of_nodes()
    assert tables.edge_count == graph.number_of_edges()
    relationships = collections.Counter(
        data["relationship"] for _, _, data in graph.edges(data=True)
    )
    assert {name: frame.height for name, frame in tables.edges.items()} == dict(relationships)


def test_the_cli_writes_a_dataset_and_reports_it(register, tmp_path):
    out = tmp_path / "from_cli"
    result = runner.invoke(
        app, ["register", str(register), "--out", str(out), "--state", "NV", "--json"]
    )
    assert result.exit_code == 0, result.output
    written = json.loads(result.stdout)
    assert written["nodes"] == {"Organization": 3, "Person": 1}
    assert (out / "manifest.json").is_file()

    # A sink that needs an answer key is not on offer for a register.
    result = runner.invoke(app, ["register", str(register), "--out", str(tmp_path / "pyg"), "--sink", "pyg"])
    assert result.exit_code != 0 and "--sink for a register" in result.output


def test_a_register_with_nothing_in_scope_is_refused_rather_than_written(register, tmp_path):
    result = runner.invoke(
        app, ["register", str(register), "--out", str(tmp_path / "empty"), "--state", "ZZ"]
    )
    assert result.exit_code != 0 and "nothing to write" in result.output


def test_the_parser_is_interchangeable(register):
    """``orjson`` is used when it is there, and must not change an answer.

    The whole point of the swap is speed, so this checks the records come out
    the same whichever parser is in the environment: the test is meaningful
    in both, because it compares against stdlib ``json`` directly.
    """
    from graphfaker.fetchers.senzing import _loads

    raw = register.read_text(encoding="utf-8").splitlines()
    assert [_loads(line) for line in raw] == [json.loads(line) for line in raw]
    assert list(read_records(register)) == [json.loads(line) for line in raw]
