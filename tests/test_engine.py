"""The engine: seeding, sharding, tables, manifests."""

import subprocess
import sys

import networkx as nx
import polars as pl
import pytest

from graphfaker import GraphFaker
from graphfaker.backends import GraphTables
from graphfaker.domains import social
from graphfaker.engine import GraphRun, Manifest, Streams, fingerprint, generate
from graphfaker.engine.sampling import shard_bounds
from graphfaker.schema import (
    CategorySampler,
    DegreeDerived,
    EdgeType,
    ExpressionSampler,
    GraphSchema,
    LatentFactor,
    NodeType,
    ReferenceSampler,
    Relationship,
    SubcategorySampler,
    UniformSampler,
    UniformTopology,
)


@pytest.fixture(scope="module")
def run() -> GraphRun:
    return generate(social.schema(total_nodes=200, total_edges=800), seed=7)


# ------------------------------------------------------------------- seeding


def test_streams_are_reproducible():
    a, b = Streams.root(3), Streams.root(3)
    assert a.rand.random() == b.rand.random()
    assert a.rng.integers(0, 1_000_000) == b.rng.integers(0, 1_000_000)
    assert a.fake.name() == b.fake.name()


def test_spawned_children_are_independent_and_ordered():
    first = Streams.root(3).spawn(3)
    second = Streams.root(3).spawn(3)
    draws = [child.rand.random() for child in first]
    assert draws == [child.rand.random() for child in second]
    assert len(set(draws)) == 3


def test_shard_bounds_cover_the_count_exactly():
    assert shard_bounds(0, 10) == []
    assert shard_bounds(25, 10) == [(0, 10), (10, 10), (20, 5)]
    assert sum(length for _, length in shard_bounds(1234, 100)) == 1234


# -------------------------------------------------------------- generation


def test_same_seed_same_shard_size_is_byte_identical():
    schema = social.schema(150, 600)
    assert fingerprint(generate(schema, seed=1)) == fingerprint(generate(schema, seed=1))


def test_different_seeds_differ():
    schema = social.schema(150, 600)
    assert fingerprint(generate(schema, seed=1)) != fingerprint(generate(schema, seed=2))


def test_unseeded_runs_differ():
    schema = social.schema(150, 600)
    assert fingerprint(generate(schema)) != fingerprint(generate(schema))


def test_reproducible_across_processes():
    """Set iteration order depends on PYTHONHASHSEED; a seeded run must not."""
    code = (
        "from graphfaker.domains import social;"
        "from graphfaker.engine import generate, fingerprint;"
        "print(fingerprint(generate(social.schema(150, 600), seed=11)))"
    )
    outputs = set()
    for hash_seed in ("1", "2"):
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**__import__("os").environ, "PYTHONHASHSEED": hash_seed},
        )
        outputs.add(result.stdout.strip())
    assert len(outputs) == 1


def test_node_tables_follow_the_schema(run: GraphRun):
    schema = run.schema
    assert list(run.tables.nodes) == [node.name for node in schema.nodes]
    for node in schema.nodes:
        frame = run.tables.nodes[node.name]
        assert frame.height == node.count
        assert frame["id"].to_list() == [f"{node.prefix}_{i}" for i in range(node.count)]
        for attr in node.attributes:
            assert attr in frame.columns
        assert "community" in frame.columns


def test_edge_tables_are_keyed_by_relationship(run: GraphRun):
    declared = {rel.name for _, rel in run.schema.relationships()}
    assert set(run.tables.edges) <= declared
    for frame in run.tables.edges.values():
        assert frame.columns[:2] == ["source", "target"]


def test_edge_attributes_are_sampled(run: GraphRun):
    visited = run.tables.edges["VISITED"]
    assert visited["visit_count"].min() >= 1 and visited["visit_count"].max() <= 20
    purchased = run.tables.edges["PURCHASED"]
    assert purchased["amount"].dtype == pl.Float64


def test_home_place_is_in_the_same_community(run: GraphRun):
    people = run.tables.nodes["Person"]
    places = run.tables.nodes["Place"].select(["id", "community"]).rename({"id": "home_place"})
    joined = people.join(places, on="home_place", suffix="_place")
    same = (joined["community"] == joined["community_place"]).mean()
    # Falls back to any place only when a community has none.
    assert same > 0.9


def test_derived_attribute_agrees_with_structure(run: GraphRun):
    G = run.to_networkx()
    for node, data in G.nodes(data=True):
        if data["type"] != "Organization":
            continue
        employed = sum(
            1 for _, _, e in G.in_edges(node, data=True) if e["relationship"] == "WORKS_AT"
        )
        assert data["employee_count"] >= max(employed, 1)


def test_truth_records_group_parameters(run: GraphRun):
    community = run.truth["community"]
    assert community.columns == ["group", "mean_age", "education", "region"]
    assert community.height == run.schema.latent_factor("community").groups


def test_manifest_pins_the_run(run: GraphRun):
    m = run.manifest
    assert m.schema_digest == run.schema.digest()
    assert m.seed == 7
    assert sum(m.node_counts.values()) == 200
    assert m.edge_counts == {rel: f.height for rel, f in run.tables.edges.items()}


