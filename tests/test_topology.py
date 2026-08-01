# tests/test_topology.py
"""Structural realism of the synthetic generator.

These are differential tests. Rather than asserting an absolute threshold for
"realistic" — which would be arbitrary — each one compares the default generator
against ``topology="uniform"``, the pre-0.5 behaviour of drawing both endpoints
uniformly at random. Uniform attachment is a known quantity (an Erdos-Renyi
graph), so it makes a principled baseline for every property that was supposed to
have been fixed.
"""

import networkx as nx
import pytest

from graphfaker.core import GraphFaker
from graphfaker.metrics import (
    as_simple_undirected,
    degree_gini,
    graph_stats,
    numeric_assortativity,
)

NODES = 400
EDGES = 1600


@pytest.fixture(scope="module")
def realistic():
    return GraphFaker(seed=11).generate_graph(total_nodes=NODES, total_edges=EDGES)


@pytest.fixture(scope="module")
def uniform():
    return GraphFaker(seed=11).generate_graph(
        total_nodes=NODES, total_edges=EDGES, topology="uniform"
    )


def _friendship_subgraph(G):
    """Person-Person edges only.

    The whole graph is multipartite — people attach to hub cities and employers —
    so it is legitimately disassortative. The social layer is where positive
    assortativity is expected, so mixing them hides both effects.
    """
    H = nx.Graph()
    H.add_nodes_from(
        (node, data) for node, data in G.nodes(data=True) if data.get("type") == "Person"
    )
    H.add_edges_from(
        (u, v)
        for u, v, data in G.edges(data=True)
        if u != v
        and data.get("relationship") in {"FRIENDS_WITH", "COLLEAGUES", "MENTORS"}
        and u in H
        and v in H
    )
    return H


# --------------------------------------------------------------------------- #
# heavy-tailed degree distribution
# --------------------------------------------------------------------------- #


def test_degree_distribution_is_heavy_tailed(realistic, uniform):
    """Fano factor separates a Poisson degree distribution from a heavy tail.

    Uniform attachment gives variance approximately equal to mean, so its Fano
    factor sits near 1. A hub-bearing graph is far above it.
    """
    fano_realistic = graph_stats(realistic)["degree_fano"]
    fano_uniform = graph_stats(uniform)["degree_fano"]
    assert fano_uniform < 3.0, "uniform attachment should be roughly Poisson"
    assert fano_realistic > 3 * fano_uniform


def test_hubs_exist(realistic, uniform):
    realistic_max = graph_stats(realistic)["max_degree"]
    uniform_max = graph_stats(uniform)["max_degree"]
    assert realistic_max > 2 * uniform_max


def test_degree_inequality_is_higher(realistic, uniform):
    assert graph_stats(realistic)["degree_gini"] > graph_stats(uniform)["degree_gini"] + 0.1


def test_degree_gini_bounds():
    assert degree_gini([]) == 0.0
    assert degree_gini([0, 0, 0]) == 0.0
    assert degree_gini([5, 5, 5, 5]) == pytest.approx(0.0, abs=0.01)
    # One node holding every edge is maximally unequal.
    assert degree_gini([0, 0, 0, 9]) > 0.6


# --------------------------------------------------------------------------- #
# clustering
# --------------------------------------------------------------------------- #


def test_clustering_far_exceeds_random_baseline(realistic):
    """A sparse random graph clusters at about mean_degree / n."""
    stats = graph_stats(realistic)
    assert stats["average_clustering"] > 5 * stats["clustering_baseline"]


def test_clustering_beats_uniform_attachment(realistic, uniform):
    assert (
        graph_stats(realistic)["average_clustering"]
        > 3 * graph_stats(uniform)["average_clustering"]
    )


def test_friendship_layer_is_strongly_clustered(realistic, uniform):
    """Triadic closure should be most visible among people."""
    assert (
        graph_stats(_friendship_subgraph(realistic))["average_clustering"]
        > 5 * graph_stats(_friendship_subgraph(uniform))["average_clustering"]
    )


# --------------------------------------------------------------------------- #
# communities and homophily
# --------------------------------------------------------------------------- #


def test_community_structure_is_recoverable(realistic, uniform):
    """Modularity against the latent `community` label.

    Uniform attachment ignores the label entirely, so its modularity sits near
    zero — there is no group structure to find.
    """
    assert graph_stats(uniform)["community_modularity"] < 0.05
    assert graph_stats(realistic)["community_modularity"] > 0.35


def test_attributes_correlate_with_topology(realistic, uniform):
    """Age homophily: neighbours should be closer in age than chance allows."""
    assert numeric_assortativity(realistic, "age") > 0.3
    assert abs(numeric_assortativity(uniform, "age")) < 0.15


def test_every_node_carries_a_community(realistic):
    for _, data in realistic.nodes(data=True):
        assert isinstance(data.get("community"), int)


def test_community_count_is_configurable():
    G = GraphFaker(seed=3).generate_graph(
        total_nodes=200, total_edges=600, communities=4
    )
    assert len({data["community"] for _, data in G.nodes(data=True)}) <= 4


