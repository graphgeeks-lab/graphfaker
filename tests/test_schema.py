"""The schema layer: declaration, validation, serialisation."""

import pytest
from pydantic import ValidationError

from graphfaker.domains import social
from graphfaker.schema import (
    CategorySampler,
    EdgeType,
    ForeignKeySampler,
    GaussianSampler,
    GraphSchema,
    LatentFactor,
    NodeType,
    Relationship,
    SocialTopology,
    UniformTopology,
)


def minimal(**overrides):
    base = {
        "name": "tiny",
        "latent": [LatentFactor(name="community", groups=2)],
        "nodes": [NodeType(name="A", count=3), NodeType(name="B", count=2)],
        "edges": [
            EdgeType(source="A", target="B", share=1.0, relationships=[Relationship(name="TO")])
        ],
        "total_edges": 4,
    }
    base.update(overrides)
    return GraphSchema(**base)


def test_social_schema_round_trips_through_yaml():
    schema = social.schema(total_nodes=120, total_edges=500, communities=3)
    text = schema.to_yaml()
    assert GraphSchema.from_yaml(text) == schema
    assert GraphSchema.from_dict(schema.to_dict()) == schema


def test_social_schema_round_trips_through_yaml_file(tmp_path):
    schema = social.schema(total_nodes=50, total_edges=100)
    path = tmp_path / "social.yaml"
    schema.to_yaml(path)
    assert GraphSchema.from_yaml(path) == schema


def test_digest_is_stable_and_content_sensitive():
    a = social.schema(100, 1000)
    assert a.digest() == social.schema(100, 1000).digest()
    assert a.digest() != social.schema(101, 1000).digest()
    assert a.digest() != social.schema(100, 1000, topology="uniform").digest()


def test_node_counts_add_up():
    schema = social.schema(total_nodes=137)
    assert schema.total_nodes == 137


def test_generation_order_puts_foreign_key_targets_first():
    order = [node.name for node in social.schema(100).generation_order()]
    assert order.index("Place") < order.index("Person")


def test_unknown_latent_reference_is_rejected():
    with pytest.raises(ValidationError, match="unknown latent parameter"):
        minimal(
            nodes=[
                NodeType(
                    name="A",
                    count=1,
                    attributes={"age": GaussianSampler(mean="@community.mean_age", sd=1)},
                ),
                NodeType(name="B", count=1),
            ]
        )


def test_reference_must_look_like_factor_dot_param():
    with pytest.raises(ValidationError):
        GaussianSampler(mean="@community", sd=1)


def test_foreign_key_to_unknown_type_is_rejected():
    with pytest.raises(ValidationError, match="unknown node type"):
        minimal(
            nodes=[
                NodeType(name="A", count=1, attributes={"b": ForeignKeySampler(node_type="Z")}),
                NodeType(name="B", count=1),
            ]
        )


def test_foreign_key_cycle_is_rejected():
    schema = minimal(
        nodes=[
            NodeType(name="A", count=1, attributes={"b": ForeignKeySampler(node_type="B")}),
            NodeType(name="B", count=1, attributes={"a": ForeignKeySampler(node_type="A")}),
        ]
    )
    with pytest.raises(ValueError, match="cycle"):
        schema.generation_order()


def test_edge_shares_must_sum_to_one():
    with pytest.raises(ValidationError, match="shares must sum to 1"):
        minimal(
            edges=[
                EdgeType(source="A", target="B", share=0.5, relationships=[Relationship(name="X")]),
            ]
        )


def test_edge_to_unknown_type_is_rejected():
    with pytest.raises(ValidationError, match="unknown node type"):
        minimal(
            edges=[
                EdgeType(source="A", target="Q", share=1.0, relationships=[Relationship(name="X")])
            ]
        )


def test_target_from_must_be_a_source_attribute():
    with pytest.raises(ValidationError, match="target_from"):
        minimal(
            edges=[
                EdgeType(
                    source="A",
                    target="B",
                    share=1.0,
                    relationships=[Relationship(name="X", target_from="home")],
                )
            ]
        )


def test_closure_requires_same_type():
    with pytest.raises(ValidationError, match="closure"):
        EdgeType(
            source="A", target="B", share=1.0, closure=True, relationships=[Relationship(name="X")]
        )


def test_topology_group_must_be_a_latent_factor():
    with pytest.raises(ValidationError, match="not a latent factor"):
        minimal(topology=SocialTopology(group="tribe"))


def test_uniform_topology_needs_no_latent_factor():
    schema = minimal(latent=[], topology=UniformTopology())
    assert schema.topology.kind == "uniform"


def test_reserved_attribute_names():
    with pytest.raises(ValidationError, match="reserved"):
        NodeType(name="A", count=1, attributes={"id": CategorySampler(values=[1])})


def test_category_weights_must_match_values():
    with pytest.raises(ValidationError):
        CategorySampler(values=["a", "b"], weights=[1.0])


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        NodeType(name="A", count=1, colour="red")
