# How generation works

This page explains what happens between a schema and a folder of Parquet files. It covers the generic engine. The fraud pack builds on the same pieces and adds a transaction process and injected patterns; that is described in [fraud-generation.md](fraud-generation.md).

## The pieces

```
schema  ->  latent factors  ->  node tables  ->  edges  ->  derived attributes  ->  GraphRun
            (per-group          (sharded,        (topology                        (tables, truth,
             parameters)         seeded)          model)                           manifest)
```

A **schema** (`graphfaker.schema.GraphSchema`) declares the graph: node types with attribute samplers, latent factors, edge families, a topology model and optional realism targets. It is data. It validates before anything runs, round-trips through YAML and JSON, and hashes into the manifest. `graphfaker schema social --out mine.yaml` writes one to edit, and `graphfaker generate --schema mine.yaml` runs it; every run also writes the schema it used as `schema.yaml` next to its data.

The **engine** (`graphfaker.engine`) turns a schema into tables. `generate(schema, seed, shard_size, workers)` returns a `GraphRun`.

A **GraphRun** holds the result: `tables` (one Polars frame per node type and per relationship), `truth` (what the generator knows and a user should not have to reverse-engineer), and `manifest` (everything needed to reproduce the run).

## Step 1: latent factors

A latent factor is a hidden grouping that every node belongs to. The social schema has one called `community`. Each group gets its own parameters, sampled once: a community has a mean age, a preferred education level and a region.

Attribute samplers can refer to those parameters with the `@factor.param` syntax, for example `GaussianSampler(mean="@community.mean_age", sd=9)`. A person's age is then drawn around their community's mean rather than from one population-wide distribution.

This is the mechanism behind two properties real graphs have and random graphs do not. Attributes are correlated with each other through the shared group, and, because the topology model prefers same-group edges, attributes are correlated with structure. Without a shared hidden variable, "like attaches to like" has nothing to key on.

## Step 2: node tables

Node types are generated in foreign-key order: if `Person.home_place` points at `Place`, places come first.

Each node type is split into shards of `shard_size` rows. Every shard draws from its own random stream (see reproducibility below) and never looks at another shard, so shards can run in separate processes (`workers`) without changing the result.

Within a shard, attributes are drawn column by column in the order the schema lists them. Later attributes may depend on earlier ones: `ExpressionSampler` evaluates a Python expression over the row so far, `SubcategorySampler` picks from a list chosen by a parent attribute, `ForeignKeySampler` picks a node of another type, preferring one in the same latent group.

The available samplers are: `constant`, `category`, `subcategory`, `uniform`, `gaussian`, `lognormal`, `poisson`, `bernoulli`, `faker` (any Faker provider), `expression`, `reference` (copy a group parameter), `mixture` (choose between samplers by weight) and `foreign_key`. The names follow NVIDIA Data Designer's sampler vocabulary so the two read alike.

## Step 3: edges

The topology model decides who connects to whom. Two models ship today.

`uniform` draws both endpoints at random. It produces an Erdős–Rényi graph and exists only for comparison.

`social` forms edges one at a time with three mechanisms:

- **Preferential attachment.** Candidates are drawn with probability proportional to degree plus one, so popular nodes attract more edges and the degree distribution grows a heavy tail. A fraction of draws ignore degree (`uniform_attachment_rate`) so the tail stays bounded and low-degree nodes remain reachable.
- **Homophily.** Several candidates are drawn per edge (`affinity_sample_size`), most from the source's own latent group (`same_group_rate`), and the most plausible one is kept. Plausibility adds points for a shared group, for the target's prominence, and for attribute similarity as configured (`numeric_affinity`, `categorical_affinity`).
- **Triadic closure.** For edge families marked `closure` (friendships), a share of edges (`triadic_closure_rate`) connect a node to a friend of a friend. This is the only source of clustering.

Two rules keep the result sensible. Relationships marked `functional` (LIVES_IN, BORN_IN) are created at most once per node. A first pass walks every source node once before preference takes over, and a top-up pass attaches any node left isolated and fills the edge budget, so the graph has one giant component and `number_of_edges()` matches what was asked for.

Edge attributes are sampled per edge from the relationship's samplers.

The social model is sequential and runs on a NetworkX graph. It is the right tool up to about a million edges; [methods.md](methods.md) says what to use beyond that.

## Step 4: derived attributes

Some attributes should agree with the structure that was built rather than be sampled independently. `DegreeDerived` computes an attribute from a node's degree in one relationship after edges exist. An organization's `employee_count` is derived from its WORKS_AT in-degree, scaled up by prominence because the people in the graph are a sample of the workforce.

## Step 5: tables, truth, manifest

The canonical representation is columnar: a Polars frame per node type (with an `id` column) and per relationship (with `source` and `target`). This is what every loader wants and what scales; a NetworkX object graph is 50 to 100 times larger. `run.to_networkx()` materialises the object view when you need algorithms or drawing.

`run.truth` holds what the generator knows: for the generic engine, the latent groups and their parameters; for the fraud pack, also the injected patterns, their accounts and their transactions.

`run.manifest` records the schema name and digest, the seed, the shard size, the engine and package versions, and the node and edge counts. `run.write(dir)` produces:

```
dir/
  nodes/<Type>.parquet
  edges/<RELATIONSHIP>.parquet
  truth/<name>.parquet
  schema.yaml
  manifest.json
```

Parquet is written through pyarrow, in row groups of two million rows, so that third-party loaders (LadybugDB `COPY`, DuckDB, Spark) read the timestamps.

## Reproducibility

A run is a function of the schema, the seed and the shard size. Nothing else affects the bytes: not the number of workers, not the machine, not `PYTHONHASHSEED`.

The contract is per version. A release may change what a seed produces, because drawing a column faster or modelling a process better changes the draws; 0.6.0 and 1.0.0 both did, and each release that does says so in the changelog. `manifest.json` records `graphfaker_version` and `engine_version` next to the seed and the shard size, so a dataset always carries what is needed to make it again.

Randomness comes from one `numpy.random.SeedSequence` rooted at the seed. Stages and shards spawn children from it in a fixed order (latent factors, then one child per node type, then one for edges; within a node type, one child per shard). Each child feeds a numpy generator, a `random.Random` and a Faker instance, so every consumer of randomness in a stage draws from the same lineage.

The engine avoids Python sets where iteration order matters, because set order for strings depends on the process hash seed. A test runs the same seed in two subprocesses with different hash seeds and checks the output is identical.

## Sharding and workers

`shard_size` is part of the reproducibility contract and is recorded in the manifest. `workers` is not: it only decides how many processes execute the shards. The default shard size is 10,000 rows.

Columns are drawn a column at a time, not a row at a time. The simple samplers (constant, category, uniform, gaussian, lognormal, poisson, bernoulli, reference) are one numpy call each, with `@factor.param` references resolved by gathering each row's group value. Faker providers are drawn by `graphfaker.engine.fastfaker`, which reads Faker's own locale tables (names, weights, address and phone formats) and fills a whole column from the shard's stream; the vocabulary and the weights are Faker's, the cost is about a microsecond a value instead of a millisecond. Providers it does not cover (`iban`, `catch_phrase`) fall back to Faker row by row, as do expressions, mixtures, subcategories and foreign keys, which look at other columns. Parallel workers split the shards across processes on top of that.
