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


# --------------------------------------------------- the engine, across dates


def test_a_seeded_dataset_is_the_same_tomorrow():
    """The promise is "the same bytes on any machine", and midnight is a machine.

    ``date_of_birth`` and ``date`` count backwards from today, so reading the
    real clock made the fraud and social packs produce a different birth-date
    column every day: reproducible across machines and not across midnight,
    which is the half nobody checks. The supply chain and coordination packs
    never had it, because they take an explicit ``period_start`` instead, and
    that is the same idea written down.

    Moving the clock eleven years rather than one day, so a drift of a single
    day in a single column still fails this.
    """
    import datetime as dt

    from graphfaker.domains import get
    from graphfaker.engine import fastfaker
    from graphfaker.engine.run import fingerprint

    cases = [
        ("fraud", {"scale": 0.0005, "seed": 42}),
        ("social", {"seed": 3, "total_nodes": 300, "total_edges": 900}),
        ("supply_chain", {"scale": 0.002, "seed": 42}),
        ("coordination", {"scale": 0.0004, "seed": 5}),
    ]
    today = {name: fingerprint(get(name).run(**options)) for name, options in cases}

    real = fastfaker.dt.datetime

    class Later(real):
        @classmethod
        def now(cls, tz=None):
            return real.now(tz) + dt.timedelta(days=4000)

    fastfaker.dt.datetime = Later
    try:
        later = {name: fingerprint(get(name).run(**options)) for name, options in cases}
    finally:
        fastfaker.dt.datetime = real

    assert today == later, "a seeded dataset changed because the calendar did"


def test_the_reference_date_is_a_constant_and_not_the_clock():
    """The one place the engine is allowed to decide what "today" means.

    A direct check as well as the behavioural one above, because the
    behavioural one passes if a future provider reads the clock and happens
    not to be used by any pack yet.
    """
    import datetime as dt
    import inspect

    from graphfaker.engine import fastfaker

    assert isinstance(fastfaker.REFERENCE_DATE, dt.date)
    source = inspect.getsource(fastfaker)
    body = source.split("REFERENCE_DATE = ", 1)[1]
    assert "datetime.now" not in body and ".today()" not in body, (
        "a date provider is reading the clock; derive it from REFERENCE_DATE instead"
    )
