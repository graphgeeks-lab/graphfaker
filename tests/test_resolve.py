# tests/test_resolve.py
"""Graph-native entity resolution."""

import networkx as nx
import pytest

from graphfaker.core import GraphFaker
from graphfaker.resolve import (
    MERGED_FROM,
    MERGED_VARIANTS,
    attribute_similarity,
    evaluate_clusters,
    merge_clusters,
    neighbor_overlap,
    normalize,
    pick_canonical,
    resolve_entities,
)


def _duplicate_pair_graph():
    """Two spellings of one company, sharing most of their neighbourhood.

    Attribute similarity alone is mediocre; the shared neighbours are what make
    this pair recognisable.
    """
    G = nx.DiGraph()
    G.add_node("org_a", type="Organization", name="Acme Corporation", industry="Tools")
    G.add_node("org_b", type="Organization", name="ACME Corp.", industry="Tools")
    G.add_node("org_far", type="Organization", name="Zenith Industries")
    # Deliberately dissimilar names: "Person 0" and "Person 1" would themselves
    # score as near-duplicates, which would make this fixture test the wrong
    # thing.
    employees = ["Ada Lovelace", "Grace Hopper", "Alan Turing", "Edsger Dijkstra"]
    for i, full_name in enumerate(employees):
        person = f"person_{i}"
        G.add_node(person, type="Person", name=full_name)
        G.add_edge(person, "org_a", relationship="WORKS_AT")
        G.add_edge(person, "org_b", relationship="WORKS_AT")
    G.add_node("person_x", type="Person", name="Katherine Johnson")
    G.add_edge("person_x", "org_far", relationship="WORKS_AT")
    return G


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #


def test_normalize_strips_punctuation_and_case():
    assert normalize("ACME Corp.") == "acme corp"
    assert normalize(None) == ""
    assert normalize("  multiple   spaces ") == "multiple spaces"


def test_attribute_similarity_is_token_order_insensitive():
    left = {"name": "Acme Corporation"}
    right = {"name": "Corporation Acme"}
    assert attribute_similarity(left, right, ["name"]) > 0.9


def test_attribute_similarity_skips_fields_absent_on_both_sides():
    # A shared missing email must not be scored as agreement.
    both_present = attribute_similarity(
        {"name": "Ann", "email": "a@x.com"}, {"name": "Ann", "email": "a@x.com"}, ["name", "email"]
    )
    email_missing = attribute_similarity({"name": "Ann"}, {"name": "Ann"}, ["name", "email"])
    assert both_present == pytest.approx(1.0)
    assert email_missing == pytest.approx(1.0)

    # And a field present on only one side counts as disagreement.
    half = attribute_similarity(
        {"name": "Ann", "email": "a@x.com"}, {"name": "Ann"}, ["name", "email"]
    )
    assert half < 1.0


def test_attribute_similarity_with_no_comparable_fields_is_zero():
    assert attribute_similarity({}, {}, ["name"]) == 0.0


def test_token_subset_lifts_a_shortened_name():
    """"Hill" vs "Allison Hill" scores ~0.5 on character ratio alone."""
    plain = attribute_similarity({"name": "Hill"}, {"name": "Allison Hill"}, ["name"])
    lifted = attribute_similarity(
        {"name": "Hill"}, {"name": "Allison Hill"}, ["name"], token_subset_floor=0.75
    )
    assert plain < 0.6
    assert lifted == pytest.approx(0.75)


def test_token_subset_floor_never_reaches_certainty():
    """Containment is suggestive, not conclusive, so it must stay below 1.0."""
    from graphfaker.resolve import TOKEN_SUBSET_FLOOR

    assert TOKEN_SUBSET_FLOOR < 1.0
    score = attribute_similarity(
        {"name": "Smith"}, {"name": "John Smith"}, ["name"],
        token_subset_floor=TOKEN_SUBSET_FLOOR,
    )
    assert score < 1.0


