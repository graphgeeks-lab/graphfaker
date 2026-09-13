"""Attribute samplers.

A sampler describes how one attribute of a node or edge is drawn. Samplers are
declared, not executed, here: the engine (`graphfaker.engine.sampling`) turns a
sampler into a column. Keeping the two apart is what lets a schema round-trip
through YAML and be validated before anything is generated.

The vocabulary deliberately mirrors NVIDIA Data Designer's sampler types so a
user who knows one can read the other, and so an adapter between them stays a
renaming rather than a translation.

Parameters that can vary by latent group are typed ``float | str``; a string
must start with ``@`` and name a latent-factor parameter, e.g.
``"@community.mean_age"``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

REF_PREFIX = "@"


def is_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(REF_PREFIX)


def parse_ref(value: str) -> tuple[str, str]:
    """Split ``"@factor.param"`` into ``("factor", "param")``."""
    body = value[len(REF_PREFIX):]
    if "." not in body:
        raise ValueError(
            f"reference {value!r} must look like '@factor.param'"
        )
    factor, param = body.split(".", 1)
    return factor, param


Ref = Annotated[str, Field(pattern=r"^@[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")]


class _Sampler(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConstantSampler(_Sampler):
    kind: Literal["constant"] = "constant"
    value: Any


class CategorySampler(_Sampler):
    """Draw from a fixed set, optionally weighted."""

    kind: Literal["category"] = "category"
    values: list[Any] = Field(min_length=1)
    weights: list[float] | None = None

    @model_validator(mode="after")
    def _weights_match(self) -> CategorySampler:
        if self.weights is not None and len(self.weights) != len(self.values):
            raise ValueError("weights must have one entry per value")
        return self


class SubcategorySampler(_Sampler):
    """Draw from a set chosen by the value of an earlier attribute.

    ``values`` maps each parent value to its own options, so an organization's
    industry follows its subtype instead of being drawn from one flat list.
    """

    kind: Literal["subcategory"] = "subcategory"
    parent: str
    values: dict[Any, list[Any]] = Field(min_length=1)


class UniformSampler(_Sampler):
    kind: Literal["uniform"] = "uniform"
    low: float | Ref
    high: float | Ref
    integer: bool = False
    decimals: int | None = None


class GaussianSampler(_Sampler):
    kind: Literal["gaussian"] = "gaussian"
    mean: float | Ref
    sd: float | Ref
    low: float | None = None
    high: float | None = None
    integer: bool = False
    decimals: int | None = None


class LognormalSampler(_Sampler):
    """Heavy-tailed positive values: prominence, wealth, popularity."""

    kind: Literal["lognormal"] = "lognormal"
    mu: float | Ref = 0.0
    sigma: float | Ref = 1.0
    decimals: int | None = None


class PoissonSampler(_Sampler):
    kind: Literal["poisson"] = "poisson"
    lam: float | Ref


class BernoulliSampler(_Sampler):
    kind: Literal["bernoulli"] = "bernoulli"
    p: float | Ref


class FakerSampler(_Sampler):
    """Call a Faker provider, e.g. ``name``, ``city``, ``company``.

    ``join`` concatenates a list-valued provider (``words``) into a string, and
    ``transform`` applies a string method afterwards. Both exist because an
    attribute is a column, and a column of lists is a poor citizen in every
    downstream format.
    """

    kind: Literal["faker"] = "faker"
    provider: str
    kwargs: dict[str, Any] = Field(default_factory=dict)
    join: str | None = None
    transform: Literal["capitalize", "title", "upper", "lower"] | None = None
    as_type: Literal["str", "float", "int"] | None = None


class ExpressionSampler(_Sampler):
    """Derive a value from attributes sampled earlier on the same row.

    The expression is evaluated with the row's attributes, its latent-group
    parameters (``community.mean_age``), ``math``, and a seeded ``rand``
    (``random.Random``) in scope. It is Python, not a template language, because
    the people writing schemas are writing Python anyway.
    """

    kind: Literal["expression"] = "expression"
    expr: str
    decimals: int | None = None

    @field_validator("expr")
    @classmethod
    def _compiles(cls, expr: str) -> str:
        compile(expr, "<expression>", "eval")
        return expr


class ReferenceSampler(_Sampler):
    """Copy a latent-group parameter onto the node, e.g. the group's region."""

    kind: Literal["reference"] = "reference"
    ref: Ref


class MixtureComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    weight: float = Field(gt=0)
    sampler: Sampler


class MixtureSampler(_Sampler):
    """With probability proportional to weight, use one of several samplers.

    The typical use is "mostly the group's value, sometimes anything": it is
    how an attribute ends up correlated with community without being
    determined by it.
    """

    kind: Literal["mixture"] = "mixture"
    components: list[MixtureComponent] = Field(min_length=1)


class ForeignKeySampler(_Sampler):
    """Pick the id of a node of another type.

    ``same_group`` prefers nodes sharing the row's latent group (a person's home
    is in their community's places); when the group has none, any node of the
    type is used, and when there are none at all the value is ``""`` — never
    ``None``, which GraphML cannot serialise.
    """

    kind: Literal["foreign_key"] = "foreign_key"
    node_type: str
    same_group: str | None = None


Sampler = Annotated[
    ConstantSampler | CategorySampler | SubcategorySampler | UniformSampler | GaussianSampler | LognormalSampler | PoissonSampler | BernoulliSampler | FakerSampler | ExpressionSampler | ReferenceSampler | MixtureSampler | ForeignKeySampler,
    Field(discriminator="kind"),
]

MixtureComponent.model_rebuild()
MixtureSampler.model_rebuild()


def sampler_refs(sampler: Any) -> set[tuple[str, str]]:
    """Every ``(factor, param)`` reference a sampler tree depends on."""
    found: set[tuple[str, str]] = set()
    if isinstance(sampler, MixtureSampler):
        for component in sampler.components:
            found |= sampler_refs(component.sampler)
        return found
    if isinstance(sampler, ReferenceSampler):
        found.add(parse_ref(sampler.ref))
        return found
    for name in type(sampler).model_fields:
        value = getattr(sampler, name)
        if is_ref(value):
            found.add(parse_ref(value))
    return found
