"""Domain registry.

A domain is a kind of graph GraphFaker knows how to make: the social graph,
a bank with fraud, later a supply chain or a claims network. Every domain has
the same shape, so the CLI, the docs and third-party packages can treat them
alike:

* ``name``: what you type on the command line.
* ``options``: a pydantic model listing the knobs and their defaults.
* ``generate(seed, workers, **options)``: returns a :class:`GraphRun`.
* ``schema`` (optional): for domains that are just a :class:`GraphSchema`
  preset, the function that builds it.

Built-in domains register themselves when ``graphfaker.domains`` is
imported. Other packages register through the ``graphfaker.domains`` entry
point group; each entry point must resolve to a :class:`Domain` instance.
See ``docs/adding-a-domain.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel

from graphfaker.engine.run import GraphRun
from graphfaker.logger import logger
from graphfaker.schema.graph import GraphSchema

ENTRY_POINT_GROUP = "graphfaker.domains"


@dataclass(frozen=True)
class Domain:
    name: str
    summary: str
    generate: Callable[..., GraphRun]
    options: type[BaseModel] | None = None
    schema: Callable[..., GraphSchema] | None = None

    def run(self, seed: int | None = None, workers: int = 1, **options: Any) -> GraphRun:
        """Validate ``options`` against the options model, then generate."""
        if self.options is not None:
            options = self.options(**options).model_dump()
        return self.generate(seed=seed, workers=workers, **options)

    def describe_options(self) -> list[tuple[str, str, Any]]:
        """``(name, type, default)`` for each option, for ``--help`` and docs."""
        if self.options is None:
            return []
        rows = []
        for name, field in self.options.model_fields.items():
            rows.append((name, _describe_type(field.annotation), field.default))
        return rows


def _describe_type(annotation: Any) -> str:
    """``Literal["a", "b"]`` -> ``a | b``; ``int | None`` -> ``int | None``; else the name."""
    if get_origin(annotation) is Literal:
        return " | ".join(str(v) for v in get_args(annotation))
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return str(annotation).replace("typing.", "")


_BUILTIN: dict[str, Domain] = {}


def register(domain: Domain) -> Domain:
    if domain.name in _BUILTIN and _BUILTIN[domain.name] is not domain:
        raise ValueError(f"a domain named {domain.name!r} is already registered")
    _BUILTIN[domain.name] = domain
    return domain


def _plugins() -> dict[str, Domain]:
    found: dict[str, Domain] = {}
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        try:
            domain = entry.load()
        except Exception as exc:
            logger.warning("domain plugin %s failed to load: %s", entry.name, exc)
            continue
        if not isinstance(domain, Domain):
            logger.warning("domain plugin %s is not a Domain instance; skipped", entry.name)
            continue
        found[domain.name] = domain
    return found


def available() -> dict[str, Domain]:
    """Built-in domains plus any installed plugins, by name."""
    return {**_plugins(), **_BUILTIN}


def get(name: str) -> Domain:
    domains = available()
    if name not in domains:
        raise KeyError(f"unknown domain {name!r}; available: {', '.join(sorted(domains))}")
    return domains[name]