def test_initials_are_dropped_so_a_shortened_name_still_matches():
    """"A. Hill" reduces to its meaningful token, "hill".

    That token is contained in "allison hill", so the pair is lifted — which is
    the intent: an initialised form is one of the commonest ways a document
    refers back to a person it already named.
    """
    score = attribute_similarity(
        {"name": "A. Hill"}, {"name": "Allison Hill"}, ["name"], token_subset_floor=0.75
    )
    assert score == pytest.approx(0.75)
    assert (
        attribute_similarity(
            {"name": "A. Hill"}, {"name": "Allison Hill"}, ["name"],
            token_subset_floor=0.0,
        )
        < 0.75
    )


def test_same_surname_different_initial_is_a_known_precision_hazard():
    """Documents a real weakness rather than asserting it is absent.

    "A. Hill" and "B. Hill" are different people, but character ratio scores
    them ~0.83 because five of six characters agree. Nothing in this module
    fixes that; it is why `structural_weight` exists and why a resolution result
    should be reviewed rather than applied blindly.
    """
    score = attribute_similarity({"name": "A. Hill"}, {"name": "B. Hill"}, ["name"])
    assert score > 0.8

    # Structure is what separates them: with no shared neighbours they are not
    # merged at the default threshold.
    G = nx.Graph()
    G.add_node("a", type="Person", name="A. Hill")
    G.add_node("b", type="Person", name="B. Hill")
    G.add_edge("a", "acme")
    G.add_edge("b", "zenith")
    assert resolve_entities(G, on=["name"], threshold=0.9).clusters == []


def test_containment_alone_does_not_merge_without_structural_support():
    """The floor must lift a pair into consideration, not decide it."""
    G = nx.Graph()
    G.add_node("a", type="Person", name="Hill")
    G.add_node("b", type="Person", name="Allison Hill")
    # No shared neighbours at all.
    result = resolve_entities(G, on=["name"], threshold=0.85, structural_weight=0.5)
    assert result.clusters == []


def test_containment_plus_shared_neighbours_does_merge():
    G = nx.Graph()
    G.add_node("a", type="Person", name="Hill")
    G.add_node("b", type="Person", name="Allison Hill")
    for shared in ("acme", "london", "project_x", "team_y"):
        G.add_edge("a", shared)
        G.add_edge("b", shared)
    result = resolve_entities(G, on=["name"], threshold=0.85, structural_weight=0.5)
    assert result.clusters == [["a", "b"]]


def test_token_subset_can_be_disabled():
    G = nx.Graph()
    G.add_node("a", type="Person", name="Hill")
    G.add_node("b", type="Person", name="Allison Hill")
    for shared in ("acme", "london", "project_x", "team_y"):
        G.add_edge("a", shared)
        G.add_edge("b", shared)
    off = resolve_entities(
        G, on=["name"], threshold=0.85, structural_weight=0.5, token_subset_floor=0.0
    )
    assert off.clusters == []


def test_neighbor_overlap_ignores_the_direct_edge_between_candidates():
    G = nx.DiGraph()
    G.add_edge("a", "b")  # only connection is to each other
    assert neighbor_overlap(G, "a", "b") == 0.0


def test_neighbor_overlap_counts_shared_context():
    G = nx.DiGraph()
    for shared in ("x", "y", "z"):
        G.add_edge("a", shared)
        G.add_edge("b", shared)
    assert neighbor_overlap(G, "a", "b") == pytest.approx(1.0)


def test_neighbor_overlap_ignores_direction():
    G = nx.DiGraph()
    G.add_edge("a", "shared")
    G.add_edge("shared", "b")  # opposite direction, same context
    assert neighbor_overlap(G, "a", "b") == pytest.approx(1.0)


def test_neighbor_overlap_works_on_undirected_and_multigraphs():
    for graph_type in (nx.Graph, nx.MultiGraph, nx.MultiDiGraph):
        G = graph_type()
        G.add_edge("a", "shared")
        G.add_edge("b", "shared")
        assert neighbor_overlap(G, "a", "b") == pytest.approx(1.0), graph_type


