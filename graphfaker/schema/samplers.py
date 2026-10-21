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

import re
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
        try:
            compile(expr, "<expression>", "eval")
        except SyntaxError as exc:
            # ValueError, so pydantic wraps it the way it wraps every
            # other check here. A SyntaxError walks straight out of a
            # field validator, so a schema with a typo in an expression
            # crashed the command that was supposed to report it.
            raise ValueError(f"{expr!r} is not a valid expression: {exc.msg}") from exc
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
    type is used, and when there are none at all the value is ``""``, never
    ``None``, which GraphML cannot serialise.
    """

    kind: Literal["foreign_key"] = "foreign_key"
    node_type: str
    same_group: str | None = None


#: Faker date providers whose range is "counting back from now" with no way
#: to say otherwise. A schema using one generates different data every day.
CLOCK_PROVIDERS = frozenset({
    "date_this_year", "date_this_month", "date_this_decade", "date_this_century",
    "date_time_this_year", "date_time_this_month", "date_time_this_decade",
    "date_time_this_century", "past_date", "past_datetime", "future_date",
    "future_datetime", "time_delta",
})

#: Argument values that mean "ask the clock": the two keywords Faker accepts
#: and its relative offsets (``-365d``, ``+2y``).
_RELATIVE = re.compile(r"^(now|today|[+-]\d+[smhdwy])$", re.IGNORECASE)


def describe_sampler(sampler: Any) -> str:
    """One short phrase for a sampler, for reading a schema back.

    Short enough that a node's whole attribute list fits on a couple of
    lines, because the thing a reader is looking for is a distribution that
    is wrong for what the column means: a gaussian amount has no tail, and
    a lognormal age is nonsense. The parameters are in the phrase for the
    same reason.
    """
    kind = getattr(sampler, "kind", None)
    if kind == "constant":
        return f"constant({_number(sampler.value)})"
    if kind == "category":
        return f"category({len(sampler.values)})"
    if kind == "subcategory":
        return f"subcategory(by {sampler.parent})"
    if kind == "uniform":
        return f"uniform({_number(sampler.low)}, {_number(sampler.high)})"
    if kind == "gaussian":
        return f"gaussian({_number(sampler.mean)}, {_number(sampler.sd)})"
    if kind == "lognormal":
        return f"lognormal({_number(sampler.mu)}, {_number(sampler.sigma)})"
    if kind == "poisson":
        return f"poisson({_number(sampler.lam)})"
    if kind == "bernoulli":
        return f"bernoulli({_number(sampler.p)})"
    if kind == "faker":
        return f"faker({sampler.provider})"
    if kind == "expression":
        return "expression"
    if kind == "reference":
        return f"@{sampler.ref.lstrip('@')}"
    if kind == "mixture":
        return "mixture(" + " + ".join(describe_sampler(c.sampler) for c in sampler.components) + ")"
    if kind == "foreign_key":
        return f"-> {sampler.node_type}"
    return str(kind or type(sampler).__name__)


def _number(value: Any) -> str:
    """A parameter, without the trailing zeros that make a line unreadable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return f"{value:g}"


def clock_dependent(sampler: object) -> str | None:
    """Why this sampler would produce different data tomorrow, or ``None``.

    The engine's own date providers count from a fixed reference date, so a
    seeded dataset is the same on any day. A provider it does not implement
    falls through to Faker, which reads the real clock when it is asked to,
    and ``end_date: now`` is asking. The engine cannot honestly freeze
    somebody else's module, so it says so instead: a schema that wants a
    reproducible dataset pins its dates, and one that genuinely wants the
    clock now knows it has one.
    """
    if not isinstance(sampler, FakerSampler):
        return None
    if sampler.provider in CLOCK_PROVIDERS:
        return f"the {sampler.provider!r} provider is relative to the current date"
    relative = sorted(
        name for name, value in sampler.kwargs.items()
        if isinstance(value, str) and _RELATIVE.match(value.strip())
    )
    if relative:
        shown = ", ".join(f"{name}={sampler.kwargs[name]!r}" for name in relative)
        return f"{shown} is relative to the current date"
    return None


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
