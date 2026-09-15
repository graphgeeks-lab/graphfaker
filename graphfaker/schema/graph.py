"""The graph schema: what a synthetic graph should look like.

A ``GraphSchema`` is the unit of declaration. It names the node types and
their attribute samplers, the latent factors shared across them, the edge
families and the relationships inside each, and the topology model that
decides who connects to whom. It is data: it round-trips through YAML and
JSON, validates before anything runs, and hashes into the run manifest so a
dataset can always be traced back to the declaration that produced it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from graphfaker.schema.samplers import (
    ExpressionSampler,
    ForeignKeySampler,
    Sampler,
    sampler_refs,
)
from graphfaker.schema.topology import SocialTopology, TopologyModel

_IDENT = r"^[A-Za-z_][A-Za-z0-9_]*$"


def _looks_like_path(text: str) -> bool:
    """A short single-line string naming an existing file. Guarded because
    ``Path(long_yaml_text).exists()`` raises on Linux (name too long)."""
    if len(text) > 4096:
        return False
    try:
        return Path(text).is_file()
    except OSError:
        return False


class LatentFactor(BaseModel):
    """A hidden grouping shared by every node type, with per-group parameters.

    The group id is written onto each node under ``name``. Parameters are
    sampled once per group and are what attribute samplers reference with
    ``@name.param``. A community with its own mean age and favourite
    education level is what makes attributes and edges agree.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=_IDENT)
    #: Number of groups. ``None`` lets the engine pick from the node count.
    groups: int | None = Field(default=None, ge=1)
    params: dict[str, Sampler] = Field(default_factory=dict)


class DegreeDerived(BaseModel):
    """An attribute computed from structure after edges exist.

    ``expr`` sees ``count`` (the matching degree) and the node's attributes.
    An organization's headcount that disagrees with its WORKS_AT edges is a
    trap for anyone testing aggregation logic, so scale attributes are
    reconciled from the graph rather than sampled independently.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["degree"] = "degree"
    relationship: str
    direction: Literal["in", "out"] = "in"
    expr: str = "count"

    @field_validator("expr")
    @classmethod
    def _compiles(cls, expr: str) -> str:
        compile(expr, "<derived>", "eval")
        return expr


class NodeType(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=_IDENT)
    count: int = Field(ge=0)
    #: Ids are ``f"{id_prefix}_{index}"``. Defaults to the lower-cased name.
    id_prefix: str | None = None
    #: Ordered: an expression may reference attributes declared before it.
    attributes: dict[str, Sampler] = Field(default_factory=dict)
    derived: dict[str, DegreeDerived] = Field(default_factory=dict)

    @property
    def prefix(self) -> str:
        return self.id_prefix or self.name.lower()

    @model_validator(mode="after")
    def _no_reserved_names(self) -> NodeType:
        for reserved in ("id", "type"):
            if reserved in self.attributes or reserved in self.derived:
                raise ValueError(f"{reserved!r} is a reserved attribute name")
        return self


class Relationship(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    #: A node has at most one of these (LIVES_IN, BORN_IN). Nobody was born in
    #: four cities.
    functional: bool = False
    #: Add the reverse edge as well (FRIENDS_WITH).
    bidirectional: bool = False
    #: Take the target from this attribute of the source node instead of
    #: choosing one: a foreign key already sampled on the node.
    target_from: str | None = None
    attributes: dict[str, Sampler] = Field(default_factory=dict)


class EdgeType(BaseModel):
    """A family of relationships between two node types with a share of the
    edge budget."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    target: str
    relationships: list[Relationship] = Field(min_length=1)
    #: Fraction of ``GraphSchema.total_edges`` spent on this family.
    share: float = Field(gt=0.0, le=1.0)
    #: Form some of these edges by closing triangles. Only meaningful when
    #: ``source == target``; it is the only source of clustering.
    closure: bool = False

    @model_validator(mode="after")
    def _closure_needs_same_type(self) -> EdgeType:
        if self.closure and self.source != self.target:
            raise ValueError("closure requires source and target to be the same type")
        return self

    def relationship(self, name: str) -> Relationship:
        for rel in self.relationships:
            if rel.name == name:
                return rel
        raise KeyError(name)