# --------------------------------------------------------------------------- #
# resolution
# --------------------------------------------------------------------------- #


def test_structural_signal_finds_a_pair_attributes_alone_would_miss():
    G = _duplicate_pair_graph()
    attributes_only = resolve_entities(
        G, on=["name"], threshold=0.85, structural_weight=0.0
    )
    with_structure = resolve_entities(
        G, on=["name"], threshold=0.85, structural_weight=0.8
    )
    assert attributes_only.clusters == []
    assert with_structure.clusters == [["org_a", "org_b"]]


def test_structure_never_lowers_a_confident_attribute_match():
    G = nx.DiGraph()
    G.add_node("a", type="Person", name="Jane Doe")
    G.add_node("b", type="Person", name="Jane Doe")
    # No edges at all, so structural overlap is 0.
    result = resolve_entities(G, on=["name"], threshold=0.95, structural_weight=0.9)
    assert result.clusters == [["a", "b"]]


def test_unrelated_nodes_are_not_clustered():
    G = _duplicate_pair_graph()
    result = resolve_entities(G, on=["name"], threshold=0.85, structural_weight=0.8)
    clustered = {node for cluster in result.clusters for node in cluster}
    assert "org_far" not in clustered
    assert "person_x" not in clustered


def test_respect_type_prevents_cross_type_merges():
    G = nx.DiGraph()
    G.add_node("p", type="Person", name="Acme")
    G.add_node("o", type="Organization", name="Acme")
    assert resolve_entities(G, on=["name"], respect_type=True).clusters == []
    assert resolve_entities(G, on=["name"], respect_type=False).clusters == [["o", "p"]]


def test_node_types_filter_restricts_the_search():
    G = _duplicate_pair_graph()
    result = resolve_entities(
        G, on=["name"], threshold=0.85, structural_weight=0.8, node_types=["Person"]
    )
    assert result.clusters == []


def test_resolution_is_deterministic():
    G = _duplicate_pair_graph()
    first = resolve_entities(G, on=["name"], structural_weight=0.8)
    second = resolve_entities(G, on=["name"], structural_weight=0.8)
    assert first.clusters == second.clusters
    assert first.scores == second.scores


def test_resolve_does_not_mutate_the_input_graph():
    G = _duplicate_pair_graph()
    before = (G.number_of_nodes(), G.number_of_edges())
    resolve_entities(G, on=["name"], structural_weight=0.8).apply()
    assert (G.number_of_nodes(), G.number_of_edges()) == before


def test_invalid_arguments_are_rejected():
    G = _duplicate_pair_graph()
    with pytest.raises(ValueError):
        resolve_entities(G, on=["name"], threshold=1.5)
    with pytest.raises(ValueError):
        resolve_entities(G, on=["name"], structural_weight=-0.1)
    with pytest.raises(ValueError):
        resolve_entities(G, on=[])


def test_unblocked_comparison_is_guarded_on_large_graphs():
    from graphfaker import resolve as resolve_module

    G = nx.Graph()
    for i in range(resolve_module.MAX_UNBLOCKED_NODES + 1):
        G.add_node(i, type="Person", name=f"Person {i}")
    with pytest.raises(ValueError, match="Refusing to compare"):
        resolve_entities(G, on=["name"], block_on=[])


def test_report_is_printable():
    G = _duplicate_pair_graph()
    text = resolve_entities(G, on=["name"], structural_weight=0.8).report()
    assert "clusters found" in text
    assert "duplicate nodes" in text


# --------------------------------------------------------------------------- #
# merging
# --------------------------------------------------------------------------- #


def test_merge_rewires_edges_onto_the_canonical_node():
    G = _duplicate_pair_graph()
    merged = merge_clusters(G, [["org_a", "org_b"]])

    assert merged.number_of_nodes() == G.number_of_nodes() - 1
    survivor = "org_a" if "org_a" in merged else "org_b"
    assert ("org_a" in merged) != ("org_b" in merged)

    # All four employees must still reach the surviving organization.
    for i in range(4):
        assert merged.has_edge(f"person_{i}", survivor)
    # And no edge may still point at the absorbed node.
    absorbed = "org_b" if survivor == "org_a" else "org_a"
    assert absorbed not in merged
    assert all(absorbed not in (u, v) for u, v in merged.edges())


