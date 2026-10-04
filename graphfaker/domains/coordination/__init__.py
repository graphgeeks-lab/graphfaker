"""The coordinated-behaviour domain pack: a social platform's accounts, topics
and devices; an organic activity process; injected, labelled coordination
campaigns with organic decoys and a measurable tradecraft level."""

from graphfaker.domains.coordination.config import (
    DECOY_PLAYBOOKS,
    PLAYBOOKS,
    CoordinationConfig,
    TradecraftProfile,
)
from graphfaker.domains.coordination.evaluate import Evaluation, evaluate
from graphfaker.domains.coordination.generate import generate
from graphfaker.domains.coordination.hardness import (
    HardnessReport,
    account_features,
    hardness_report,
    realism_report,
)

__all__ = [
    "DECOY_PLAYBOOKS",
    "PLAYBOOKS",
    "CoordinationConfig",
    "Evaluation",
    "HardnessReport",
    "TradecraftProfile",
    "account_features",
    "evaluate",
    "generate",
    "hardness_report",
    "realism_report",
]
