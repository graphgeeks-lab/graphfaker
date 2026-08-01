# tests/test_seed.py
"""Reproducibility of synthetic generation."""

import networkx as nx

from graphfaker.core import GraphFaker


def _fingerprint(G: nx.Graph):
    """Everything that should be identical between two seeded runs."""
    return (
        sorted(G.nodes()),
        sorted(
            (n, tuple(sorted((k, str(v)) for k, v in d.items())))
            for n, d in G.nodes(data=True)
        ),
        sorted((u, v, d.get("relationship")) for u, v, d in G.edges(data=True)),
    )


def test_same_seed_produces_identical_graph():
    a = GraphFaker(seed=42).generate_graph(
        source="faker", total_nodes=40, total_edges=120
    )
    b = GraphFaker(seed=42).generate_graph(
        source="faker", total_nodes=40, total_edges=120
    )
    assert _fingerprint(a) == _fingerprint(b)


def test_different_seeds_produce_different_graphs():
    a = GraphFaker(seed=1).generate_graph(source="faker", total_nodes=40, total_edges=120)
    b = GraphFaker(seed=2).generate_graph(source="faker", total_nodes=40, total_edges=120)
    assert _fingerprint(a) != _fingerprint(b)


def test_seed_via_generate_graph_argument():
    gf = GraphFaker()
    a = gf.generate_graph(source="faker", total_nodes=30, total_edges=90, seed=7)
    b = gf.generate_graph(source="faker", total_nodes=30, total_edges=90, seed=7)
    assert _fingerprint(a) == _fingerprint(b)


def test_reseed_resets_state():
    gf = GraphFaker(seed=99)
    first = gf.generate_graph(source="faker", total_nodes=30, total_edges=90)
    fingerprint = _fingerprint(first)
    gf.reseed(99)
    second = gf.generate_graph(source="faker", total_nodes=30, total_edges=90)
    assert _fingerprint(second) == fingerprint


def test_seeding_does_not_disturb_global_random():
    import random

    random.seed(123)
    expected = [random.random() for _ in range(3)]

    random.seed(123)
    GraphFaker(seed=42).generate_graph(source="faker", total_nodes=20, total_edges=40)
    actual = [random.random() for _ in range(3)]

    assert actual == expected


def test_unseeded_instances_still_differ():
    a = GraphFaker().generate_graph(source="faker", total_nodes=40, total_edges=120)
    b = GraphFaker().generate_graph(source="faker", total_nodes=40, total_edges=120)
    assert _fingerprint(a) != _fingerprint(b)


def test_unknown_source_names_the_valid_options():
    gf = GraphFaker()
    try:
        gf.generate_graph(source="nonsense")
    except ValueError as exc:
        message = str(exc)
        assert "faker" in message
        assert "osm" in message
        assert "flights" in message
        assert "random" not in message
    else:
        raise AssertionError("expected ValueError for an unknown source")