def test_merge_drops_self_loops_created_by_the_merge():
    G = nx.DiGraph()
    G.add_node("a", type="Person", name="Dup")
    G.add_node("b", type="Person", name="Dup")
    G.add_edge("a", "b", relationship="FRIENDS_WITH")
    merged = merge_clusters(G, [["a", "b"]])
    assert merged.number_of_nodes() == 1
    assert merged.number_of_edges() == 0


def test_merge_records_provenance_and_conflicting_values():
    G = nx.DiGraph()
    G.add_node("a", type="Person", name="Jane Doe", email="jane@x.com")
    G.add_node("b", type="Person", name="Jane Doe", email="j.doe@x.com")
    G.add_edge("a", "peer")
    G.add_edge("b", "peer")

    merged = merge_clusters(G, [["a", "b"]])
    survivor = next(n for n in ("a", "b") if n in merged)
    absorbed = "b" if survivor == "a" else "a"
    data = merged.nodes[survivor]

    assert data[MERGED_FROM] == [absorbed]
    assert "email" in data[MERGED_VARIANTS]


def test_merge_fills_gaps_from_absorbed_nodes():
    G = nx.Graph()
    G.add_node("a", type="Person", name="Jane", email=None)
    G.add_node("b", type="Person", name="Jane", email="jane@x.com", phone="555")
    merged = merge_clusters(G, [["a", "b"]])
    survivor = next(n for n in ("a", "b") if n in merged)
    assert merged.nodes[survivor]["email"] == "jane@x.com"
    assert merged.nodes[survivor]["phone"] == "555"


def test_merge_counts_collapsed_duplicate_edges():
    G = nx.DiGraph()
    G.add_node("a", type="Organization", name="Dup")
    G.add_node("b", type="Organization", name="Dup")
    G.add_node("p", type="Person", name="Person")
    G.add_edge("p", "a", relationship="WORKS_AT")
    G.add_edge("p", "b", relationship="WORKS_AT")
    merged = merge_clusters(G, [["a", "b"]])
    survivor = next(n for n in ("a", "b") if n in merged)
    assert merged.edges["p", survivor]["_merge_count"] == 2


def test_merge_handles_a_cluster_of_three():
    G = nx.Graph()
    for name in ("a", "b", "c"):
        G.add_node(name, type="Person", name="Same Person")
        G.add_edge(name, "anchor")
    merged = merge_clusters(G, [["a", "b", "c"]])
    assert merged.number_of_nodes() == 2  # one survivor plus the anchor


def test_merge_ignores_singleton_and_missing_clusters():
    G = _duplicate_pair_graph()
    merged = merge_clusters(G, [["org_a"], ["ghost_1", "ghost_2"]])
    assert merged.number_of_nodes() == G.number_of_nodes()


def test_merge_inplace_mutates_the_original():
    G = _duplicate_pair_graph()
    original_count = G.number_of_nodes()
    returned = merge_clusters(G, [["org_a", "org_b"]], inplace=True)
    assert returned is G
    assert G.number_of_nodes() == original_count - 1


def test_pick_canonical_prefers_the_better_connected_node():
    G = nx.Graph()
    G.add_node("sparse", name="X")
    G.add_node("rich", name="X")
    G.add_edge("rich", "n1")
    G.add_edge("rich", "n2")
    assert pick_canonical(G, ["sparse", "rich"]) == "rich"


def test_canonical_override_is_respected():
    G = _duplicate_pair_graph()
    merged = merge_clusters(
        G, [["org_a", "org_b"]], canonical=lambda graph, cluster: "org_b"
    )
    assert "org_b" in merged
    assert "org_a" not in merged


# --------------------------------------------------------------------------- #
# evaluation
# --------------------------------------------------------------------------- #


