# graphfaker

**Synthetic graph data that behaves like the real thing.**

GraphFaker generates synthetic graph datasets that look and behave like real ones. You describe the graph you need, or pick a ready-made domain such as a bank with laundering patterns, and GraphFaker produces the entities, the relationships between them and the events over time, with attributes, structure and timing that agree with each other, and a record of everything it planted. Use it to build and demo graph applications without touching real data, to benchmark graph databases and algorithms at any size, and to train and evaluate fraud detectors, entity resolution and knowledge-graph pipelines against a known answer.

[![PyPI version](https://img.shields.io/pypi/v/graphfaker.svg)](https://pypi.python.org/pypi/graphfaker)
[![Docs Status](https://readthedocs.org/projects/graphfaker/badge/?version=latest)](https://graphfaker.readthedocs.io/en/latest/?version=latest)
[![image](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Join our Discord server: [![](https://dcbadge.limes.pink/api/server/https://discord.gg/mQQz9bRRpH)](https://discord.gg/mQQz9bRRpH)

*The authors and GraphGeeks Lab do not hold any responsibility for the correctness of this generator.*

## Why graphs need their own generator

Tabular generators such as NVIDIA Data Designer, SDV and Mostly AI produce rows. A graph needs more than rows joined together: a degree distribution with hubs, clustering and communities, events that happen in a plausible order over time, and labels for what was injected. None of that comes from generating a table of people and a table of payments and joining them. Data Designer generates the tables; GraphFaker generates the connections.

The graphs people most want to test against, such as who pays whom, who knows whom, or who shares a device with whom, are the ones that cannot be shared. GraphFaker makes them, and because it made them it can tell you the answer: which accounts are the mules, which two nodes are the same person, which community a node belongs to.

## Install

```sh
pip install graphfaker
pip install "graphfaker[osm]"         # the OpenStreetMap fetcher (osmnx and its geo stack, about 150 MB)
pip install "graphfaker[examples]"    # adds matplotlib, ladybug and jupyter for the notebooks
```

The fraud pack, the social domain, schemas and every sink are in the base install. Database clients and the PyG export are extras: `[neo4j]`, `[ladybug]`, `[duckdb]`, `[pyg]`. `graphfaker info` shows which are installed.

Or without a Python environment, as a container ([docs](https://graphfaker.readthedocs.io/en/latest/get-started/docker.html)):

```sh
docker run --rm -v "$PWD/bank:/data" ghcr.io/graphgeeks-lab/graphfaker:main fraud --scale 0.01 --seed 42 --out /data
```

For development:

```sh
git clone https://github.com/graphgeeks-lab/graphfaker.git
cd graphfaker
uv venv && uv pip install -e ".[dev,examples]"
```

## Three things to try

See what can be generated:

```sh
graphfaker domains
```

Generate a bank with labelled laundering patterns, written to disk with its ground truth:

```sh
graphfaker generate fraud --scale 0.01 --hardness medium --seed 42 --out ./bank
```

`--scale 0.01` is the size (about 100,000 accounts and 900,000 transactions; `1.0` is a full-size bank). `--hardness medium` is how well the fraud hides among normal activity. `--seed 42` is any whole number you choose; run the same command with the same seed on any machine and you get the same data, change it and you get a different bank of the same shape. The options are explained under [Command line](#command-line).

Load it into an embedded graph database and ask it a question in Cypher:

```sh
pip install "graphfaker[ladybug]"
graphfaker generate fraud --scale 0.01 --seed 42 --out ./bank --sink ladybug
```

```python
import ladybug
conn = ladybug.Connection(ladybug.Database("bank/graph.lbdb"))
conn.execute("MATCH (a:Account)-[t:TRANSFERS]->(b:Account) WHERE t.amount > 9000 RETURN a.id, count(t) ORDER BY count(t) DESC LIMIT 5").get_all()
```

The same from Python:

```python
from graphfaker import GraphFaker
from graphfaker.domains import fraud

G = GraphFaker(seed=42).generate_graph(source="faker", total_nodes=500, total_edges=2500)   # a social graph, as NetworkX
run = fraud.generate(scale=0.01, hardness="medium", seed=42)                                   # the bank, as tables plus truth
```

## Where to read next

| you want to | read |
|---|---|
| see everything in one place, with charts | [docs/notebooks/graphfaker_tour.ipynb](docs/notebooks/graphfaker_tour.ipynb), or run [examples/fraud_tour.py](examples/fraud_tour.py) |
| understand how a schema becomes a graph | [docs/how-it-works.md](docs/how-it-works.md) |
| understand how the bank and its fraud are generated | [docs/fraud-generation.md](docs/fraud-generation.md) |
| load a dataset into Neo4j, or an embedded LadybugDB, and check the load | [docs/neo4j.md](docs/neo4j.md), [docs/ladybug.md](docs/ladybug.md) |
| know which generation methods exist and which GraphFaker uses | [docs/methods.md](docs/methods.md) |
| add your own domain (supply chain, claims, telecom, ...) | [docs/adding-a-domain.md](docs/adding-a-domain.md) |

## What you can generate

| domain | what it is | how |
|---|---|---|
| `social` | people, places, organizations, events and products; heavy-tailed degrees, clustering, communities, attributes that agree with structure | `GraphFaker.generate_graph(source="faker")` or `graphfaker generate social` |
| `fraud` | a bank: customers, accounts, merchants, devices, counterparties; a realistic transaction process; eleven labelled laundering typologies with decoys and a measured hardness | `graphfaker fraud` or `graphfaker generate fraud` |
| your own | a `GraphSchema`, or a process with injected patterns | [docs/adding-a-domain.md](docs/adding-a-domain.md) |

Real-world sources, loaded rather than generated:

| source | what it gives you |
|---|---|
| `osm` | road, walking or cycling networks from OpenStreetMap, by place name, address or bounding box (`pip install "graphfaker[osm]"`) |
| `flights` | airline, airport and flight-leg networks from BTS on-time data, for a month or a date range |
| `WikiFetcher` | Wikipedia page text, sections, links and references as JSON, for building your own graph or RAG pipeline |

List the domains and their options with `graphfaker domains`.

## Schemas

A synthetic graph is declared as a `GraphSchema`: node types with attribute samplers, latent factors shared across them, edge families, and a topology model. The social graph is one; you can read it, change it and save it.

```python
from graphfaker import GraphFaker, GraphSchema
from graphfaker.domains import social

schema = social.schema(total_nodes=1000, total_edges=8000, communities=8)
schema.to_yaml("social.yaml")
schema = GraphSchema.from_yaml("social.yaml")

run = GraphFaker(seed=42).generate(schema)
run.tables.nodes["Person"]      # a Polars frame per node type
run.tables.edges["WORKS_AT"]    # a Polars frame per relationship
run.truth["community"]          # the latent groups and their parameters
run.manifest                    # schema digest, seed, shard size, versions
run.write("out/")               # nodes/, edges/, truth/, schema.yaml, manifest.json
G = run.to_networkx()           # the NetworkX view
```

A small schema of your own:

```python
from graphfaker.schema import *

schema = GraphSchema(
    name="tiny_payments",
    latent=[LatentFactor(name="region", groups=4, params={"log_wealth": GaussianSampler(mean=8, sd=0.6)})],
    nodes=[
        NodeType(name="Account", count=500, attributes={
            "owner": FakerSampler(provider="name"),
            "balance": LognormalSampler(mu="@region.log_wealth", sigma=1.0, decimals=2),
            "tier": CategorySampler(values=["basic", "plus", "premium"], weights=[7, 2, 1]),
        }),
    ],
    edges=[EdgeType(source="Account", target="Account", share=1.0,
                    relationships=[Relationship(name="PAYS", attributes={"amount": UniformSampler(low=5, high=500, decimals=2)})])],
    total_edges=4000,
    topology=SocialTopology(group="region"),
)
```

A parameter written as `"@factor.param"` varies by latent group. That is how attributes end up correlated with each other and, through the topology model, with structure. The samplers are `constant`, `category`, `subcategory`, `uniform`, `gaussian`, `lognormal`, `poisson`, `bernoulli`, `faker`, `expression`, `reference`, `mixture` and `foreign_key`. The full mechanism is in [docs/how-it-works.md](docs/how-it-works.md).

## The fraud pack

A bank's transaction graph is the most useful dataset a fraud team cannot share. GraphFaker generates one that behaves like it without containing anyone: customers, accounts, merchants and devices with correlated attributes; a transaction process with salaries, rent, repeat partners and seasonality; and money-laundering typologies injected on top with every account, role and transaction recorded. Realism is measured, not asserted (degree distribution, clustering, communities, assortativity against a random baseline), and so is how hard the fraud is to find (the AUC of every single-feature rule a detector would try first). The result loads into Neo4j, LadybugDB or DuckDB and verifies against the files it came from, with a blind copy for honest benchmarks. No real customer data goes in, so none can come out.

`fraud.generate` builds a bank and a period of activity: salary on payday, rent on the first, subscriptions, card payments that follow merchant popularity, transfers that go mostly to the same few contacts, seasonality by hour and weekday, amounts that scale with income. Then it injects laundering typologies and records them.

```python
from graphfaker.domains import fraud
from graphfaker.domains.fraud.hardness import hardness_report, realism_report
from graphfaker.domains.fraud.evaluate import evaluate

run = fraud.generate(scale=0.01, hardness="high", seed=42, workers=8)
run.tables.edges["TRANSFERS"]    # source, target, tx_id, timestamp, amount, memo, recurring
run.truth["patterns"]            # pattern_id, typology, is_fraud, accounts, roles, start, end
run.truth["accounts"]            # account_id, pattern_id, typology, role, is_fraud
run.truth["transactions"]        # tx_id, pattern_id, typology, is_fraud
print(hardness_report(run).summary())
print(evaluate(run, flagged_accounts=my_detector(run)).summary())
```

`scale` follows gen-fraud-graph: `1.0` is about 10M accounts and 90M transactions.

The typologies are `fan_in`, `fan_out`, `gather_scatter`, `scatter_gather`, `cycle`, `stack`, `bipartite` (the AMLworld set), plus `structuring` (deposits under the reporting threshold), `mule_network` (pass-through within hours, a shared device, freshly opened accounts), `bust_out` (credit built up then maxed out) and `synthetic_identity` (customers sharing phone, address and device). The edge tables carry no labels, and transaction ids are assigned in time order so the id does not reveal what was injected.

`hardness` (`low`, `medium`, `high`) blends signature amounts into legitimate ones, spreads timing from hours to weeks, overlaps rings, shrinks ring sizes, keeps or strips normal activity on pattern accounts, and adds decoys: payroll fan-outs, marketplace fan-ins and supplier cycles that are labelled as not fraud. `hardness_report` measures the result: for each single feature a simple rule might threshold on (amount, round amounts, proximity to the threshold, degree, pass-through, burstiness, account age), the AUC it achieves against the truth. On a 20K-account run:

| hardness | transaction `amount` AUC | best account-level feature AUC |
|---|---|---|
| low | 0.94 | 0.85 (`max_amount`) |
| medium | 0.79 | 0.75 (`max_amount`) |
| high | 0.62 | 0.73 (`in_partners`) |

Amount signals fade as intended. Degree fades less, because the scale convention gives an account about nine transactions a quarter and a ring adds partners an ordinary account does not have. The full method, every typology's signature, and what each hardness parameter changes are in [docs/fraud-generation.md](docs/fraud-generation.md).

`evaluate` scores flagged accounts and transactions at account, transaction and pattern level, the way gen-fraud-graph's evaluator does. From the command line: `graphfaker evaluate ./bank --accounts flagged.txt --ring-threshold 0.5`.

## Write, read, query

`run.write(dir)` writes Parquet (`nodes/`, `edges/`, `truth/`), `schema.yaml` and `manifest.json`. `GraphTables.read_parquet(dir)` and `Manifest.read(path)` read them back.

Sinks put the same tables into a database's own loader format:

| sink | what it writes | use |
|---|---|---|
| `load_tables`, `load_directory` | batched `UNWIND` writes into a **running** Neo4j over Bolt, including the truth as a subgraph | a Neo4j you already have up, or Aura; no restart, no file staging |
| `verify_tables`, `verify_directory` | every check in [docs/neo4j.md](docs/neo4j.md): counts, constraints, endpoint labels, per-property aggregates, sampled round trips, truth coverage | proving the load is the dataset, in CI |
| `write_ladybug`, `graphfaker load ladybug` | DDL, `COPY FROM` Parquet and the truth subgraph; loads and verifies when the `ladybug` driver is installed | an embedded graph database, one file, Cypher, no server; see [docs/ladybug.md](docs/ladybug.md) |
| `write_duckdb`, `graphfaker load duckdb` | the tables as DuckDB tables plus a `CREATE PROPERTY GRAPH` for SQL/PGQ pattern queries; DuckDB reads the Parquet itself | no graph database at all: SQL, with graph patterns, on the same files; see [docs/duckdb.md](docs/duckdb.md) |
| `to_hetero_data`, `write_pyg`, `graphfaker generate ... --sink pyg` | a PyTorch Geometric `HeteroData`: features encoded, `y` and `decoy` on accounts, `y` and `edge_time` on transactions, stratified train/val/test masks | training and evaluating GNNs against a known answer; see [docs/pyg.md](docs/pyg.md) |
| `write_neo4j_admin` | typed CSVs and the `neo4j-admin database import` command | the fastest path into Neo4j, offline, at any scale |
| `write_gen_fraud_graph` | gen-fraud-graph's `accounts/`, `transactions/`, `fraud/` layout | pipelines built on that generator |
| `export_csv`, `export_neo4j_csv`, `export_cypher` | from a NetworkX graph: CSV, neo4j-admin CSV, Cypher, openCypher, ISO GQL | Memgraph, Neptune, TigerGraph, any bulk loader |
| `export_graph` | GraphML | Gephi, Cytoscape, igraph |

```python
from graphfaker import GraphTables, Manifest
from graphfaker.sinks import Target, load_directory, verify_directory, write_ladybug, write_neo4j_admin

tables = GraphTables.read_parquet("bank")
write_neo4j_admin(tables, "bank/neo4j")
write_ladybug(tables, "bank", db_path="bank/graph.lbdb")

target = Target(database="fraud", password="...")
print(load_directory("bank", target, wipe_first=True).summary())
assert verify_directory("bank", target).ok
```

On the command line, `--sink parquet|neo4j|neo4j-admin|ladybug|duckdb|pyg|gen-fraud-graph` on `graphfaker fraud` and `graphfaker generate` (add `--blind` to keep the ground truth out of the database), and `graphfaker load neo4j|ladybug|duckdb` / `graphfaker verify neo4j|ladybug|duckdb` for a dataset already on disk. All three loaders put the truth in the graph the same way (`Pattern` nodes, `IN_PATTERN` memberships with roles, `is_fraud` on the money relationships) and verify the load with the same checks.

## Reproducibility

A run is a function of the schema (or domain options), the seed and the shard size. The number of workers, the machine and the process hash seed do not change the bytes. The same seed gives the same graph in any process.

That holds within a version. Across releases the bytes may change, because a faster or more realistic generator draws differently: 0.6.0 changed them and so did 1.0.0, and `HISTORY.rst` says so each time. `manifest.json` records the version that wrote a dataset, so pin the version next to the seed when a dataset has to come back exactly, in a paper or a benchmark:

```sh
pip install graphfaker==1.0.0
graphfaker fraud --scale 0.01 --seed 42 --out ./bank
```

```python
GraphFaker(seed=42).generate_graph(source="faker", total_nodes=100)
fraud.generate(scale=0.001, seed=42)
```

## Command line

Every domain can be generated from the command line. `graphfaker domains` shows what is available and which options each one takes; `graphfaker generate <domain>` runs one, with the domain's options passed as `--name value`.

```sh
graphfaker domains
```

```
fraud: A bank: customers, accounts, merchants, devices; transactions; labelled laundering typologies.
    --scale <float>  default 0.001
    --hardness <low | medium | high>  default 'medium'
    --period-days <int>  default 90
    ...
social: People, places, organizations, events and products with realistic social structure.
    --total-nodes <int>  default 100
    --total-edges <int>  default 1000
    --topology <realistic | uniform>  default 'realistic'
    --communities <int | None>  default None
```

Generate a social graph of 2,000 people, places, organizations, events and products with 12,000 relationships:

```sh
graphfaker generate social --total-nodes 2000 --total-edges 12000 --seed 42 --out ./social
```

Generate a bank with 100K accounts and labelled laundering patterns that are hard to find:

```sh
graphfaker generate fraud --scale 0.01 --hardness high --seed 42 --out ./bank
```

Or generate from a schema file, which is how you get a graph of your own without writing Python. Start from a built-in schema, edit it, run it:

```sh
graphfaker schema social --total-nodes 500 --out my_graph.yaml     # a domain's schema as YAML, with its options applied
graphfaker generate --schema my_graph.yaml --seed 42 --out ./my_graph
```

The file is the same `schema.yaml` every run writes next to its data, so a dataset can be regenerated, or varied, from the file it came with; `graphfaker validate` checks an edited file before a run. `graphfaker schema fraud` prints the bank's entity schema with a note that its transactions and patterns come from code; `graphfaker generate fraud` is the way to get the bank.

Both commands write `nodes/`, `edges/`, `truth/`, `schema.yaml` and `manifest.json` under `--out`. Options shared by every domain:

| option | meaning |
|---|---|
| `--out DIR` | where to write (default `graphfaker_out`) |
| `--seed N` | reproducible output; the same seed gives the same bytes on any machine, with the same version of GraphFaker |
| `--schema FILE` | generate from a schema YAML instead of a named domain (`--shard-size N` sets the rows per shard, default 10000, and is part of what the seed reproduces) |
| `--workers N` | processes for node sampling; faster, does not change the result |
| `--sink parquet\|neo4j\|neo4j-admin\|ladybug\|duckdb\|pyg\|gen-fraud-graph` | also load a live database, or write a loader layout (Parquet is always written) |
| `--quiet`, `-q` | no progress logging; warnings and errors only |
| `--json` | when done, print the manifest (and for `fraud`, the hardness and realism reports) as one JSON document on stdout. Logging goes to stderr, so a pipeline can read stdout |

Three commands look at datasets and schema files without generating anything. `graphfaker inspect ./bank` reads the manifest and the file layout and prints what a directory contains (schema, seed, versions, counts, which truth tables are there, the domain options), with `--json` for scripts. `graphfaker validate my_graph.yaml other.yaml` checks schema files with the same rules `generate --schema` applies and exits non-zero if any fails. `graphfaker evaluate ./bank --accounts flagged.txt --json` scores a detector's output as JSON.

### What the options mean

**Options every domain has**

- `--seed N`: any whole number. Generation is random, but the randomness is derived from the seed, so the same seed with the same options produces the same bytes on any machine. Use a seed when you want a dataset others can regenerate (a benchmark, a tutorial, a bug report). Leave it out and every run produces a different dataset.
- `--out DIR`: the folder to write. It will contain `nodes/`, `edges/`, `truth/`, `schema.yaml` and `manifest.json`. The manifest records the seed and every option, so a run can always be reproduced from its folder.
- `--workers N`: how many processes generate node attributes. Use it on large runs; it changes the speed and nothing else.
- `--sink`: what else to do besides writing Parquet. `neo4j` loads a running Neo4j over Bolt (configured with `NEO4J_URI`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`); `ladybug` builds an embedded graph database you can query in Cypher; `duckdb` builds a DuckDB file with a SQL/PGQ property graph on the tables; `pyg` writes a PyTorch Geometric `HeteroData` with labels and splits to `graph.pt`; `neo4j-admin` writes CSVs and the `neo4j-admin database import` command; `gen-fraud-graph` writes the layout of Santander's generator for pipelines built on it.

**`fraud` options** (see [docs/fraud-generation.md](docs/fraud-generation.md) for the mechanics)

- `--scale`: the size of the bank. `1.0` follows gen-fraud-graph's convention of about 10 million accounts and 90 million transactions; everything else scales with it. Pick from this table:

  | scale | accounts | transactions | patterns | typical time on a laptop |
  |---|---|---|---|---|
  | 0.001 | 10,000 | 90,000 | 22 | a second |
  | 0.01 | 100,000 | 900,000 | 22 | a few seconds |
  | 0.1 | 1,000,000 | 9,000,000 | 100 | under a minute |
  | 1.0 | 10,000,000 | 90,000,000 | 1,000 | about seven minutes and 15 GB of memory; see Performance and limits |

- `--hardness`: how well the injected fraud hides in normal activity. It changes the amounts, the timing, the ring sizes, whether pattern accounts also have ordinary activity, and whether look-alike legitimate structures (decoys) are added.

  | level | what the fraud looks like | use it for |
  |---|---|---|
  | `low` | round amounts, a whole pattern within hours, single-purpose accounts, no decoys; a simple rule finds most of it | demos and tutorials where the pattern should be visible |
  | `medium` | half the amounts look ordinary, patterns spread over days, most pattern accounts also behave normally, decoys added | the default; realistic enough to develop against |
  | `high` | amounts and timing look ordinary, small rings, all pattern accounts behave normally, as many decoys as patterns; only structure and context give it away | benchmarking detectors and graph algorithms |

  `graphfaker fraud` prints a hardness report that measures this: for each simple feature (amount, round numbers, degree, and so on), how well a threshold on it alone separates fraud from legitimate accounts.

- `--period-days` and `--period-start`: the span of transaction history, 90 days from 2024-01-01 by default. Longer periods give recurring flows more cycles and patterns more room.
- `--reporting-threshold`: the cash reporting threshold that structuring stays under, 10,000 by default.
- `--patterns`: override how many of each typology to inject, for example `--patterns '{"cycle": 10, "fan_in": 5}'`. By default the count follows `--scale` with at least two of each.
- `--regions`: the number of latent regions (8 by default). Income, balances, merchant choice and transfer partners are correlated within a region.

**`social` options**

- `--total-nodes`: how many nodes, split 50% people, 20% places, 15% organizations, 10% events, 5% products.
- `--total-edges`: how many relationships across the seven edge families (friendships, where people live and work, what organizations make, and so on).
- `--communities`: how many latent communities. Ages, education and regions cluster by community, and people mostly connect within their community. Defaults to about one per 25 nodes.
- `--topology`: `realistic` forms edges by preferential attachment, triadic closure and homophily; `uniform` connects nodes at random and exists only to show the difference.

Load the bank into a graph database you can query. Into a running Neo4j, which then verifies itself against the Parquet it came from:

```sh
pip install 'graphfaker[neo4j]'
export NEO4J_PASSWORD=...
graphfaker load neo4j ./bank --database fraud --create
```

```
loaded into database 'fraud' in 81.4s (16,511 rows/s)
  nodes    255,772  Account=100000, Counterparty=200, Customer=71429, Device=82143, Merchant=2000
  edges  1,088,552  OWNS=100000, PAYS=619689, TRANSFERS=247336, USES=87093, WIRES=34434
  truth        622  Pattern=33, IN_PATTERN=205, PAYS.is_fraud=31, TRANSFERS.is_fraud=345, Region=8

PASS: 154/154 checks on database 'fraud'
```

`--blind` loads the graph without the ground truth, so the same dataset can still be used as an unbiased benchmark. [docs/neo4j.md](docs/neo4j.md) covers the checks, a scored Cypher cookbook for the laundering typologies, and when to prefer the offline importer instead.

Or into an embedded database, or as Neo4j import files:

```sh
graphfaker generate fraud --scale 0.01 --seed 42 --out ./bank --sink ladybug        # ./bank/graph.lbdb, query it in Cypher
graphfaker generate fraud --scale 0.01 --seed 42 --out ./bank --sink duckdb         # ./bank/graph.duckdb, query it in SQL/PGQ
graphfaker generate fraud --scale 0.01 --seed 42 --out ./bank --sink neo4j-admin    # ./bank/neo4j/, run import.sh
```

`graphfaker fraud` is a shortcut for the fraud domain that also prints the hardness and realism reports:

```sh
graphfaker fraud --scale 0.01 --hardness medium --seed 42 --out ./bank
```

Score a detector's flagged accounts (one id per line) against the truth of a run:

```sh
graphfaker evaluate ./bank --accounts flagged.txt --ring-threshold 0.5
```

`graphfaker gen` is the other entry point, and the two do different jobs. `generate` and `fraud` run the schema engine and write tables, ground truth and a manifest. `gen` loads a real-world network or builds a quick in-memory social graph and exports it as a single file (`--format graphml|csv|neo4j-csv|cypher|opencypher|gql`). Both are supported in 1.x; use the engine for datasets you will load into a database or train on, and `gen` for a real-world network or a one-file export:

```sh
graphfaker gen --fetcher osm --place "Berlin, Germany" --network-type drive --export berlin.graphml
graphfaker gen --fetcher flights --country "United States" --year 2024 --month 1 --export flights.graphml
graphfaker gen --fetcher faker --total-nodes 500 --total-edges 2500 --format cypher --export social.cypher
```

`graphfaker --help` and `graphfaker <command> --help` list every option. `graphfaker --version` prints the version; `graphfaker info` prints it with the versions of the libraries that decide reproducibility (polars, numpy, pyarrow, faker, networkx) and which extras are installed, which is the first thing to paste into a bug report or to check inside a container.

## Entity resolution

Knowledge graphs built by extraction often contain the same real-world entity as several nodes. `resolve()` scores candidate pairs on attribute similarity and on neighbourhood overlap, the signal a tabular record-linkage tool cannot see, then merges each cluster onto one node.

```python
gf = GraphFaker(seed=42)
gf.generate_graph(source="faker", total_nodes=500, total_edges=2000)
result = gf.resolve(on=["name", "email"], threshold=0.85)
print(result.report())
clean = result.apply()          # a merged copy; the original is untouched
```

`structural_weight` sets how much shared-neighbour evidence may raise a pair's score (0 gives plain attribute matching). Shortened names such as `"Hill"` against `"Allison Hill"` are floored at `token_subset_floor` so that the decision is left to the graph. `evaluate_clusters(predicted, gold)` scores a clustering you already have labels for (pairwise and B-cubed F1).

## Measuring entity duplication

`graphfaker.corpus` writes documents whose entities are known in advance, so you can count how many nodes a graph builder creates per real entity. The text is clean English and every entity is unambiguous to a reader, so a correct pipeline scores zero.

```python
from graphfaker.corpus import generate_corpus, duplication_report

corpus = generate_corpus(seed=42, n_entities=60, n_documents=80)
corpus.write("corpus/")                      # documents plus gold.json
report = duplication_report(extracted_graph, corpus, framework="my-pipeline")
print(report.summary())
```

[docs/notebooks/duplication_experiment.ipynb](docs/notebooks/duplication_experiment.ipynb) runs this end to end with Cognee and repairs the result with `resolve()`.

## Performance and limits

Generation is linear in the size of the data. Apple M3 Pro, single process, generation only, best of three runs; the same command at every scale:

```sh
graphfaker fraud --scale 0.1 --seed 42 --out ./bank
```

| scale | accounts | transactions | 0.5.0 | 0.6.0 |
|---|---|---|---|---|
| 0.01 | 100K | 900K | 14.5 s | 1.1 s |
| 0.1 | 1M | 9M | 159.8 s | 12.2 s |
| 0.3 | 3M | 27M | 709.9 s | 39.8 s |
| 1.0 | 10M | 90M | over 2 hours (Windows, 4 workers) | 7 min (Windows, single process, 0.6.1) |

At `scale=0.1` the time splits roughly 57% node attributes, 38% the transaction process, and the rest indexing and pattern injection. Only the first part is parallel, so four workers are worth about 1.5x from `scale=0.1` upward (an Amdahl bound of 1.6x) and nothing below it, where node types stay in process because the pool's start-up would cost more than it saves.

Memory: the transaction process works a block of accounts at a time and the channels are assembled without a second copy, so the peak is about twice the size of the final tables: on Windows 2.5 GB at `scale=0.1`, 5.2 GB at `scale=0.3` and 15 GB at `scale=1.0` (0.6.0 needed 32 GB). A full-size bank fits a 16 GB machine with little else running; 32 GB is comfortable. macOS reports lower peaks for the same runs because it compresses idle pages out of the resident set, so size from the Windows numbers.

Two other limits are unchanged. The social topology model is sequential and suits graphs up to about a million edges. Balances are not tracked as a running ledger, so an account's balance is a starting attribute rather than the sum of its transactions.

Datasets changed in 0.6.0: a run is still a pure function of its seed and shard size, but the attribute values differ from 0.5.0's for the same seed, because columns now come from the shard's numpy stream rather than from Faker's call sequence, and edge counts move by about 0.15% as a consequence. Structure and distributions are unchanged.

[docs/scaling.md](docs/scaling.md) has the measurements (the seven-scale curve on two machines, the phase split, workers against Amdahl's bound, memory) and [docs/scaling-and-realism.md](docs/scaling-and-realism.md) what they mean: why speed is what makes a rare-event benchmark usable, why realism is the harder axis, and a measured comparison with gen-fraud-graph. The harness and the raw results are in `benchmarks/`.

## Notes on network access

The `flights` fetcher downloads from BTS and OpenFlights with TLS verification enabled. If your system fails to validate the BTS certificate chain, set `GRAPHFAKER_INSECURE_TLS=1` to opt out; this logs a warning and means the downloaded data is no longer authenticated.

## Documentation

Full documentation: https://graphfaker.readthedocs.io

If you find this project useful, star the repository to support the work and help others discover it.

## License

MIT. See [LICENSE](LICENSE).

## Credits

Some of this design is borrowed, and the debts are worth naming.

- [gen-fraud-graph](https://github.com/SantanderAI/gen-fraud-graph) by the Santander AI team (Apache-2.0). The scale convention (`1.0` is about 10M accounts and 90M transactions), the idea of hardness presets, decoy patterns that are legitimate on purpose, a verification step, the three levels the evaluator scores at (accounts, transactions, patterns) and the `accounts/`, `transactions/`, `fraud/` layout that `write_gen_fraud_graph` reproduces all come from it, so that datasets from the two generators are comparable and a pipeline built on one can switch to the other. Their framing, realistic financial data with no real customer in it, is the one the fraud pack sets out to earn: the background traffic has to be realistic enough that laundering is hard to find in it. No code was copied.
- [AMLworld](https://arxiv.org/abs/2306.16424) (Altman et al., 2023). The first seven laundering typologies (fan-in, fan-out, gather-scatter, scatter-gather, cycle, stack, bipartite) follow its catalogue, which is the shared vocabulary of the AML benchmarking literature.
- [NVIDIA Data Designer](https://github.com/NVIDIA-NeMo/DataDesigner). Not a dependency, but the reference point for what a synthetic data product looks like: it generates the tables, GraphFaker generates the connections, and the latent factors here are a graph-shaped answer to the problem its column dependencies solve for rows.
- NetworkX, Polars, Apache Arrow, Faker, Pydantic, OSMnx, and the Neo4j, LadybugDB and DuckDB / DuckPGQ projects, which do the heavy lifting under every command.

The project was started from Cookiecutter's `audreyr/cookiecutter-pypackage` template.
