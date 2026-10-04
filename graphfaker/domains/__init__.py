"""Domain packs: ready-made kinds of graph.

Each domain is registered as a :class:`~graphfaker.domains.registry.Domain`
so it can be listed and run by name. See ``docs/adding-a-domain.md`` for how
to add one.
"""

from graphfaker.domains import coordination, fraud, social
from graphfaker.domains.registry import Domain, available, get, register

register(
    Domain(
        name="social",
        summary="People, places, organizations, events and products with realistic social structure.",
        generate=social.generate,
        options=social.SocialOptions,
        schema=social.schema,
    )
)
register(
    Domain(
        name="coordination",
        summary=(
            "A social platform: accounts, topics, devices; follows, posts, reshares and replies "
            "over time; labelled coordination campaigns with organic decoys."
        ),
        generate=coordination.generate,
        options=coordination.CoordinationConfig,
        schema=lambda **options: coordination_schema(options),
        schema_note=(
            "This is the entity half of the coordination domain: its node types, attribute samplers\n"
            "and the latent interest-community factor. The follow graph, the activity stream and the\n"
            "campaigns come from a process in code (graphfaker/domains/coordination/process.py,\n"
            "playbooks.py), not from the schema, so generating from this file gives the entities only.\n"
            "Use `graphfaker generate coordination` for the platform."
        ),
    )
)
register(
    Domain(
        name="fraud",
        summary="A bank: customers, accounts, merchants, devices; transactions; labelled laundering typologies.",
        generate=fraud.generate,
        options=fraud.FraudConfig,
        schema=lambda **options: fraud.entities.schema(fraud.FraudConfig(**options)),
        schema_note=(
            "This is the entity half of the fraud domain: its node types, attribute samplers and the\n"
            "latent region factor. The transactions and the laundering patterns come from a process\n"
            "in code (graphfaker/domains/fraud/process.py, typologies.py), not from the schema, so\n"
            "generating from this file gives the entities only. Use `graphfaker generate fraud` for the bank."
        ),
    )
)

def coordination_schema(options: dict):
    """The coordination pack's entity schema, for ``graphfaker schema``."""
    from graphfaker.domains.coordination import entities

    return entities.schema(coordination.CoordinationConfig(**options))


__all__ = [
    "Domain",
    "available",
    "coordination",
    "fraud",
    "get",
    "register",
    "social",
]