def test_evaluate_perfect_prediction():
    gold = [["a", "b"], ["c", "d"]]
    scores = evaluate_clusters(gold, gold)
    assert scores["pairwise_f1"] == pytest.approx(1.0)
    assert scores["b_cubed_f1"] == pytest.approx(1.0)


def test_evaluate_penalises_a_missed_cluster():
    gold = [["a", "b"]]
    predicted = []
    scores = evaluate_clusters(predicted, gold)
    assert scores["pairwise_recall"] == pytest.approx(0.0)
    assert scores["gold_pairs"] == 1
    assert scores["correct_pairs"] == 0


def test_evaluate_penalises_an_over_merge():
    gold = [["a", "b"]]
    predicted = [["a", "b", "c"]]
    scores = evaluate_clusters(predicted, gold)
    assert scores["pairwise_recall"] == pytest.approx(1.0)
    assert scores["pairwise_precision"] < 1.0


def test_evaluate_accepts_mapping_form():
    as_clusters = evaluate_clusters([["a", "b"]], [["a", "b"]])
    as_mapping = evaluate_clusters({"a": 0, "b": 0}, {"a": "x", "b": "x"})
    assert as_clusters["pairwise_f1"] == as_mapping["pairwise_f1"]


def test_evaluate_treats_unlisted_nodes_as_singletons():
    # 'c' appears only in the prediction, and must not be assumed to match.
    scores = evaluate_clusters([["a", "b"], ["c", "d"]], [["a", "b"]])
    assert scores["pairwise_precision"] == pytest.approx(0.5)


def test_evaluate_empty_inputs_do_not_divide_by_zero():
    scores = evaluate_clusters([], [])
    assert scores["pairwise_f1"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# integration with the generator
# --------------------------------------------------------------------------- #


def test_resolve_runs_on_a_generated_graph():
    gf = GraphFaker(seed=42)
    gf.generate_graph(source="faker", total_nodes=100, total_edges=300)
    result = gf.resolve(on=["name"], threshold=0.9)
    # Distinct faker names should not collapse; the point is that it completes
    # and reports cleanly on a realistic graph.
    assert result.candidates_considered >= 0
    assert isinstance(result.report(), str)
    assert result.apply().number_of_nodes() <= 100


def test_resolve_round_trip_recovers_injected_duplicates():
    """End-to-end: duplicate a node, then check resolve finds it back.

    This is the only claim being made — that a node copied with a perturbed
    name and a shared neighbourhood is recoverable. It is not a claim that real
    extraction errors look like this one.
    """
    gf = GraphFaker(seed=7)
    G = gf.generate_graph(source="faker", total_nodes=60, total_edges=200)

    people = [n for n, d in G.nodes(data=True) if d.get("type") == "Person"]
    original = min(people)
    twin = f"{original}__twin"
    data = dict(G.nodes[original])
    data["name"] = data["name"].upper() + "."
    G.add_node(twin, **data)
    for neighbor in list(G.successors(original)):
        G.add_edge(twin, neighbor, **G.edges[original, neighbor])

    result = resolve_entities(
        G, on=["name", "email"], threshold=0.85, structural_weight=0.6
    )
    found = [set(cluster) for cluster in result.clusters]
    assert {original, twin} in found

    scores = evaluate_clusters(result.clusters, [[original, twin]])
    assert scores["pairwise_recall"] == pytest.approx(1.0)


def test_export_survives_merge_provenance(tmp_path):
    """Merged nodes carry list/dict attributes; GraphML export must not break."""
    gf = GraphFaker(seed=3)
    G = gf.generate_graph(source="faker", total_nodes=40, total_edges=100)
    people = sorted(n for n, d in G.nodes(data=True) if d.get("type") == "Person")
    a, b = people[0], people[1]
    merged = merge_clusters(G, [[a, b]])

    out = tmp_path / "merged.graphml"
    gf.export_graph(merged, path=str(out))
    assert out.exists()
    reloaded = nx.read_graphml(str(out))
    assert reloaded.number_of_nodes() == merged.number_of_nodes()
