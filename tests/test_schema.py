"""The schema layer: declaration, validation, serialisation."""

import pytest
from pydantic import ValidationError

from graphfaker.domains import social
from graphfaker.schema import (
    CategorySampler,
    ConstantSampler,
    DegreeDerived,
    EdgeType,
    ExpressionSampler,
    FakerSampler,
    ForeignKeySampler,
    GaussianSampler,
    GraphSchema,
    LatentFactor,
    NodeType,
    ReferenceSampler,
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


def test_a_null_that_overrides_a_default_survives_the_round_trip():
    """The one place ``exclude_none`` was wrong.

    A written schema drops every optional nobody set, which keeps it
    readable. ``SocialTopology.prominence`` defaults to the attribute name
    ``prominence`` and is set to null to say there is no popularity
    attribute at all, so dropping that null brought the default back: the
    file said one thing and loading it meant another. A schema that does
    not round-trip is a dataset that cannot be regenerated from its own
    file, which is the promise the manifest makes.
    """
    from graphfaker.schema.topology import SocialTopology

    schema = social.schema(120, 500).model_copy(
        update={"topology": SocialTopology(prominence=None)}
    )
    assert "prominence: null" in schema.to_yaml()
    reloaded = GraphSchema.from_yaml(schema.to_yaml())
    assert reloaded.topology.prominence is None
    assert reloaded == schema
    assert reloaded.digest() == schema.digest()

    # The nulls that mean nothing are still dropped, because a schema full
    # of empty optionals is a schema nobody reads. Every optional the social
    # pack leaves alone defaults to None, so a written one has no nulls at all.
    assert "null" not in social.schema(120, 500).to_yaml()


def test_an_expression_can_only_use_names_it_will_have():
    """Caught when the file is read, not halfway through a run.

    An expression used to be checked by compiling it, so a name that is not
    in scope validated cleanly and raised a ``NameError`` a minute into
    generation: ``graphfaker validate`` said the file was fine and the
    generator disagreed. Reaching for a module that is not there is the
    commonest mistake in a hand-written schema and in a drafted one.
    """
    from graphfaker.schema.samplers import ExpressionSampler, GaussianSampler

    with pytest.raises(ValidationError, match="datetime"):
        minimal(nodes=[NodeType(name="A", count=5, attributes={
            "start": GaussianSampler(mean=0, sd=1),
            "end": ExpressionSampler(expr="start + datetime.timedelta(days=1)"),
        })])

    # Attributes are ordered, so an expression cannot see one declared later.
    with pytest.raises(ValidationError, match="later"):
        minimal(nodes=[NodeType(name="A", count=5, attributes={
            "early": ExpressionSampler(expr="later * 2"),
            "later": GaussianSampler(mean=0, sd=1),
        })])

    # What it can see: earlier attributes, math, rand, and timedelta, which
    # is there so that "discharged some days after admission" can be said
    # without handing a schema a clock to read.
    ok = minimal(
        edges=[],
        total_edges=0,
        nodes=[NodeType(name="A", count=5, attributes={
        "start": GaussianSampler(mean=0, sd=1),
        "jitter": ExpressionSampler(expr="round(start * math.pi + rand.random(), 2)"),
        "span": ExpressionSampler(expr="timedelta(days=rand.randint(1, 20)).days"),
    })])
    assert ok.node_type("A").attributes["span"].expr.startswith("timedelta")


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


def test_a_schema_that_reads_the_clock_can_be_spotted():
    """Not an error: a schema is allowed to want the clock.

    The engine's own date providers count from a fixed reference date, so a
    seeded dataset is the same on any day. A provider it does not implement
    falls through to Faker, which reads the clock when asked, and
    ``end_date: now`` is asking. Finding that out by diffing two runs a day
    apart is a bad way to find it out, so ``graphfaker validate`` says so.
    """
    from graphfaker.schema.samplers import FakerSampler, clock_dependent

    assert clock_dependent(FakerSampler(provider="past_date"))
    assert clock_dependent(FakerSampler(provider="date_time_between", kwargs={"end_date": "now"}))
    assert clock_dependent(FakerSampler(provider="date_time_between", kwargs={"start_date": "-2y"}))
    # Pinned dates, and the engine's own providers, are fine.
    assert clock_dependent(
        FakerSampler(provider="date_time_between", kwargs={"start_date": "2025-01-01", "end_date": "2026-01-01"})
    ) is None
    assert clock_dependent(FakerSampler(provider="date_of_birth", kwargs={"maximum_age": 80})) is None
    assert clock_dependent(FakerSampler(provider="name")) is None


def test_an_expression_that_does_not_parse_is_a_validation_error():
    """A ``SyntaxError`` walks straight out of a pydantic field validator.

    So a schema with a typo in an expression crashed the command that was
    supposed to report it, and anything catching schema errors, including
    the drafter's repair round, never saw it.
    """
    from graphfaker.schema.samplers import ExpressionSampler

    with pytest.raises(ValidationError, match="not a valid expression"):
        ExpressionSampler(expr="x if y else")
    with pytest.raises(ValidationError, match="not a valid expression"):
        minimal(nodes=[NodeType(name="A", count=2, attributes={"a": ExpressionSampler(expr="1 +")})])
    with pytest.raises(ValidationError, match="not a valid expression"):
        minimal(nodes=[NodeType(
            name="A", count=2,
            attributes={"n": ConstantSampler(value=1)},
            derived={"d": DegreeDerived(relationship="TO", expr="count +")},
        )])


# ------------------------------------------------------- reading one back


def test_every_sampler_has_a_short_form():
    """Generated from the union, so a new sampler cannot be left out."""
    import typing

    from graphfaker.schema.samplers import Sampler, describe_sampler

    for model in typing.get_args(typing.get_args(Sampler)[0]):
        kind = model.model_fields["kind"].default
        assert kind in _SAMPLER_FORMS, f"{kind} has no expected short form in this test"
        assert describe_sampler(_SAMPLER_FORMS[kind][0]) == _SAMPLER_FORMS[kind][1], kind


def _sampler_forms():
    from graphfaker.schema.samplers import (
        BernoulliSampler,
        ForeignKeySampler,
        LognormalSampler,
        MixtureComponent,
        MixtureSampler,
        PoissonSampler,
        SubcategorySampler,
        UniformSampler,
    )

    return {
        "constant": (ConstantSampler(value="USD"), "constant(USD)"),
        "category": (CategorySampler(values=["a", "b", "c"]), "category(3)"),
        "subcategory": (SubcategorySampler(parent="specialty", values={"x": ["y"]}), "subcategory(by specialty)"),
        "uniform": (UniformSampler(low=1, high=10), "uniform(1, 10)"),
        # Trailing zeros taken off, because a column of them is unreadable.
        "gaussian": (GaussianSampler(mean=52.0, sd=19.5), "gaussian(52, 19.5)"),
        "lognormal": (LognormalSampler(mu=0.0, sigma=1.3), "lognormal(0, 1.3)"),
        "poisson": (PoissonSampler(lam=4.2), "poisson(4.2)"),
        "bernoulli": (BernoulliSampler(p=0.004), "bernoulli(0.004)"),
        "faker": (FakerSampler(provider="name"), "faker(name)"),
        "expression": (ExpressionSampler(expr="1 + 1"), "expression"),
        "reference": (ReferenceSampler(ref="@community.region"), "@community.region"),
        "mixture": (
            MixtureSampler(components=[
                MixtureComponent(weight=0.6, sampler=ReferenceSampler(ref="@community.region")),
                MixtureComponent(weight=0.4, sampler=CategorySampler(values=["a", "b"])),
            ]),
            "mixture(@community.region + category(2))",
        ),
        "foreign_key": (ForeignKeySampler(node_type="Patient"), "-> Patient"),
    }


_SAMPLER_FORMS = _sampler_forms()


def test_describe_answers_the_questions_a_draft_has():
    """The ratio per source row is the line that earns this feature.

    A schema states a share of a total edge budget, and nothing in the file
    says whether that works out to one payment per purchase or half of one.
    On a drafted schema that arithmetic was the whole finding.
    """
    schema = minimal(
        nodes=[
            NodeType(name="Patient", count=4000, attributes={
                "age": GaussianSampler(mean=52, sd=19),
                "band": ReferenceSampler(ref="@community.band"),
            }),
            NodeType(name="Doctor", count=250, attributes={"name": FakerSampler(provider="name")}),
        ],
        latent=[LatentFactor(name="community", groups=8, params={"band": CategorySampler(values=["a", "b"])})],
        edges=[
            EdgeType(source="Patient", target="Doctor", share=0.5, relationships=[
                Relationship(name="TREATED_BY"),
                Relationship(name="PRIMARY_PHYSICIAN", functional=True),
            ]),
            EdgeType(source="Doctor", target="Doctor", share=0.5, closure=True, relationships=[
                Relationship(name="CONSULTS_WITH"),
            ]),
        ],
        total_edges=4000,
    )
    text = "\n".join(schema.describe())

    assert "4,250 nodes, 4,000 edges, social topology" in text
    assert "latent  community x8: band category(2)" in text
    # Samplers and their parameters, because a gaussian where the column
    # means money is the commonest thing wrong with a written schema.
    assert "age gaussian(52, 19)" in text and "band @community.band" in text
    # 2,000 edges over 4,000 patients.
    assert "0.50 per Patient" in text
    # 2,000 over 250 doctors, on a family that closes triangles.
    assert "8.00 per Doctor" in text and "closes triangles" in text
    assert "PRIMARY_PHYSICIAN*" in text and "TREATED_BY," in text
    assert "* functional" in text


def test_describe_says_when_there_is_no_latent_factor():
    """A schema with none is a pile of independent columns, so it is worth
    a line rather than an absent section."""
    # Uniform topology, because the default social one reads a latent
    # factor and a schema with none has to say which it is.
    bare = GraphSchema(name="flat", nodes=[NodeType(name="A", count=5)], topology=UniformTopology())
    assert any("latent  none" in line for line in bare.describe())
    # And no edge block at all, which is what the packs look like: their
    # edges come from code rather than from the schema.
    assert not any("per A" in line for line in bare.describe())
    assert not any("functional" in line for line in bare.describe())


def test_describe_survives_every_shipped_schema():
    from graphfaker.domains import available

    for name, pack in sorted(available().items()):
        if pack.schema is None:
            continue
        schema = pack.schema(**(pack.options().model_dump() if pack.options else {}))
        lines = schema.describe()
        assert lines and lines[0].startswith(f"{schema.name}:"), name
        for node in schema.nodes:
            assert any(node.name in line for line in lines), (name, node.name)