# --------------------------------------------------------------------------- #
# connectivity
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("nodes,edges", [(40, 120), (100, 400), (300, 1200)])
def test_no_isolated_nodes(nodes, edges):
    """Preferential attachment alone left 24% of a 600-node graph isolated.

    Real graphs have a giant component, so coverage and top-up passes exist to
    guarantee every node is reachable.
    """
    G = GraphFaker(seed=5).generate_graph(total_nodes=nodes, total_edges=edges)
    assert [node for node, degree in G.degree() if degree == 0] == []


def test_graph_is_connected_as_one_component():
    G = GraphFaker(seed=5).generate_graph(total_nodes=300, total_edges=1200)
    components = list(nx.connected_components(as_simple_undirected(G)))
    largest = max(len(component) for component in components)
    assert largest / G.number_of_nodes() > 0.9


@pytest.mark.parametrize("nodes,edges", [(20, 50), (100, 1000), (300, 1200)])
def test_edge_budget_is_respected(nodes, edges):
    """`total_edges` should mean what `number_of_edges()` reports.

    Counting loop iterations rather than edges made this overshoot by ~14% when
    bidirectional friendships dominated, and undershoot by ~12% when skipped
    functional relationships did.
    """
    G = GraphFaker(seed=5).generate_graph(total_nodes=nodes, total_edges=edges)
    assert edges * 0.9 <= G.number_of_edges() <= edges * 1.1


# --------------------------------------------------------------------------- #
# semantic realism
# --------------------------------------------------------------------------- #


def test_functional_relationships_are_singular():
    """Nobody lives in three cities or was born in two."""
    G = GraphFaker(seed=9).generate_graph(total_nodes=400, total_edges=1600)
    for relationship in ("LIVES_IN", "BORN_IN"):
        counts = {}
        for source, _, data in G.edges(data=True):
            if data.get("relationship") == relationship:
                counts[source] = counts.get(source, 0) + 1
        assert not counts or max(counts.values()) == 1, relationship


def test_uniform_topology_still_produces_the_old_defect():
    """Guards the baseline: the comparison is meaningless if it also got fixed."""
    G = GraphFaker(seed=9).generate_graph(
        total_nodes=300, total_edges=1200, topology="uniform"
    )
    counts = {}
    for source, _, data in G.edges(data=True):
        if data.get("relationship") == "LIVES_IN":
            counts[source] = counts.get(source, 0) + 1
    assert counts and max(counts.values()) > 1


def test_employee_count_matches_the_actual_workforce():
    """An attribute that contradicts the topology is a trap for users."""
    G = GraphFaker(seed=7).generate_graph(total_nodes=400, total_edges=1600)
    pairs = []
    for node, data in G.nodes(data=True):
        if data.get("type") != "Organization":
            continue
        employed = sum(
            1
            for _, _, edge in G.in_edges(node, data=True)
            if edge.get("relationship") == "WORKS_AT"
        )
        pairs.append((data["employee_count"], employed))
        assert data["employee_count"] >= employed

    assert len(pairs) > 5
    import statistics

    assert statistics.correlation(*zip(*pairs)) > 0.5


def test_place_population_tracks_prominence():
    G = GraphFaker(seed=7).generate_graph(total_nodes=400, total_edges=1600)
    pairs = [
        (data["population"], data["prominence"])
        for _, data in G.nodes(data=True)
        if data.get("type") == "Place"
    ]
    import statistics

    assert statistics.correlation(*zip(*pairs)) > 0.8


def test_industry_is_an_industry_not_a_job_title():
    """`industry=fake.job()` produced values like "Chartered accountant"."""
    from graphfaker.core import INDUSTRY_BY_SUBTYPE

    allowed = {value for values in INDUSTRY_BY_SUBTYPE.values() for value in values}
    G = GraphFaker(seed=7).generate_graph(total_nodes=200, total_edges=600)
    industries = {
        data["industry"]
        for _, data in G.nodes(data=True)
        if data.get("type") == "Organization"
    }
    assert industries
    assert industries <= allowed


# --------------------------------------------------------------------------- #
# contract
# --------------------------------------------------------------------------- #


def test_realistic_topology_is_reproducible():
    a = GraphFaker(seed=42).generate_graph(total_nodes=200, total_edges=600)
    b = GraphFaker(seed=42).generate_graph(total_nodes=200, total_edges=600)
    assert sorted(a.edges()) == sorted(b.edges())
    assert [d.get("community") for _, d in a.nodes(data=True)] == [
        d.get("community") for _, d in b.nodes(data=True)
    ]


def test_node_count_is_exact():
    G = GraphFaker(seed=1).generate_graph(total_nodes=137, total_edges=400)
    assert G.number_of_nodes() == 137


def test_unknown_topology_is_rejected():
    with pytest.raises(ValueError, match="topology must be"):
        GraphFaker(seed=1).generate_graph(total_nodes=50, topology="scale-free")


def test_stats_on_an_empty_graph():
    assert graph_stats(nx.DiGraph()) == {"nodes": 0, "edges": 0}


def test_compare_topology_renders_a_table():
    from graphfaker.metrics import compare_topology

    a = GraphFaker(seed=1).generate_graph(total_nodes=80, total_edges=240)
    b = GraphFaker(seed=1).generate_graph(
        total_nodes=80, total_edges=240, topology="uniform"
    )
    table = compare_topology({"realistic": a, "uniform": b})
    assert "degree_fano" in table
    assert "realistic" in table and "uniform" in table
    assert len(table.splitlines()) > 5
