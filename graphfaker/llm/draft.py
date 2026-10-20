"""A sentence in, a schema file out.

The first thing the semantic layer does, and the smallest useful one. You
describe a graph in a line of English, a model writes the YAML, pydantic
checks it, and you get a file. The file is the deliverable: you read it,
change the counts, fix the thing the model misunderstood, commit it, and
from then on it is an ordinary schema that generates the same bytes every
time. Nothing downstream knows a model was involved.

Three things make that honest rather than a demo.

**The contract is generated, not written.** The system prompt's list of
sampler kinds and their fields comes out of the pydantic models at call
time, so it cannot drift from what the engine actually accepts the way a
hand-maintained prompt does. The worked example is the social pack's own
schema, for the same reason.

**The output is validated before you see it.** A draft that does not parse
is an error. The pydantic errors go back to the model once, because the
failures are mechanical almost every time: a sampler kind that does not
exist, an edge pointing at a node type that got renamed between two parts
of the answer, a latent parameter referenced but never declared. One repair
round fixes most of them; after that it stops and tells you what was wrong,
which is worth more than a third attempt.

**What comes back is a schema and not data.** The model never sees a row
and never writes one. See :mod:`graphfaker.llm` for why that line is where
it is.
"""

from __future__ import annotations

import re
import typing
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from graphfaker.schema.graph import GraphSchema
from graphfaker.schema.samplers import Sampler

#: Fenced blocks, so a reply that explains itself before the YAML still works.
_FENCE = re.compile(r"```(?:ya?ml)?\s*\n(.*?)```", re.DOTALL)

SYSTEM = """You turn a description of a graph dataset into a GraphFaker schema in YAML.

Answer with the YAML and nothing else. No preamble, no explanation, no code fence.

The schema is the whole specification of a dataset: node types with counts and
attribute samplers, edge types with a share of the edge budget, and optional
latent factors that make attributes and edges agree with each other. Rules that
matter:

* Every node type needs a `name` and a `count`. Attribute names cannot be `id`
  or `type`.
* Every edge type needs `source`, `target`, a list of `relationships` and a
  `share`. The shares across all edge types should sum to about 1.0.
* `total_edges` is the whole edge budget, divided by the shares.
* A latent factor is a hidden grouping shared by every node type: a region, a
  community, a market segment. Its `params` are sampled once per group, and an
  attribute references one with `@factor_name.param_name`. This is what stops a
  dataset being a pile of independent columns, so use one whenever the
  description implies that some entities belong together.
* `functional: true` on a relationship means a node has at most one (BORN_IN).
  `bidirectional: true` adds the reverse edge (FRIENDS_WITH). `closure: true` on
  an edge type whose source and target are the same type forms some edges by
  closing triangles, which is the only source of clustering.
* Prefer a `faker` sampler for anything Faker has a provider for (names,
  addresses, emails, dates), a `category` sampler for a fixed set with weights,
  and `gaussian`/`lognormal`/`poisson` for numbers. Money and sizes are
  lognormal far more often than they are gaussian.
* An `expression` sampler is evaluated per row with a small scope and nothing
  else: the attributes declared *before* it on the same node, the latent
  factors as `factor.param`, `math`, `rand` (a seeded `random.Random`),
  `timedelta`, and the builtins abs, bool, float, int, len, max, min, round,
  str, sum. There are no imports and no other modules. A date some days after
  another one is `start_date + timedelta(days=rand.randint(1, 20))`; there is
  deliberately no way to read the clock, because a schema that did would
  generate a different dataset every run.
* Never ask a date provider for the clock. `end_date: now`, `start_date: -2y`
  and providers like `past_date` or `date_this_year` are relative to the day
  the generator runs, so the same seed produces different data tomorrow. Pin
  them: `date_time_between` with literal `start_date: '2025-01-01'` and
  `end_date: '2026-01-01'`.
* The default topology is `social`, and it reads a latent factor called
  `community`. So either name your latent factor `community`, or set
  `topology: {kind: social, group: your_factor_name}`. Use
  `topology: {kind: uniform}` only when the entities genuinely have no
  grouping at all; it produces a graph with no hubs and no clustering.

Choose counts that match the description's scale if it gives one, and something
small and round (a few thousand nodes) if it does not."""

REPAIR = """That schema did not validate. The errors are below, from pydantic.

Fix every one of them and answer with the corrected YAML and nothing else.
Change as little as possible: keep the node types, the attributes and the
structure you already chose unless an error is about them."""


