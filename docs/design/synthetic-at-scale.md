# GraphFaker 1.0: Synthetic Graph Data at Scale

**Status:** design draft · **Branch:** `synthetic-at-scale` · **Date:** 2026-09-13

This document replaces the earlier proposals. It sets the direction for taking GraphFaker from an experimentation library (one hard-coded social schema, NetworkX in memory) to a production synthetic-graph engine: schema-driven, realistic by construction, scalable past what fits in an object graph, and able to land data in any graph store with ground truth attached.

The first deliverable is a **fraud / AML domain pack**, a redesign of what [SantanderAI/gen-fraud-graph](https://github.com/SantanderAI/gen-fraud-graph) does, built on the new engine.

---

## 1. Why this matters now

Three things are converging, and none of them has a good answer for graph data.

**Graphs became AI infrastructure.** GraphRAG, GNN-based fraud detection, agent memory (cognee, graphiti, mem0), knowledge graphs for LLM grounding. Every one of these needs graph-shaped data to build, test, and benchmark against, and relational data (who pays whom, who knows whom, who shares a device with whom) is the most privacy-sensitive data that exists. It cannot be shared; it has to be synthesised.

**Synthetic data is being industrialised, for tables but not for graphs.** NVIDIA's Data Designer (20T+ tokens processed, Gretel acquired), Mostly AI, SDV. They generate rows. Data Designer has samplers, LLM columns, validators, judges, DAG-ordered dependencies, checkpointing and plugins, but no concept of an edge. SDV handles multi-table foreign keys but has no topology model. The gap is exactly "realistic relational structure": degree distributions, clustering, communities, temporal processes, injected labelled patterns.

**Fraud is the graph killer app, and everyone demos it on toy data.** Neo4j, TigerGraph, Neptune, Kùzu/LadybugDB, FalkorDB, Memgraph all lead with fraud-ring demos. Santander open-sourced gen-fraud-graph because banks cannot share the real thing. IBM built AMLSim and AMLworld for the same reason. The existing generators either have scale without realism (gen-fraud-graph: uniform-random edges, one sentinel amount, constant timestamps) or realism without scale or labels.

The unique asset of synthetic data is **ground truth**. You know which accounts are mules, which two nodes are the same person, which community a node belongs to. A generator that keeps that truth attached to every output, and emits it in the format the target system wants, is a benchmark substrate for the entire graph+AI stack. That is what GraphFaker should be.

---

## 2. Where we are

### GraphFaker (`new_feature_branch`, v0.5)

| Asset | State |
|---|---|
| Social/knowledge graph generator | Hard-coded 5 node types, 7 edge families, in `core.py`. **Realistic topology** (preferential attachment, triadic closure, homophily via latent communities, heavy-tailed prominence, functional-relationship constraints). This is good and must survive the redesign. |
| Reproducibility | Per-instance seeding. |
| Entity resolution (`resolve.py`) | Graph-native ER: attribute similarity + neighbour overlap, blocking, cluster merge, precision/recall evaluation against gold. |
| Corpus (`corpus.py`) | Text corpus that mentions graph entities under aliases, with gold, for measuring entity duplication in GraphRAG builders (cognee adapter). |
| Metrics (`metrics.py`) | Degree gini, clustering, modularity, assortativity; `compare_topology` realistic-vs-uniform. |
| Export (`export.py`) | CSV, Neo4j admin-import CSV, Cypher/openCypher/GQL scripts, GraphML. |
| Real-data fetchers | OSM road networks (osmnx), flight networks (BTS), Wikipedia. |
| Scale ceiling | NetworkX object graph. Practically ≤ ~1M edges; edge formation is sequential Python. |

### gen-fraud-graph (Santander, Apache-2.0, v0.1)

| Has | Lacks |
|---|---|
| Scale-factor convention (`1.0` = 10M accounts / 90M tx) | Any realism: `Customer_{uid}` names, uniform balances, one `creation_date`, one `timestamp` for every transaction |
| Sharded `ProcessPoolExecutor` workers, per-shard seeding, resume from partial files | Topology: src/dst drawn uniformly → Erdős–Rényi, no hubs, no merchants, no recurring payments |
| CSV + Neptune bulk-load format, optional embeddings | Only one typology (`cycle`); fraud amount is a sentinel `9999` (jittered at higher hardness) |
| `hardness` presets (amount jitter, ring overlap, decoy cycles), a good idea | Hardness is asserted, not measured |
| `evaluate.py`: account- and ring-level P/R/F1 vs `fraud_cases.csv` | Any notion of customers, devices, geography, time |

**Read together:** gen-fraud-graph is a scale engine with a toy model; GraphFaker is a realism engine with a toy scale. The redesign marries them.

### NVIDIA Data Designer (Apache-2.0)

What to borrow, not what to depend on:

- **Declarative config → compiled execution DAG.** Pydantic discriminated unions (`column_type`, `sampler_type`), YAML/JSON round-trip, topological ordering of dependent fields.
- **Sampler vocabulary:** `uuid, category, subcategory, uniform, gaussian, poisson, binomial, bernoulli, bernoulli_mixture, scipy, datetime, timedelta, person`. Constraints via rejection sampling. Locale-aware person data with managed datasets.
- **LLM columns are one column type among many** (`llm-text`, `llm-structured`, `llm-judge`, `expression`, `validation`, `embedding`), not the centre of the system.
- **Row-group checkpointing, preview-before-run, plugin entry points.**
- **Three-package split** (config / engine / interface). Good pattern; premature for us.

Data Designer generates node tables extremely well. It cannot generate edges. That is the integration: **Data Designer (or our own samplers) for attributes, GraphFaker for structure.** Done as an optional extra, not a hard dependency, because it pulls a large tree.

---

## 3. The reframe

> **GraphFaker is to graphs what Data Designer is to tables.** You declare a graph schema (node types with attribute distributions, edge types with topology models, temporal processes, labelled patterns to inject, noise to apply) and the engine generates it at any scale, on any backend, into any sink, with ground truth attached.

Everything in v0.5 becomes an instance of this: the social graph is a `GraphSchema` preset; `resolve`/`corpus` become the *entity-duplication* noise model plus its evaluation harness; OSM/flights become *seed substrates*.

---

## 4. Realism principles

"Make sense" needs to be a checklist the engine enforces and the metrics report on.

1. **Latent factors drive both attributes and edges.** Already the core insight in `core.py` (communities). Generalise: a node type may declare latent factors; attribute samplers and edge models may condition on them. This is what produces homophily and attribute-topology correlation instead of independence.
2. **Heavy tails everywhere.** Degrees, transaction amounts, merchant popularity, account balances. Log-normal / power-law by default; uniform only when declared.
3. **Correlation is declared and verified.** A schema states expected dependencies (`income ~ f(age, education)`, `amount ~ category`, degree assortativity target). The metrics layer measures the realised values and reports drift. This is the "regression" requirement: fields depend on each other the way they do in the world.
4. **Constraints hold.** Functional relationships (one birthplace, one HQ), referential integrity, temporal causality (account opened before its first transaction; steps of a laundering pattern are time-ordered; a device seen before it is used).
5. **Collisions are first-class.** Two kinds:
- *Entity collisions*: the same real-world entity appears as several nodes (typos, aliases, re-registration). This is the entity-resolution use case; the gold clusters are emitted.
- *Attribute collisions*: distinct entities share a device, address, phone, IBAN, employer. Innocent collisions (households, offices) and guilty ones (mule networks, synthetic identities) are generated by the same mechanism with different rates, so the signal is present but not trivially separable.
6. **Hardness is measured, not asserted.** For any labelled pattern, run a trivial baseline (single-feature threshold, logistic regression on degree/amount features) and report its AUC. `hardness=high` means "no trivial baseline exceeds X". This makes datasets comparable across versions and generators.
7. **Ground truth is never lost.** Every run writes a `truth/` directory (pattern labels, duplicate clusters, community assignments) and a manifest (schema hash, seed, engine version). Same manifest ⇒ identical bytes.

---

## 5. Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│  interface      GraphFaker API · CLI · domain presets (fraud, …)   │
├────────────────────────────────────────────────────────────────────┤
│  schema         GraphSchema · NodeType · EdgeType · Process ·      │
│                 Pattern · Noise · samplers (Pydantic, YAML)        │
├────────────────────────────────────────────────────────────────────┤
│  engine         compile → plan → shards → generate → inject →      │
│                 noise → verify · seeding · checkpoint/resume       │
├──────────────────────────┬─────────────────────────────────────────┤
│  backends (compute)      │  sinks (I/O)                            │
│  columnar (Polars/Arrow) │  Parquet/CSV · Neo4j · LadybugDB/Kùzu · │
│  NetworkX view           │  Neptune · Memgraph/FalkorDB · RDF ·    │
│  igraph · networkit ·    │  PyG/DGL · GraphML · gen-fraud-graph    │
│  cuGraph (optional)      │  compatible                             │
├──────────────────────────┴─────────────────────────────────────────┤
│  truth & metrics   manifest · labels · topology stats · realism    │
│                    drift · hardness baselines · evaluation harness │
└────────────────────────────────────────────────────────────────────┘
```

### 5.1 Schema layer (`graphfaker.schema`)

Pydantic models, serialisable to YAML/JSON, discriminated on `kind` fields.

```python
GraphSchema
  nodes:     [NodeType]      # name, count | count_expr, attributes: {name: Sampler}, latent: [LatentFactor]
  edges:     [EdgeType]      # name, src, dst, count | rate, topology: TopologyModel, attributes, constraints
  processes: [Process]       # temporal event generators (e.g. TransactionProcess: seasonality, recurrence)
  patterns:  [Pattern]       # labelled motifs injected at a rate: FanIn, FanOut, Cycle, GatherScatter, …
  noise:     [Noise]         # Duplicate(rate, corruption), MissingValues, SharedAttribute(collision)
  targets:   RealismTargets  # optional: degree_gini, clustering, modularity, assortativity, hardness
```

**Samplers** mirror Data Designer's vocabulary so users (and an eventual adapter) speak one language: `uuid, category, subcategory, uniform, gaussian, lognormal, poisson, binomial, bernoulli, datetime, timedelta, person, expression, conditional(on=…)`. Person sampling stays on Faker with locale.

**Topology models** are the graph-specific contribution:

| Model | Produces | Use |
|---|---|---|
| `preferential_attachment` | heavy-tailed degree | social, merchant popularity |
| `triadic_closure` | clustering | friendships, co-working |
| `homophily(on=attr/latent)` | community structure, assortativity | any social layer |
| `stochastic_block` | explicit communities | vectorisable at scale |
| `bipartite(src_degree, dst_degree)` | customer→merchant, user→product | transactional layers |
| `chung_lu(degree_seq)` / `rmat` | scale-free at 10⁸+ edges | vectorised bulk generation |
| `geo_gravity(lat, lon)` | distance-decayed links | branches, deliveries, OSM-seeded |
| `substrate(source=osm/flights)` | real network as backbone | geo-realistic synthesis |

The `"realistic"` social topology in `core.py` becomes the composition `preferential_attachment + triadic_closure + homophily`, expressed in the schema instead of hard-coded.

### 5.2 Engine (`graphfaker.engine`)

- **Compile:** validate schema, resolve dependency order (attributes → latent → edges → processes → patterns → noise), compute counts from scale factor.
- **Shard:** node id ranges per worker (gen-fraud-graph's approach), seeded with `numpy.random.SeedSequence(seed).spawn(n)` so shards are independent *and* the whole run is reproducible for any worker count.
- **Generate nodes** embarrassingly parallel per shard, writing Parquet row groups.
- **Generate edges** with the chosen topology model. Sequential models (PA with triadic closure) run on the NetworkX backend for ≤ 1M edges; vectorised models (Chung-Lu, SBM, RMAT, bipartite with degree sequences) run on the columnar backend for anything larger. Realism metrics stay comparable across both because they are computed from the same edge table.
- **Processes** generate timestamped events over the structural edges (a transaction is an event on a customer→merchant or account→account relationship). Seasonality (hour, weekday, month), recurrence (salary, rent, subscriptions), per-category amount distributions.
- **Inject patterns** post hoc with labels; respect temporal ordering and account eligibility.
- **Apply noise** (duplication, missing values, shared-attribute collisions) and emit the gold.
- **Checkpoint** per shard/stage in a manifest; `--resume` skips completed stages.

### 5.3 Backends (`graphfaker.backends`): the key scale decision

**The canonical representation at scale is columnar: a node table and an edge table (Polars / Arrow), not an object graph.** NetworkX becomes a *view* materialised on demand for graphs that fit, and stays the home of the sequential realism models.

Why: 100M edges is two `int64` columns plus attributes. It fits in memory on a laptop as Arrow, streams to Parquet, and is exactly what every sink wants (Neo4j admin import, Ladybug `COPY FROM`, Neptune bulk, PyG tensors). Object graphs are 50 to 100 times larger and cannot be sharded.

Optional compute adapters for metrics on large graphs, selected by size or explicitly: `igraph` (fast C core, easy install), `networkit` (parallel, billions of edges), `cugraph` (GPU). All three consume edge lists directly from Arrow. NetworkX remains the default for compatibility.

### 5.4 Sinks (`graphfaker.sinks`)

Common interface `Sink.write(graph_tables, truth, manifest)`.

| Sink | Mechanism | Notes |
|---|---|---|
| Parquet / CSV | canonical, one file set per node/edge type | already the internal format |
| Neo4j | admin-import CSV (exists), plus live `neo4j` driver batched `UNWIND` | driver optional extra |
| LadybugDB / Kùzu | embedded, `COPY FROM` Parquet | best zero-infra local target; ships with query engine |
| Memgraph / FalkorDB | openCypher script (exists) or bolt | |
| Neptune | bulk-load CSV | port from gen-fraud-graph |
| TigerGraph | CSV + loading job skeleton | later |
| RDF | Turtle / N-Triples via `rdflib`; schema → lightweight OWL; node → IRI, attribute → datatype property, edge → object property, edge attributes → RDF-star (or reification fallback) | opens the semantic-web / Oxigraph / GraphDB audience |
| PyG / DGL | `HeteroData` / heterograph tensors + label masks from truth | GNN training |
| GraphML | exists | |
| gen-fraud-graph | byte-compatible `accounts/ transactions/ fraud/` layout | migration path for their users |

### 5.5 Truth, metrics, evaluation

- `truth/`: `patterns.parquet` (pattern_id, type, nodes, edges, timestamps), `duplicates.parquet` (cluster gold), `communities.parquet`, `manifest.json`.
- `metrics.graph_stats` / `compare_topology` extended to run on any backend.
- `metrics.realism_report(schema, tables)`: realised vs declared targets.
- `metrics.hardness_report(tables, truth)`: trivial-baseline AUCs per pattern type.
- `evaluate`: unify gen-fraud-graph's account/ring-level P/R/F1 with `resolve.evaluate_clusters` into one harness that scores any detector output against `truth/`.

### 5.6 Domain packs (`graphfaker.domains`)

A domain pack is a `GraphSchema` preset plus optional Python hooks (custom process or pattern implementations), discoverable via entry points (`graphfaker.domains`) so third parties can publish their own.

- `social`: today's generator, re-expressed.
- `fraud`: Phase 1 (§6).
- Later candidates: `supply_chain`, `healthcare_claims`, `identity_cyber`, `telco_cdr`.

### 5.7 Semantic layer (`graphfaker[llm]`, later)

Where language models help a graph generator, and where they do not:

- **Not for topology.** LLMs are poor at producing consistent structure and cannot scale to 10⁸ edges. Structure is statistical.
- **Schema authoring:** "a payments network for a mid-size bank with card, wire and P2P rails" → draft `GraphSchema` YAML the user edits.
- **Text attributes:** merchant names, transaction descriptions, bios, reviews, via Data Designer LLM columns when installed, or a minimal provider abstraction (OpenAI-compatible endpoint) when not. Generated once per category and sampled, not once per row, so cost stays flat with scale.
- **Corpus generation:** `corpus.py`, documents mentioning graph entities under aliases, for GraphRAG dedup benchmarking. Keep; move under the semantic layer.
- **Judges / validators:** realism checks on text fields.
- **Fit from seed (the enterprise feature):** learn marginals, conditionals (naive Bayes / Bayesian network over attributes) and degree/motif statistics from a *sample* of a real graph, then synthesise a privacy-safe twin at N× scale. This is what Mostly AI / SDV do for tables and is the reason enterprises pay for synthetic data. Design the schema so a fitted schema and a hand-written one are the same object.

---

## 6. Phase 1: the fraud / AML domain pack

This is the first product. Everything in §5 gets built exactly as far as this needs, and no further.

### Data model

| Node | Key attributes | Notes |
|---|---|---|
| `Customer` | person (locale), dob, address, phone, email, kyc_tier, segment, risk_appetite (latent) | one customer → 1..n accounts |
| `Account` | type (checking/savings/business/card), opened_at, balance (log-normal), currency, status | opened_at precedes any activity |
| `Merchant` | name, category (MCC), prominence (heavy-tailed), location | receives card / bill payments |
| `Device` | fingerprint, type, first_seen | shared by households (innocent) and mule rings (guilty) |
| `Bank` / `Counterparty` | external institution for wires | lets patterns cross the perimeter |

| Edge / event | Model |
|---|---|
| `Customer -OWNS-> Account` | 1..n, count ~ segment |
| `Customer -USES-> Device` | collision model: household share rate vs. ring share rate |
| `Account -PAYS-> Merchant` | bipartite, merchant degree ∝ prominence, per-category amount + recurrence |
| `Account -TRANSFERS-> Account` | PA + homophily on segment/geo; recurring (salary, rent) + ad hoc |
| `Account -WIRES-> Counterparty` | rare, heavy-tailed amounts |

Transactions are events from a `TransactionProcess` with hour/weekday/month seasonality and balance-aware amounts (an account does not spend what it does not have unless a pattern says so).

### Typology catalog (each emits labels at pattern, account and transaction level)

`fan_in`, `fan_out`, `gather_scatter`, `scatter_gather`, `cycle` (from gen-fraud-graph), `stack` (layering chains), `bipartite` (the AMLworld set) plus `structuring` (amounts just under reporting thresholds, spread over time), `mule_network` (shared devices + rapid pass-through), `bust_out` (credit build then max-out), `synthetic_identity` (attribute collisions across customers). Each typology has parameters and a *hardness* knob that controls how close its amounts, timing and degree profile sit to legitimate behaviour; decoys (legitimate structures that look like typologies) are generated at a declared rate.

### Deliverable

```bash
graphfaker fraud --scale 0.01 --hardness medium --seed 42 --sink parquet --out ./data
graphfaker fraud --scale 0.01 --sink ladybug --out ./fraud.lbdb
graphfaker fraud --scale 1.0 --workers 16 --sink neo4j-admin --out ./import
graphfaker evaluate ./data --flagged flagged_accounts.csv
```

```python
from graphfaker.domains import fraud
schema = fraud.schema(scale=0.01, hardness="medium")
run = GraphFaker(seed=42).generate(schema, workers=8)
run.to_parquet("./data"); run.truth.patterns; run.metrics.hardness_report()
```

**Acceptance:** scale `1.0` (10M accounts / ~90M transactions) generates on a 16-core laptop in well under an hour with bounded memory; realism report within declared targets; hardness report shows no single-feature baseline above 0.7 AUC at `hardness=high`; byte-identical re-runs for a given seed and worker count; loads cleanly into Neo4j and LadybugDB from the emitted files.

### Relationship to gen-fraud-graph

Depending on it as a library does not work: its API is `Config → run() → CSV files` with no reuse points, and its data model is the thing being replaced. Instead: re-implement its strengths (scale convention, sharding, resume, Neptune format, hardness presets, evaluation semantics, the cycle typology) inside the `fraud` domain pack, ship a byte-compatible output sink so their users can switch, and keep the door open to contribute a thin `gen-fraud-graph → graphfaker` adapter upstream. Apache-2.0 code we port carries its attribution in `NOTICE`.

---

## 7. Roadmap

| Phase | Scope | Outcome |
|---|---|---|
| **0: Foundations** (done) | `schema` models; columnar backend with NetworkX view; seed sharding; manifest; port the social generator onto the schema without regressing `test_topology` | Same graphs as v0.5, now declarative and reproducible per shard |
| **1: Fraud pack** (done) | data model, `TransactionProcess`, typology catalog, labels, `hardness_report`, `evaluate`; Parquet, Neo4j admin, LadybugDB sinks | The demo dataset for graph DBs, GNNs and AML teams |
| **2: Sinks and interchange** | RDF, PyG/DGL, Neptune, live Neo4j driver, gen-fraud-graph compat; YAML schemas; domain entry points; docs site | "Land it anywhere" |
| **3: Scale** | igraph/networkit/cuGraph adapters; vectorised triadic closure; 10⁹-edge target; optional Ray executor | Benchmark-grade sizes |
| **4: Semantic** | Data Designer interop extra; LLM schema authoring; text attribute providers; fit-from-seed | Enterprise synthetic twins |

Each phase ships on PyPI. Nothing in Phase 0 changes the public `GraphFaker.generate_graph` signature; new capability lands beside it and the old paths are deprecated over two releases.

---

### Phase 1 status (2026-09-13)

Shipped as `graphfaker.domains.fraud` plus `graphfaker.sinks`. What was learned by measuring rather than asserting:

- **Amount signals blend away as designed.** Transaction-level `amount` AUC 0.94 → 0.79 → 0.62 across low/medium/high.
- **Degree mostly does not.** Under the scale convention an account makes ~9 transactions a quarter, so a ring adds partners an ordinary account lacks (`in_partners` AUC ≈ 0.73 at `high`, against a 0.7 target). Ring size is the lever that works. Recruiting members among *active* accounts, tried as camouflage, measured as the opposite: hubs are outliers already, so rings built from hubs are found by degree alone; recruitment is uniform now. Options for later: a `density` knob decoupled from the scale convention, or camouflage that *adds* ordinary activity to pattern accounts.
- **Structuring and bust-out are single-feature typologies by definition** (near-threshold amounts; spend escalation). They are reported per typology; the "all" row is the number to quote.
- **The legitimate tail matters as much as the fraud.** A thin transfer-amount tail made every four-figure transfer an outlier; the process now uses a mixture with a large-transfer component (p99 ≈ $6–8K).
- **Small-scale artefacts are real artefacts.** With 50 merchants across 8 regions, category mix became a spurious regional signal that inverted income elasticity; merchant density is now ~1 per 50 accounts.
- **Process vs. attributes.** The vectorised transaction process handles 900K events in ~2 s; Faker attributes cost ~1 ms per customer and dominate. A vectorised person sampler (Data Designer's managed-dataset approach) is the Phase 3 item that unlocks `scale=1.0` in minutes rather than an hour.
- **Not done in Phase 1:** running-balance enforcement; chunked/streaming writes for 90M-row edge tables; the generic `Process`/`Pattern` schema members. The fraud pack implements them concretely, and they will be lifted into the schema once a second domain needs them.

## 8. Decisions

| Decision | Choice | Why |
|---|---|---|
| Columnar library | **Polars** (Arrow-native, lazy, Parquet built in); pandas kept for user-facing conversions | fastest path to 10⁸-row tables without a JVM |
| Config models | **Pydantic v2** | discriminated unions, YAML/JSON, validation errors users can read |
| Default local graph DB | **LadybugDB** (Kùzu continuation) | pip-installable, embedded, `COPY FROM` Parquet, Cypher |
| Package layout | single package, optional extras (`[neo4j] [ladybug] [rdf] [torch] [gpu] [llm] [datadesigner]`) | the 3-package split is premature |
| gen-fraud-graph | re-implement as domain pack + compat sink, not a dependency | see §6 |
| LLM role | attributes, schema authoring, corpus; never topology | scale and consistency |
| Python | ≥ 3.10 (unchanged) | |
| OSM / flights fetchers | keep as `sources`, reposition as seed substrates | real backbones for synthetic layers |

## 9. Resolved questions

1. **Scale factor:** keep gen-fraud-graph's convention for the fraud pack (`1.0 = 10M accounts / ~90M transactions`) so datasets are directly comparable.
2. **Realism targets:** ship literature-derived defaults per domain; users may override.
3. **RDF edge attributes:** RDF-star by default, reification fallback (decide in Phase 2).
4. **Santander:** ship first, then approach.
