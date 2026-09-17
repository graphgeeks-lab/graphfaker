# Adding a domain

A domain is a kind of graph GraphFaker can generate by name: `social`, `fraud`, and whatever you add. Every domain has the same shape, so the CLI, the docs and other packages treat them alike:

```python
from graphfaker.domains import Domain

Domain(
    name="supply_chain",                 # what you type on the command line
    summary="Suppliers, plants, warehouses and shipments.",
    options=SupplyChainOptions,          # a pydantic model: the knobs and their defaults
    generate=generate,                   # generate(seed=None, workers=1, **options) -> GraphRun
    schema=schema,                       # optional: only if the domain is a GraphSchema preset
)
```

There are two kinds of domain. Pick the simplest one that fits. And before adding one at all, check whether a schema file is enough: `graphfaker schema social --out mine.yaml`, edit the node types, samplers, latent factors and relationships, then `graphfaker generate --schema mine.yaml`. A domain is worth registering when the graph needs options with defaults, a name people can type, or a process that a schema cannot express.

## Kind 1: a schema preset

If your graph can be described as node types with samplers, latent factors, edge families and one of the built-in topology models, the domain is just a function that builds a `GraphSchema`. The generic engine does the rest: sharding, seeding, edges, derived attributes, tables, manifest.

`graphfaker/domains/social.py` is the reference. The essential parts:

```python
from pydantic import BaseModel
from graphfaker.schema import GraphSchema, NodeType, EdgeType, Relationship, LatentFactor, SocialTopology, ...
from graphfaker.engine import generate as run_engine


class Options(BaseModel):
    total_nodes: int = 100
    total_edges: int = 1000


def schema(total_nodes: int = 100, total_edges: int = 1000) -> GraphSchema:
    return GraphSchema(
        name="my_domain",
        latent=[LatentFactor(name="group", groups=5, params={...})],
        nodes=[NodeType(name="Thing", count=total_nodes, attributes={...})],
        edges=[EdgeType(source="Thing", target="Thing", share=1.0, relationships=[Relationship(name="LINKS")])],
        total_edges=total_edges,
        topology=SocialTopology(group="group"),
    )


def generate(seed=None, workers=1, **options):
    opts = Options(**options)
    return run_engine(schema(**opts.model_dump()), seed=seed, workers=workers)
```

Guidance for a good schema:

- Put shared causes in latent factors. If two attributes should correlate, or an attribute should correlate with who connects to whom, make them both depend on a group parameter.
- Prefer heavy-tailed samplers (`lognormal`) for anything that is a size, a popularity or an amount.
- Use `functional=True` for relationships a node can only have once, `target_from` when the target was already chosen as a foreign key, and `DegreeDerived` for attributes that should agree with structure.
- Write the schema to YAML once and read it: if it does not round-trip, something is not declarative.

## Kind 2: a process domain

If your graph has events over time, patterns to inject, or structure the built-in topology models cannot express, write a process domain. `graphfaker/domains/fraud/` is the reference. Its layout is the recommended one:

```
graphfaker/domains/<name>/
    __init__.py       exports generate and the options model
    config.py         the options model (pydantic), derived sizes, presets
    entities.py       node schemas (drawn by the generic engine) and structural edges
    process.py        the legitimate event process, vectorised with numpy
    typologies.py     injected, labelled patterns (if the domain has any)
    generate.py       assembly: run the steps, build truth tables and the manifest, return a GraphRun
    hardness.py       how visible the injected patterns are (if the domain has any)
    evaluate.py       scoring a detector against the truth (if the domain has any)
```

The contract for `generate.py`:

1. Root all randomness in one `Streams.root(seed)` and spawn children in a fixed order, one per stage. Never use the global `random` or `numpy.random`.
2. Draw entities with the generic engine (`entities.build_nodes` calls `sample_latent` and `sample_nodes`), so they are sharded, parallel and reproducible for free.
3. Produce edge tables as Polars frames with `source` and `target` first. Put no labels on them.
4. Put everything the generator knows into `run.truth`: injected patterns, memberships, labels, latent parameters.
5. Put the options model into `manifest.extra` under the domain's name, so a run can be reproduced from the manifest alone.
6. Return a `GraphRun`. Then `run.write`, the sinks and the CLI work unchanged.

If your domain injects patterns, also provide a way to measure them. The fraud pack's `hardness_report` (single-feature AUCs against the truth) and `evaluate` (precision and recall at entity, event and pattern level) are written for that domain, but the approach transfers: list the naive rules someone would try first, and report how well each one does.

## Registering

Built-in domains are registered in `graphfaker/domains/__init__.py`:

```python
register(Domain(name="fraud", summary="...", generate=fraud.generate, options=fraud.FraudConfig))
```

A separate package registers through the `graphfaker.domains` entry point group. In its `pyproject.toml`:

```toml
[project.entry-points."graphfaker.domains"]
supply_chain = "graphfaker_supply_chain:DOMAIN"
```

where `DOMAIN` is a `Domain` instance. After `pip install`, the domain appears in `graphfaker domains` and runs with `graphfaker generate supply_chain --option value`. A plugin that fails to import is skipped with a warning; it never breaks the CLI.

## What the CLI does for you

```
graphfaker domains                                  list domains and their options
graphfaker generate <name> --key value ...          options are validated by the domain's model
    --out DIR --seed N --workers N --sink parquet|neo4j|neo4j-admin|ladybug|duckdb|pyg|gen-fraud-graph
graphfaker schema <name> --key value ... --out FILE  a schema-preset domain's schema as YAML, options applied
graphfaker generate --schema FILE                   generate from a schema file, no domain needed
```

A domain that registers `schema=` gets `graphfaker schema <name>` for free, and users can fork its schema on disk instead of in code. A process domain can register a schema too (the fraud pack registers its entity schema) with a `schema_note` saying what the file does not cover.

Option names on the command line use dashes (`--total-nodes`) and map to the model's fields (`total_nodes`). Values are strings and the model converts them, so a typo in a name or a value produces a message naming the option.

## Checklist before opening a pull request

- A test generates the domain twice with the same seed and checks the outputs are identical (`graphfaker.engine.fingerprint`).
- A test checks each realism property you care about (degree Gini above some floor, a share within a range). Put the numbers you expect in the test and in the docs.
- If patterns are injected: a test that an oracle detector scores perfectly and that the truth tables are consistent with the edge tables.
- A page under `docs/` that explains the generation the way [fraud-generation.md](fraud-generation.md) does: entities, process, patterns, what each option changes, what the truth contains, what a naive detector sees.
