"""The fraud / AML domain pack: a bank's customers, accounts, merchants and
devices; a realistic transaction process; injected, labelled laundering
typologies with a measurable hardness. See ``docs/design/synthetic-at-scale.md`` §6."""

from graphfaker.domains.fraud.config import TYPOLOGIES, FraudConfig, HardnessProfile
from graphfaker.domains.fraud.generate import generate

__all__ = ["TYPOLOGIES", "FraudConfig", "HardnessProfile", "generate"]