@dataclass
class DraftResult:
    """What the drafter produced, and what it took."""

    schema: GraphSchema
    yaml: str
    #: One entry per failed attempt, in order, as pydantic reported it.
    errors: list[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""

    @property
    def repaired(self) -> bool:
        return bool(self.errors)

    def summary(self) -> str:
        nodes = ", ".join(f"{n.name}={n.count:,}" for n in self.schema.nodes)
        relationships = sum(len(e.relationships) for e in self.schema.edges)
        lines = [
            f"drafted {self.schema.name!r} with {self.provider} ({self.model})"
            + (" after one repair round" if self.repaired else ""),
            f"  nodes  {self.schema.total_nodes:,}  {nodes}",
            f"  edges  {self.schema.total_edges:,}  {relationships} relationship types"
            f" across {len(self.schema.edges)} families",
        ]
        if self.schema.latent:
            lines.append(
                "  latent " + ", ".join(f"{f.name}({len(f.params)} params)" for f in self.schema.latent)
            )
        lines.append("  read it before you generate from it: a drafted schema is a first guess, not an answer")
        return "\n".join(lines)


class DraftError(RuntimeError):
    """The model's answer did not validate, twice.

    Carries the attempts so the last YAML can be looked at, which is usually
    enough to see what the model was reaching for.
    """

    def __init__(self, message: str, attempts: list[str], errors: list[str]) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.errors = errors


def sampler_contract() -> str:
    """Every sampler kind and its fields, read off the models.

    Generated rather than written down, so the prompt cannot describe a
    sampler the engine does not have or miss one it gained.
    """
    union = typing.get_args(typing.get_args(Sampler)[0])
    lines = []
    for model in union:
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            continue
        kind = model.model_fields["kind"].default
        fields = [
            f"{name}{'' if info.is_required() else '?'}"
            for name, info in model.model_fields.items()
            if name != "kind"
        ]
        summary = (model.__doc__ or "").strip().split("\n")[0]
        lines.append(f"- {kind}: {', '.join(fields) or 'no fields'}" + (f"  # {summary}" if summary else ""))
    return "\n".join(lines)


def worked_example() -> str:
    """A real schema, from the pack rather than from this file."""
    from graphfaker.domains import get

    return get("social").schema(total_nodes=400, total_edges=2000).to_yaml()


def build_prompt(description: str, name: str | None = None) -> tuple[str, str]:
    """The system and user prompts for one drafting attempt."""
    system = "\n\n".join([
        SYSTEM,
        "The sampler kinds, with their fields (`?` means optional):\n" + sampler_contract(),
        "A complete, valid schema, for shape rather than for content:\n" + worked_example(),
    ])
    user = description if name is None else f"{description}\n\nCall the schema {name!r}."
    return system, user


def extract_yaml(reply: str) -> str:
    """The YAML out of a reply, fenced or not.

    Models are told to answer with YAML alone and mostly do; the ones that
    explain themselves first put the schema in a fence, and taking the
    fence is cheaper than arguing with the prompt.
    """
    fenced = _FENCE.search(reply)
    return (fenced.group(1) if fenced else reply).strip()


def draft(
    description: str,
    provider,
    *,
    name: str | None = None,
    repair: bool = True,
    max_tokens: int = 8192,
) -> DraftResult:
    """Draft a schema from ``description``, validating what comes back.

    One call, then one repair call with the pydantic errors if the first
    did not validate. ``repair=False`` makes a failure final, which is what
    a test of the unhappy path wants.
    """
    system, user = build_prompt(description, name)
    attempts: list[str] = []
    errors: list[str] = []

    reply = provider.complete(system, user, max_tokens=max_tokens)
    for round_number in range(2 if repair else 1):
        text = extract_yaml(reply)
        attempts.append(text)
        try:
            schema = GraphSchema.from_yaml(text)
        except (ValidationError, ValueError, TypeError, KeyError, SyntaxError) as exc:
            # Anything the schema throws is a thing to repair, not a crash.
            # Most of it arrives as a ValidationError; a malformed YAML
            # document and the odd check that raises straight through do
            # not, and a traceback helps nobody.
            errors.append(_explain(exc))
        else:
            return DraftResult(
                schema=schema,
                yaml=text,
                errors=errors,
                provider=getattr(provider, "name", "provider"),
                model=getattr(provider, "model", "?"),
            )
        if round_number == 0 and repair:
            reply = provider.complete(
                system,
                f"{user}\n\nYour previous answer:\n{text}\n\n{REPAIR}\n\n{errors[-1]}",
                max_tokens=max_tokens,
            )

    raise DraftError(
        f"the drafted schema did not validate after {len(attempts)} attempt(s): {errors[-1]}",
        attempts,
        errors,
    )


def _explain(exc: Exception) -> str:
    """Pydantic's errors as the model should see them: location, then message."""
    if not isinstance(exc, ValidationError):
        return str(exc)
    return "\n".join(
        f"- {'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors()
    )
