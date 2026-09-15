# tests/test_export.py
"""Export connectors."""

import csv

import networkx as nx
import pytest

from graphfaker.core import GraphFaker
from graphfaker.export import (
    export_csv,
    export_cypher,
    export_neo4j_csv,
    flatten_value,
)


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _heterogeneous_graph():
    """Node types with genuinely different attribute sets.

    This is the case that breaks a naive exporter which takes the first node's
    keys as the header for every row.
    """
    G = nx.DiGraph()
    G.add_node("p1", type="Person", name="Ada Lovelace", age=36, email="ada@x.com")
    G.add_node("pl1", type="Place", name="London", population=9000000)
    G.add_node("o1", type="Organization", name="Acme Ltd", revenue=1000.5)
    G.add_edge("p1", "o1", relationship="WORKS_AT", position="Engineer")
    G.add_edge("p1", "pl1", relationship="LIVES_IN")
    return G


# --------------------------------------------------------------------------- #
# value flattening
# --------------------------------------------------------------------------- #


def test_flatten_value_handles_containers_and_scalars():
    assert flatten_value(None) == ""
    assert flatten_value(True) is True
    assert flatten_value(3) == 3
    assert flatten_value("x") == "x"
    assert flatten_value([1, 2]) == "1,2"
    assert flatten_value((1.5, 2.5)) == "1.5,2.5"
    assert flatten_value({"b": 2, "a": 1}) == "a=1; b=2"


# --------------------------------------------------------------------------- #
# generic CSV
# --------------------------------------------------------------------------- #


