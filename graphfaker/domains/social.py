"""The social / knowledge graph domain.

This is the generator GraphFaker shipped with from the start — people, places,
organizations, events and products — re-expressed as a schema. Nothing about
the graph it produces is meant to change; what changes is that every choice
that used to be a constant in ``core.py`` is now declared here where a user
can read it, tune it, and serialise it.
"""

from __future__ import annotations

from graphfaker.schema import (
    CategorySampler,
    DegreeDerived,
    EdgeType,
    ExpressionSampler,
    FakerSampler,
    ForeignKeySampler,
    GaussianSampler,
    GraphSchema,
    LatentFactor,
    LognormalSampler,
    MixtureComponent,
    MixtureSampler,
    NodeType,
    NumericAffinity,
    RealismTargets,
    ReferenceSampler,
    Relationship,
    SocialTopology,
    SubcategorySampler,
    UniformSampler,
    UniformTopology,
)

PERSON_SUBTYPES = ["Student", "Professional", "Retiree", "Unemployed"]
PLACE_SUBTYPES = ["City", "Park", "Restaurant", "Airport", "University"]
ORG_SUBTYPES = ["TechCompany", "Hospital", "NGO", "University", "RetailChain"]
EVENT_SUBTYPES = ["Concert", "Conference", "Protest", "SportsGame"]
PRODUCT_SUBTYPES = ["Electronics", "Apparel", "Book", "Vehicle"]
EDUCATION_LEVELS = ["High School", "Bachelor", "Master", "PhD"]

#: Industries, not job titles: an organization whose industry was "Chartered
#: accountant" made attribute-based matching meaningless.
INDUSTRY_BY_SUBTYPE = {
    "TechCompany": ["Software", "Semiconductors", "Cloud Infrastructure", "Robotics"],
    "Hospital": ["Healthcare", "Medical Research", "Elder Care"],
    "NGO": ["Humanitarian Aid", "Conservation", "Human Rights", "Education Access"],
    "University": ["Higher Education", "Research"],
    "RetailChain": ["Grocery", "Apparel Retail", "Consumer Electronics", "Home Goods"],
}

#: Share of nodes per type.
NODE_MIX = {
    "Person": 0.50,
    "Place": 0.20,
    "Organization": 0.15,
    "Event": 0.10,
    # Product takes the remainder.
}

#: Heavy-tailed scale factor shared by places, organizations, events and
#: products. Lognormal rather than uniform: it is the difference between a
#: graph where every place is the same size and one with a handful of hubs.
PROMINENCE = LognormalSampler(mu=0.0, sigma=1.1, decimals=4)

#: Literature-derived expectations for a social graph of this kind: strongly
#: unequal degrees, clustering well above random, recoverable communities and
#: positive age assortativity.
TARGETS = RealismTargets(
    degree_gini_min=0.4,
    average_clustering_min=0.1,
    community_modularity_min=0.35,
    assortativity={"age": 0.3},
)


