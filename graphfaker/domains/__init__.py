"""Domain packs: ready-made kinds of graph.

Each domain is registered as a :class:`~graphfaker.domains.registry.Domain`
so it can be listed and run by name. See ``docs/adding-a-domain.md`` for how
to add one.
"""

from graphfaker.domains import fraud, social
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
        name="fraud",
        summary="A bank: customers, accounts, merchants, devices; transactions; labelled laundering typologies.",
        generate=fraud.generate,
        options=fraud.FraudConfig,
    )
)

__all__ = ["Domain", "available", "fraud", "get", "register", "social"]