def test_csv_header_is_the_union_of_all_node_keys(tmp_path):
    G = _heterogeneous_graph()
    nodes_path, _ = export_csv(
        G, str(tmp_path / "nodes.csv"), str(tmp_path / "edges.csv")
    )
    with open(nodes_path, encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    for column in ("id", "type", "name", "age", "email", "population", "revenue"):
        assert column in header, column


def test_csv_values_land_in_the_right_columns(tmp_path):
    """The bug this guards against writes Place values under Person columns."""
    G = _heterogeneous_graph()
    nodes_path, _ = export_csv(
        G, str(tmp_path / "nodes.csv"), str(tmp_path / "edges.csv")
    )
    with open(nodes_path, encoding="utf-8") as handle:
        rows = {row["id"]: row for row in csv.DictReader(handle)}

    assert rows["pl1"]["name"] == "London"
    assert rows["pl1"]["population"] == "9000000"
    assert rows["pl1"]["age"] == ""  # Place has no age
    assert rows["p1"]["age"] == "36"
    assert rows["p1"]["population"] == ""  # Person has no population
    assert rows["o1"]["revenue"] == "1000.5"


def test_csv_edges_carry_source_target_and_attributes(tmp_path):
    G = _heterogeneous_graph()
    _, edges_path = export_csv(
        G, str(tmp_path / "nodes.csv"), str(tmp_path / "edges.csv")
    )
    with open(edges_path, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    works = next(r for r in rows if r["relationship"] == "WORKS_AT")
    assert works["source"] == "p1"
    assert works["target"] == "o1"
    assert works["position"] == "Engineer"


def test_csv_handles_an_empty_graph(tmp_path):
    """A naive implementation raises StopIteration here."""
    nodes_path, edge_path = export_csv(
        nx.DiGraph(), str(tmp_path / "n.csv"), str(tmp_path / "e.csv")
    )
    with open(nodes_path, encoding="utf-8") as handle:
        assert next(csv.reader(handle)) == ["id"]
    with open(edge_path, encoding="utf-8") as handle:
        assert next(csv.reader(handle)) == ["source", "target"]


def test_csv_writes_non_ascii(tmp_path):
    G = nx.DiGraph()
    G.add_node("n1", type="Person", name="Zoë Ünicode 北京")
    nodes_path, _ = export_csv(G, str(tmp_path / "n.csv"), str(tmp_path / "e.csv"))
    with open(nodes_path, encoding="utf-8") as handle:
        assert "Zoë Ünicode 北京" in handle.read()


def test_csv_flattens_container_attributes(tmp_path):
    G = nx.DiGraph()
    G.add_node("n1", type="Place", coordinates=(51.5, -0.13), tags=["a", "b"])
    nodes_path, _ = export_csv(G, str(tmp_path / "n.csv"), str(tmp_path / "e.csv"))
    with open(nodes_path, encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["coordinates"] == "51.5,-0.13"
    assert row["tags"] == "a,b"


def test_csv_handles_multigraph_edges(tmp_path):
    G = nx.MultiDiGraph()
    G.add_node("a", type="X")
    G.add_node("b", type="X")
    G.add_edge("a", "b", relationship="R1")
    G.add_edge("a", "b", relationship="R2")
    _, edges_path = export_csv(G, str(tmp_path / "n.csv"), str(tmp_path / "e.csv"))
    with open(edges_path, encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 2


# --------------------------------------------------------------------------- #
# neo4j-admin CSV
# --------------------------------------------------------------------------- #


def test_neo4j_csv_uses_typed_headers(tmp_path):
    G = _heterogeneous_graph()
    result = export_neo4j_csv(G, str(tmp_path / "import"))
    with open(result["nodes"], encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert header[0] == "id:ID"
    assert header[-1] == ":LABEL"

    with open(result["edges"], encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert header[0] == ":START_ID"
    assert ":TYPE" in header
    assert header[-1] == ":END_ID"
    assert "neo4j-admin database import" in result["command"]


def test_neo4j_csv_labels_come_from_the_type_attribute(tmp_path):
    G = _heterogeneous_graph()
    result = export_neo4j_csv(G, str(tmp_path / "import"))
    with open(result["nodes"], encoding="utf-8") as handle:
        rows = list(csv.reader(handle))[1:]
    labels = {row[0]: row[-1] for row in rows}
    assert labels["p1"] == "Person"
    assert labels["pl1"] == "Place"


# --------------------------------------------------------------------------- #
# Cypher / GQL
# --------------------------------------------------------------------------- #


def test_cypher_emits_one_statement_per_node_and_edge(tmp_path):
    G = _heterogeneous_graph()
    body = _read(export_cypher(G, str(tmp_path / "g.cypher")))
    assert body.count("CREATE (n:") == 3
    assert body.count("MATCH (a {_gf_id:") == 2
    assert ":Person" in body
    assert "WORKS_AT" in body


def test_gql_dialect_uses_insert(tmp_path):
    G = _heterogeneous_graph()
    with open(export_cypher(G, str(tmp_path / "g.gql"), dialect="gql"), encoding="utf-8") as handle:
        body = handle.read()
    assert "INSERT (n:" in body
    assert "CREATE (n:" not in body


def test_neo4j_dialect_emits_a_constraint_but_opencypher_does_not(tmp_path):
    G = _heterogeneous_graph()
    neo4j = _read(export_cypher(G, str(tmp_path / "a.cypher"), dialect="neo4j"))
    opencypher = _read(
        export_cypher(G, str(tmp_path / "b.cypher"), dialect="opencypher")
    )
    assert "CREATE CONSTRAINT" in neo4j
    assert "CREATE CONSTRAINT" not in opencypher


def test_cypher_rejects_an_unknown_dialect(tmp_path):
    with pytest.raises(ValueError, match="dialect must be one of"):
        export_cypher(_heterogeneous_graph(), str(tmp_path / "g.cypher"), dialect="sql")


def test_cypher_escapes_quotes_and_newlines(tmp_path):
    G = nx.DiGraph()
    G.add_node("n1", type="Person", name='He said "hi"\nthen left', note="back\\slash")
    with open(export_cypher(G, str(tmp_path / "g.cypher")), encoding="utf-8") as handle:
        body = handle.read()
    # The literal must not break out of its quoting, so no raw newline may
    # appear inside the statement.
    statement = next(line for line in body.splitlines() if line.startswith("CREATE (n:"))
    assert statement.endswith(");")
    assert '\\"hi\\"' in statement
    assert "\\n" in statement


def test_cypher_node_id_property_matches_what_relationships_look_up(tmp_path):
    """Regression: identifier sanitising must not rewrite `_gf_id`.

    Stripping the leading underscore made nodes carry `gf_id` while the
    relationship MATCH clauses searched for `_gf_id`, so every relationship
    silently failed to create while the script still ran without error.
    """
    G = _heterogeneous_graph()
    with open(export_cypher(G, str(tmp_path / "g.cypher")), encoding="utf-8") as handle:
        body = handle.read()

    creates = [line for line in body.splitlines() if line.startswith("CREATE (n:")]
    matches = [line for line in body.splitlines() if line.startswith("MATCH (a {")]
    assert creates and matches
    for line in creates:
        assert "_gf_id:" in line, line
        assert " gf_id:" not in line, line
    for line in matches:
        assert "_gf_id:" in line, line


def test_cypher_preserves_underscore_prefixed_properties(tmp_path):
    """`resolve()` writes `_merged_from`; it must survive export intact."""
    G = nx.DiGraph()
    G.add_node("a", type="Person", name="X", _merged_from=["b", "c"])
    with open(export_cypher(G, str(tmp_path / "g.cypher")), encoding="utf-8") as handle:
        body = handle.read()
    assert "_merged_from:" in body


def test_cypher_sanitises_labels_and_relationship_types(tmp_path):
    G = nx.DiGraph()
    G.add_node("a", type="Weird Type-With Punctuation!")
    G.add_node("b", type="123numeric")
    G.add_edge("a", "b", relationship="has spaces & symbols")
    with open(export_cypher(G, str(tmp_path / "g.cypher")), encoding="utf-8") as handle:
        body = handle.read()
    assert "Weird_Type_With_Punctuation" in body
    assert ":_123numeric" in body  # a label may not begin with a digit
    assert "HAS_SPACES___SYMBOLS" in body


def test_cypher_falls_back_to_defaults_for_untyped_elements(tmp_path):
    G = nx.DiGraph()
    G.add_node("a")
    G.add_node("b")
    G.add_edge("a", "b")
    with open(export_cypher(G, str(tmp_path / "g.cypher")), encoding="utf-8") as handle:
        body = handle.read()
    assert ":Node" in body
    assert "RELATED_TO" in body


# --------------------------------------------------------------------------- #
# integration
# --------------------------------------------------------------------------- #


def test_all_exporters_run_on_a_generated_graph(tmp_path):
    gf = GraphFaker(seed=42)
    G = gf.generate_graph(source="faker", total_nodes=60, total_edges=180)

    nodes_path, edges_path = export_csv(
        G, str(tmp_path / "n.csv"), str(tmp_path / "e.csv")
    )
    with open(nodes_path, encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 60

    export_neo4j_csv(G, str(tmp_path / "import"))
    for dialect in ("neo4j", "opencypher", "gql"):
        assert export_cypher(G, str(tmp_path / f"{dialect}.cypher"), dialect=dialect)


def test_export_survives_resolve_provenance(tmp_path):
    """Merged nodes carry list and dict attributes; CSV must handle them."""
    from graphfaker.resolve import merge_clusters

    gf = GraphFaker(seed=3)
    G = gf.generate_graph(source="faker", total_nodes=40, total_edges=100)
    people = sorted(n for n, d in G.nodes(data=True) if d.get("type") == "Person")
    merged = merge_clusters(G, [[people[0], people[1]]])

    nodes_path, _ = export_csv(
        merged, str(tmp_path / "n.csv"), str(tmp_path / "e.csv")
    )
    with open(nodes_path, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert any(row.get("_merged_from") for row in rows)