def test_uniform_topology_runs():
    run = generate(social.schema(100, 400, topology="uniform"), seed=1)
    assert run.tables.edge_count > 0


def test_node_only_schema():
    schema = social.schema(50, total_edges=0)
    run = generate(schema, seed=1)
    assert run.tables.edge_count == 0 and run.tables.node_count == 50


def test_samplers_compose():
    schema = GraphSchema(
        name="compose",
        latent=[LatentFactor(name="g", groups=2, params={"flavour": CategorySampler(values=["x", "y"])})],
        nodes=[
            NodeType(
                name="A",
                count=40,
                attributes={
                    "kind": CategorySampler(values=["p", "q"]),
                    "sub": SubcategorySampler(parent="kind", values={"p": ["p1"], "q": ["q1", "q2"]}),
                    "flavour": ReferenceSampler(ref="@g.flavour"),
                    "n": UniformSampler(low=1, high=3, integer=True),
                    "double": ExpressionSampler(expr="n * 2"),
                },
                derived={"loops": DegreeDerived(relationship="LOOP", direction="out")},
            )
        ],
        edges=[EdgeType(source="A", target="A", share=1.0, relationships=[Relationship(name="LOOP")])],
        total_edges=30,
        topology=UniformTopology(),
    )
    frame = generate(schema, seed=5).tables.nodes["A"]
    assert set(frame["sub"].to_list()) <= {"p1", "q1", "q2"}
    assert all(s.startswith(k) for k, s in zip(frame["kind"], frame["sub"]))
    assert set(frame["flavour"].to_list()) <= {"x", "y"}
    assert (frame["double"] == frame["n"] * 2).all()
    assert frame["loops"].sum() > 0


# ------------------------------------------------------------------- tables


def test_tables_round_trip_through_networkx(run: GraphRun):
    G = run.to_networkx()
    assert G.number_of_nodes() == run.tables.node_count
    assert G.number_of_edges() == run.tables.edge_count
    back = GraphTables.from_networkx(G)
    assert {t: f.height for t, f in back.nodes.items()} == {
        t: f.height for t, f in run.tables.nodes.items()
    }
    assert {r: f.height for r, f in back.edges.items()} == {
        r: f.height for r, f in run.tables.edges.items()
    }


def test_networkx_view_has_the_expected_layout(run: GraphRun):
    G = run.to_networkx()
    person = next(d for _, d in G.nodes(data=True) if d["type"] == "Person")
    assert {"name", "age", "email", "community", "home_place"} <= set(person)
    assert all("relationship" in d for _, _, d in G.edges(data=True))
    assert all(v is not None for _, d in G.nodes(data=True) for v in d.values())


def test_run_writes_and_tables_read_back(tmp_path, run: GraphRun):
    root = run.write(tmp_path / "out")
    assert (root / "schema.yaml").exists()
    assert (root / "truth" / "community.parquet").exists()
    manifest = Manifest.read(root / "manifest.json")
    assert manifest.schema_digest == run.schema.digest()
    tables = GraphTables.read_parquet(root)
    assert tables.node_count == run.tables.node_count
    assert tables.edge_count == run.tables.edge_count
    assert GraphSchema.from_yaml(root / "schema.yaml") == run.schema


# ----------------------------------------------------------------- facade


def test_graphfaker_generate_exposes_the_run():
    gf = GraphFaker(seed=3)
    run = gf.generate(social.schema(80, 300))
    assert gf.run is run
    assert isinstance(gf.G, nx.DiGraph)
    assert gf.G.number_of_nodes() == 80


def test_two_step_generation_still_works():
    gf = GraphFaker(seed=3)
    gf.generate_nodes(total_nodes=80)
    assert gf.G.number_of_edges() == 0
    gf.generate_edges(total_edges=300)
    assert 270 <= gf.G.number_of_edges() <= 330


def test_worker_pool_produces_the_same_bytes(monkeypatch):
    """Shards drawn in worker processes, with the foreign-key index arriving
    through the temp file, equal the in-process result. The row threshold
    is lowered so a small graph actually goes to the pool."""
    from graphfaker.domains import fraud
    from graphfaker.engine import sampling

    single = fraud.generate(scale=0.001, seed=5, shard_size=1000)
    monkeypatch.setattr(sampling, "PARALLEL_MIN_ROWS", 0)
    pooled = fraud.generate(scale=0.001, seed=5, shard_size=1000, workers=2)
    assert fingerprint(pooled) == fingerprint(single)
    assert pooled.tables.nodes["Account"]["customer"].to_list() == single.tables.nodes["Account"]["customer"].to_list()


def test_small_node_types_stay_in_process(monkeypatch):
    """Below the threshold the pool is never used, whatever ``workers`` says."""
    from graphfaker.engine import sampling

    calls = []
    monkeypatch.setattr(sampling, "ProcessPoolExecutor", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(AssertionError("pool used")))
    run = generate(social.schema(120, 400), seed=2, workers=4)
    assert run.tables.nodes["Person"].height > 0 and not calls