class RealismTargets(BaseModel):
    """Structural properties the generated graph is expected to have.

    Purely declarative in Phase 0; ``graphfaker.metrics`` reports the realised
    values next to these so drift is visible.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    degree_gini_min: float | None = None
    average_clustering_min: float | None = None
    community_modularity_min: float | None = None
    assortativity: dict[str, float] = Field(default_factory=dict)


class GraphSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=_IDENT)
    version: str = "1"
    latent: list[LatentFactor] = Field(default_factory=list)
    nodes: list[NodeType] = Field(min_length=1)
    edges: list[EdgeType] = Field(default_factory=list)
    total_edges: int = Field(default=0, ge=0)
    topology: TopologyModel = Field(default_factory=SocialTopology)
    targets: RealismTargets | None = None

    # ------------------------------------------------------------------ lookups

    def node_type(self, name: str) -> NodeType:
        for node in self.nodes:
            if node.name == name:
                return node
        raise KeyError(name)

    def latent_factor(self, name: str) -> LatentFactor:
        for factor in self.latent:
            if factor.name == name:
                return factor
        raise KeyError(name)

    @property
    def total_nodes(self) -> int:
        return sum(node.count for node in self.nodes)

    def relationships(self) -> list[tuple[EdgeType, Relationship]]:
        return [(edge, rel) for edge in self.edges for rel in edge.relationships]

    # --------------------------------------------------------------- validation

    @model_validator(mode="after")
    def _consistent(self) -> GraphSchema:
        node_names = [node.name for node in self.nodes]
        if len(set(node_names)) != len(node_names):
            raise ValueError("node type names must be unique")
        latent_names = {factor.name for factor in self.latent}
        if len(latent_names) != len(self.latent):
            raise ValueError("latent factor names must be unique")
        for factor in self.latent:
            if factor.name in {"id", "type"}:
                raise ValueError(f"latent factor cannot be named {factor.name!r}")
        known_params = {
            (factor.name, param) for factor in self.latent for param in factor.params
        }

        for node in self.nodes:
            for attr, sampler in node.attributes.items():
                for ref in sampler_refs(sampler):
                    if ref not in known_params:
                        raise ValueError(
                            f"{node.name}.{attr} references unknown latent "
                            f"parameter @{ref[0]}.{ref[1]}"
                        )
                if isinstance(sampler, ForeignKeySampler):
                    if sampler.node_type not in node_names:
                        raise ValueError(
                            f"{node.name}.{attr} points at unknown node type "
                            f"{sampler.node_type!r}"
                        )
                    if sampler.same_group and sampler.same_group not in latent_names:
                        raise ValueError(
                            f"{node.name}.{attr}: same_group {sampler.same_group!r} "
                            "is not a latent factor"
                        )
            rel_names = {rel.name for _, rel in self.relationships()}
            for attr, derived in node.derived.items():
                if derived.relationship not in rel_names:
                    raise ValueError(
                        f"{node.name}.{attr} derives from unknown relationship "
                        f"{derived.relationship!r}"
                    )

        for edge in self.edges:
            for endpoint in (edge.source, edge.target):
                if endpoint not in node_names:
                    raise ValueError(f"edge references unknown node type {endpoint!r}")
            source = self.node_type(edge.source)
            for rel in edge.relationships:
                if rel.target_from and rel.target_from not in source.attributes:
                    raise ValueError(
                        f"{rel.name}.target_from={rel.target_from!r} is not an "
                        f"attribute of {edge.source}"
                    )
                for attr, sampler in rel.attributes.items():
                    if isinstance(sampler, (ForeignKeySampler, ExpressionSampler)):
                        raise TypeError(
                            f"{rel.name}.{attr}: {sampler.kind} samplers are not "
                            "supported on edge attributes yet"
                        )
        if self.edges:
            total_share = sum(edge.share for edge in self.edges)
            if not 0.99 <= total_share <= 1.01:
                raise ValueError(f"edge shares must sum to 1, got {total_share:.3f}")

        if (
            isinstance(self.topology, SocialTopology)
            and self.topology.group not in latent_names
        ):
            raise ValueError(
                f"topology group {self.topology.group!r} is not a latent factor"
            )
        return self

    def generation_order(self) -> list[NodeType]:
        """Node types ordered so every foreign key target is generated first."""
        deps: dict[str, set[str]] = {}
        for node in self.nodes:
            deps[node.name] = {
                sampler.node_type
                for sampler in node.attributes.values()
                if isinstance(sampler, ForeignKeySampler)
            } - {node.name}
        ordered: list[str] = []
        remaining = dict(deps)
        while remaining:
            ready = sorted(name for name, needs in remaining.items() if needs <= set(ordered))
            if not ready:
                raise ValueError(
                    "foreign keys form a cycle between " + ", ".join(sorted(remaining))
                )
            ordered.extend(ready)
            for name in ready:
                del remaining[name]
        return [self.node_type(name) for name in ordered]

    # ------------------------------------------------------------ serialisation

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    def to_yaml(self, path: str | Path | None = None) -> str:
        text = yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True)
        if path is not None:
            Path(path).write_text(text, encoding="utf-8")
        return text

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphSchema:
        return cls.model_validate(data)

    @classmethod
    def from_yaml(cls, source: str | Path) -> GraphSchema:
        """Load from a file path, or parse YAML text directly."""
        text = str(source)
        if isinstance(source, Path) or ("\n" not in text and _looks_like_path(text)):
            text = Path(source).read_text(encoding="utf-8")
        return cls.from_dict(yaml.safe_load(text))

    def digest(self) -> str:
        """Stable content hash, recorded in the run manifest."""
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
