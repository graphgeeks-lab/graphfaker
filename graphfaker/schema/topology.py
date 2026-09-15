"""Topology models: how edges are formed.

A topology model is the graph-specific half of a schema. Attribute samplers say
what a node looks like; the topology model says who it connects to, and it is
allowed to look at attributes and latent groups to decide. That coupling is
deliberate: it is what makes "like attaches to like" possible.

Phase 0 ships the two models the social generator already had. Vectorised
models for very large graphs (Chung-Lu, stochastic block, R-MAT, bipartite
degree sequences) slot in here as further members of the union.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Topology(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UniformTopology(_Topology):
    """Both endpoints drawn uniformly: an Erdős–Rényi graph.

    Exists for comparison. It has no hubs, no clustering, no communities, and
    hands people four birthplaces; keep it only to show what the realistic
    model fixes.
    """

    kind: Literal["uniform"] = "uniform"


class NumericAffinity(BaseModel):
    """``weight * max(0, 1 - |a - b| / scale)`` for a numeric attribute."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    weight: float = 1.0
    scale: float = Field(gt=0)


class SocialTopology(_Topology):
    """Preferential attachment + triadic closure + homophily.

    Edge formation samples a handful of candidates by degree preference, mostly
    from the source's own latent group, and keeps the most plausible one. The
    plausibility score is the homophily term: shared group, prominent targets,
    and attribute similarity as configured below. Same-type edge families
    marked ``closure`` close triangles instead, which is the only source of
    clustering.

    Every constant that used to be a module-level magic number in ``core.py``
    is a field here, so a schema can tune it and a manifest records it.
    """

    kind: Literal["social"] = "social"

    #: Latent factor used for community-local candidate pools and the group
    #: affinity bonus.
    group: str = "community"
    #: Node attribute treated as popularity; prominent targets attract edges
    #: regardless of similarity.
    prominence: str | None = "prominence"

    group_affinity: float = 1.0
    prominence_affinity: float = 0.45
    numeric_affinity: dict[str, NumericAffinity] = Field(default_factory=dict)
    categorical_affinity: dict[str, float] = Field(default_factory=dict)

    #: Fraction of closure-eligible edges formed by closing a triangle.
    triadic_closure_rate: float = Field(default=0.55, ge=0.0, le=1.0)
    #: Probability an attachment ignores degree and picks uniformly, which keeps
    #: the tail bounded and low-degree nodes reachable.
    uniform_attachment_rate: float = Field(default=0.20, ge=0.0, le=1.0)
    #: Candidates scored per edge. Sampling keeps formation linear in edges.
    affinity_sample_size: int = Field(default=8, ge=1)
    #: Chance a candidate is drawn from the source's own group.
    same_group_rate: float = Field(default=0.75, ge=0.0, le=1.0)


TopologyModel = Annotated[
    UniformTopology | SocialTopology,
    Field(discriminator="kind"),
]
