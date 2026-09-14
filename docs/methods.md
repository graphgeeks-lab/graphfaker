# Ways to generate synthetic graph data

There are several families of methods for making synthetic graphs. They differ in what they can produce (structure only, or attributes and time as well), whether they can attach ground truth, and how far they scale. This page lists them, says which ones GraphFaker uses today and where, and which are planned. It is meant to help you judge whether GraphFaker's output is appropriate for what you are testing, and to guide anyone adding a domain.

## 1. Random graph models

These generate structure from a handful of parameters. They are well understood, fast, and produce nothing but nodes and edges.

| model | what it captures | what it misses |
|---|---|---|
| Erdős–Rényi | a baseline: every pair connected with the same probability | hubs, clustering, communities; degrees are Poisson |
| Watts–Strogatz | small-world: short paths with high clustering | heavy-tailed degrees |
| Barabási–Albert (preferential attachment) | heavy-tailed degrees, hubs | clustering, communities |
| configuration model, Chung-Lu | any prescribed degree sequence, in vectorised form | clustering, communities |
| stochastic block model | explicit communities with given densities | heavy tails within blocks (fixed by degree-corrected variants) |
| LFR benchmark | communities plus heavy-tailed degrees and community sizes | attributes, time |
| R-MAT, Kronecker | scale-free graphs at billions of edges by recursive sampling; used by Graph500 | realistic clustering, attributes |

**In GraphFaker.** The `social` topology model combines preferential attachment, triadic closure and homophily over latent groups, formed one edge at a time. It gives heavy tails, clustering and recoverable communities together, and it can look at attributes when choosing partners, which the models above cannot. The cost is that it is sequential and runs on a NetworkX graph, so it is the right tool up to about a million edges. `uniform` is Erdős–Rényi and exists for comparison.

**Planned.** Chung-Lu, stochastic block and bipartite degree-sequence models as further topology models, implemented on integer arrays for graphs that do not fit an object graph. The realism metrics are computed from the edge table, so the two kinds of model can be compared on the same numbers.

## 2. Attributed and correlated generators

Real graphs have attributes that agree with each other and with structure: people in the same community share interests, big cities have many roads. Generators in this family sample attributes and structure from a shared latent model. LDBC's Social Network Benchmark data generator is the best-known example: correlated attributes, communities and a timeline, built for benchmarking graph databases.

**In GraphFaker.** Latent factors are the mechanism. A node type declares which hidden groups it belongs to; group parameters are sampled once; attribute samplers reference them (`@community.mean_age`); the topology model prefers same-group partners. Attributes and edges are then correlated because they share a cause. Derived attributes (`employee_count` from WORKS_AT in-degree) close the loop in the other direction. See [how-it-works.md](how-it-works.md).

## 3. Process simulation with injected patterns

For temporal data such as payments, calls or logistics, a simulation produces events over time from rules about how agents behave, and then patterns of interest are injected with labels. AMLSim and its successor AMLworld (IBM) simulate banks and inject eight laundering typologies; PaySim simulates mobile money; Santander's gen-fraud-graph writes accounts and transactions with cyclic rings. Labels come for free because the generator knows what it injected.

**In GraphFaker.** The fraud pack is this kind of generator. Its legitimate process is vectorised (recurring flows, repeat partners, merchant popularity, seasonality, income scaling), its typology catalog covers the AMLworld set plus structuring, mule networks, bust-out and synthetic identities, and it adds two things the others lack: decoys (legitimate structures with the same shape, labelled as such) and a measured hardness (the AUC every naive single-feature detector achieves against the truth). See [fraud-generation.md](fraud-generation.md).

The same approach fits any domain where events happen on a relational backbone: supply chains, insurance claims, telecom call records, clinical pathways. Adding one means writing a process and a pattern catalog; see [adding-a-domain.md](adding-a-domain.md).

## 4. Fitting a generator to real data

Instead of hand-writing distributions, learn them from a sample of real data and then sample a larger, privacy-safe twin. For tables this is what SDV, CTGAN and commercial products do (copulas, Bayesian networks, GANs). For structure, Kronecker graphs can be fitted to a real graph (KronFit), and stochastic block models can be inferred.

**In GraphFaker.** Not yet. The schema is designed so that a fitted schema and a hand-written one are the same object: marginals become samplers, conditionals become latent-factor parameters and expressions, degree statistics become topology parameters. Fitting attribute distributions with a naive Bayes or Bayesian network over a seed table, and degree and motif statistics from a seed graph, is on the roadmap.

## 5. Deep generative models

GraphRNN, GraphVAE, NetGAN, GRAN and graph diffusion models learn to generate graphs from examples. They are strongest on many small graphs (molecules) and on reproducing subtle structural statistics of one graph. They are weak on attributes and time, offer no ground truth, and do not scale to millions of nodes.

Language models belong here too. They are good at producing plausible text and attribute values and at drafting a schema from a description, and poor at producing consistent structure at scale.

**In GraphFaker.** Not for topology. Planned uses of language models are schema authoring from a description, text attributes (merchant names, memos, bios) generated once per category and sampled, corpus generation for GraphRAG evaluation (already present in `graphfaker.corpus`), and judges for text realism.

## 6. Perturbation

Take a clean graph and corrupt it in known ways: duplicate an entity under a variant name, introduce typos, drop attributes, split a node's edges. Record-linkage benchmarks are built this way. The corruption is the ground truth.

**In GraphFaker.** `graphfaker.corpus` writes documents that mention graph entities under aliases and records the gold clusters, for measuring how many nodes a knowledge-graph builder creates per real entity. `graphfaker.resolve` is the matching side. A general noise layer in the schema (duplicate rate, corruption model, missing values, shared-attribute collisions) is planned; the fraud pack's synthetic identities are a first instance of shared-attribute collisions.

## Real-world sources

Sometimes the right substrate is real. GraphFaker loads OpenStreetMap road networks, BTS flight networks and Wikipedia pages. The plan is to use these as backbones for synthetic layers (deliveries on a real road network, passengers on a real flight network) rather than only as standalone datasets.

## Choosing

- You need structure only, fast, at scale: a random graph model. Use `social` for realism up to a million edges; use the planned vectorised models beyond that.
- You need attributes that make sense together and agree with structure: a schema with latent factors.
- You need events over time with labelled patterns to detect: a process simulation. Use the fraud pack, or write a domain.
- You need to mimic a specific real dataset: fitting, which is planned; today, write the schema from what you know about the data.
- You need text: a language model for the text, a schema for everything else.