def default_communities(total_nodes: int) -> int:
    """About one latent group per 25 nodes, bounded to [2, 12]."""
    return max(2, min(12, total_nodes // 25 or 2))


def node_counts(total_nodes: int) -> dict[str, int]:
    counts = {name: int(total_nodes * share) for name, share in NODE_MIX.items()}
    counts["Product"] = total_nodes - sum(counts.values())
    return counts


def schema(
    total_nodes: int = 100,
    total_edges: int = 1000,
    topology: str = "realistic",
    communities: int | None = None,
) -> GraphSchema:
    """Build the social graph schema.

    Args:
        total_nodes: Split across types by :data:`NODE_MIX`.
        total_edges: Edge budget; ``number_of_edges()`` will match to ±10%.
        topology: ``"realistic"`` or ``"uniform"``.
        communities: Latent groups; defaults to :func:`default_communities`.
    """
    if topology not in ("realistic", "uniform"):
        raise ValueError(f"topology must be 'realistic' or 'uniform', got {topology!r}")
    counts = node_counts(total_nodes)
    groups = communities if communities is not None else default_communities(total_nodes)

    community = LatentFactor(
        name="community",
        groups=groups,
        params={
            "mean_age": UniformSampler(low=24, high=62, integer=True),
            "education": CategorySampler(values=EDUCATION_LEVELS),
            "region": FakerSampler(provider="city"),
        },
    )

    place = NodeType(
        name="Place",
        count=counts["Place"],
        attributes={
            "name": FakerSampler(provider="city"),
            "place_type": CategorySampler(values=PLACE_SUBTYPES),
            "prominence": PROMINENCE,
            # Population tracks prominence, so the biggest cities are also the
            # most connected ones.
            "population": ExpressionSampler(
                expr="int(8000 * (1 + prominence) ** 2.2) + rand.randint(0, 5000)"
            ),
            "latitude": FakerSampler(provider="latitude", as_type="float"),
            "longitude": FakerSampler(provider="longitude", as_type="float"),
        },
    )

    person = NodeType(
        name="Person",
        count=counts["Person"],
        attributes={
            "name": FakerSampler(provider="name"),
            # Age clusters around the community mean instead of spanning the
            # whole adult range, which is what produces measurable age
            # homophily across edges.
            "age": GaussianSampler(
                mean="@community.mean_age", sd=9, low=18, high=80, integer=True
            ),
            "occupation": FakerSampler(provider="job"),
            "email": FakerSampler(provider="email"),
            "education_level": MixtureSampler(
                components=[
                    MixtureComponent(
                        weight=0.55, sampler=ReferenceSampler(ref="@community.education")
                    ),
                    MixtureComponent(
                        weight=0.45, sampler=CategorySampler(values=EDUCATION_LEVELS)
                    ),
                ]
            ),
            "skills": FakerSampler(provider="words", kwargs={"nb": 3}, join=", "),
            "subtype": CategorySampler(values=PERSON_SUBTYPES),
            # A home drawn from the person's own community's places.
            "home_place": ForeignKeySampler(node_type="Place", same_group="community"),
        },
    )

    organization = NodeType(
        name="Organization",
        count=counts["Organization"],
        id_prefix="org",
        attributes={
            "name": FakerSampler(provider="company"),
            "subtype": CategorySampler(values=ORG_SUBTYPES),
            "industry": SubcategorySampler(parent="subtype", values=INDUSTRY_BY_SUBTYPE),
            "prominence": PROMINENCE,
            "revenue": ExpressionSampler(expr="2.5e5 * (1 + prominence) ** 3", decimals=2),
        },
        derived={
            # Employees present in the graph are a sample, not the whole
            # workforce, so scale the WORKS_AT count up by prominence.
            "employee_count": DegreeDerived(
                relationship="WORKS_AT",
                direction="in",
                expr="max(count, int(count * (12 + 40 * prominence)) or 1)",
            ),
        },
    )

    event = NodeType(
        name="Event",
        count=counts["Event"],
        attributes={
            "name": FakerSampler(provider="catch_phrase"),
            "event_type": CategorySampler(values=EVENT_SUBTYPES),
            "start_date": FakerSampler(provider="date"),
            "duration": UniformSampler(low=1, high=5, integer=True),
            "prominence": PROMINENCE,
        },
    )

    product = NodeType(
        name="Product",
        count=counts["Product"],
        attributes={
            "name": FakerSampler(provider="word", transform="capitalize"),
            "category": CategorySampler(values=PRODUCT_SUBTYPES),
            "prominence": PROMINENCE,
            "price": ExpressionSampler(expr="12 * (1 + prominence) ** 2.5", decimals=2),
            "release_date": FakerSampler(provider="date"),
        },
    )

    edges = [
        EdgeType(
            source="Person",
            target="Person",
            share=0.40,
            closure=True,
            relationships=[
                Relationship(name="FRIENDS_WITH", bidirectional=True),
                Relationship(name="COLLEAGUES", bidirectional=True),
                Relationship(name="MENTORS"),
            ],
        ),
        EdgeType(
            source="Person",
            target="Place",
            share=0.20,
            relationships=[
                Relationship(name="LIVES_IN", functional=True, target_from="home_place"),
                Relationship(
                    name="VISITED",
                    attributes={"visit_count": UniformSampler(low=1, high=20, integer=True)},
                ),
                Relationship(name="BORN_IN", functional=True),
            ],
        ),
        EdgeType(
            source="Person",
            target="Organization",
            share=0.15,
            relationships=[
                Relationship(
                    name="WORKS_AT", attributes={"position": FakerSampler(provider="job")}
                ),
                Relationship(name="STUDIED_AT"),
                Relationship(name="OWNS"),
            ],
        ),
        EdgeType(
            source="Organization",
            target="Place",
            share=0.10,
            relationships=[
                Relationship(name="HEADQUARTERED_IN", functional=True),
                Relationship(name="HAS_BRANCH"),
            ],
        ),
        EdgeType(
            source="Person",
            target="Event",
            share=0.08,
            relationships=[Relationship(name="ATTENDED"), Relationship(name="ORGANIZED")],
        ),
        EdgeType(
            source="Organization",
            target="Product",
            share=0.05,
            relationships=[Relationship(name="MANUFACTURES"), Relationship(name="SELLS")],
        ),
        EdgeType(
            source="Person",
            target="Product",
            share=0.02,
            relationships=[
                Relationship(
                    name="PURCHASED",
                    attributes={
                        "date": FakerSampler(provider="date"),
                        "amount": UniformSampler(low=1, high=500, decimals=2),
                    },
                ),
                Relationship(
                    name="REVIEWED",
                    attributes={"rating": UniformSampler(low=1, high=5, integer=True)},
                ),
            ],
        ),
    ]

    if topology == "uniform":
        model = UniformTopology()
    else:
        model = SocialTopology(
            group="community",
            prominence="prominence",
            numeric_affinity={"age": NumericAffinity(weight=0.8, scale=35.0)},
            categorical_affinity={"education_level": 0.25},
        )

    return GraphSchema(
        name="social",
        latent=[community],
        nodes=[place, person, organization, event, product],
        edges=edges,
        total_edges=total_edges,
        topology=model,
        targets=TARGETS if topology == "realistic" else None,
    )
