"""Execution: seeding, sampling, edge formation, manifests.

Names are imported on first use: a worker process needs only
``graphfaker.engine.sampling`` and should not pay for the topology model and
networkx behind ``generate``.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

_EXPORTS = {
    "GraphRun": "graphfaker.engine.run",
    "Manifest": "graphfaker.engine.run",
    "fingerprint": "graphfaker.engine.run",
    "generate": "graphfaker.engine.run",
    "Streams": "graphfaker.engine.seeding",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'graphfaker.engine' has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


if TYPE_CHECKING:  # pragma: no cover
    from graphfaker.engine.run import GraphRun as GraphRun
    from graphfaker.engine.run import Manifest as Manifest
    from graphfaker.engine.run import fingerprint as fingerprint
    from graphfaker.engine.run import generate as generate
    from graphfaker.engine.seeding import Streams as Streams
