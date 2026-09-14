# graphfaker

GraphFaker generates synthetic graph datasets that behave like real ones, and loads a few real ones. You declare what a graph should look like, or pick a ready-made domain such as a bank with laundering patterns, and get tables you can put into Neo4j, LadybugDB, Parquet or NetworkX, with the ground truth attached.

[![PyPI version](https://img.shields.io/pypi/v/graphfaker.svg)](https://pypi.python.org/pypi/graphfaker)
[![Docs Status](https://readthedocs.org/projects/graphfaker/badge/?version=latest)](https://graphfaker.readthedocs.io/en/latest/?version=latest)
[![image](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Join our Discord server: [![](https://dcbadge.limes.pink/api/server/https://discord.gg/mQQz9bRRpH)](https://discord.gg/mQQz9bRRpH)

*The authors and GraphGeeks Lab do not hold any responsibility for the correctness of this generator.*

## Why

Graph data is hard to get. The graphs people most want to test against, such as who pays whom, who knows whom, or who shares a device with whom, are the ones that cannot be shared. Existing generators either produce structure with no attributes, or attributes with no structure, and almost none of them tell you what they planted.

GraphFaker makes graphs where attributes and structure agree, where events happen over time, and where every injected pattern is recorded. That makes the output usable for three things: developing and demonstrating graph applications, benchmarking graph databases and graph algorithms, and training and evaluating detectors (fraud models, entity resolution, GraphRAG builders) against a known answer.

## Install

```sh
pip install graphfaker
pip install "graphfaker[examples]"    # adds matplotlib, kuzu and jupyter for the notebooks
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

Load it into an embedded graph database and ask it a question in Cypher:

```sh
pip install kuzu
graphfaker generate fraud --scale 0.01 --seed 42 --out ./bank --sink ladybug
```

```python
import kuzu
conn = kuzu.Connection(kuzu.Database("bank/graph.lbdb"))
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
| know which generation methods exist and which GraphFaker uses | [docs/methods.md](docs/methods.md) |
| add your own domain (supply chain, claims, telecom, ...) | [docs/adding-a-domain.md](docs/adding-a-domain.md) |
| see the plan and the reasoning behind it | [docs/design/synthetic-at-scale.md](docs/design/synthetic-at-scale.md) |

## What you can generate

| domain | what it is | how |
|---|---|---|
| `social` | people, places, organizations, events and products; heavy-tailed degrees, clustering, communities, attributes that agree with structure | `GraphFaker.generate_graph(source="faker")` or `graphfaker generate social` |
| `fraud` | a bank: customers, accounts, merchants, devices, counterparties; a realistic transaction process; eleven labelled laundering typologies with decoys and a measured hardness | `graphfaker fraud` or `graphfaker generate fraud` |
| your own | a `GraphSchema`, or a process with injected patterns | [docs/adding-a-domain.md](docs/adding-a-domain.md) |

Real-world sources, loaded rather than generated:

| source | what it gives you |
|---|---|
| `osm` | road, walking or cycling networks from OpenStreetMap, by place name, address or bounding box |
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
| `write_ladybug` | DDL and `COPY FROM` Parquet; loads the database when `kuzu` or `ladybug` is installed | an embedded graph database, one file, Cypher, no server |
| `write_neo4j_admin` | typed CSVs and the `neo4j-admin database import` command | the fast path into Neo4j |
| `write_gen_fraud_graph` | gen-fraud-graph's `accounts/`, `transactions/`, `fraud/` layout | pipelines built on that generator |
| `export_csv`, `export_neo4j_csv`, `export_cypher` | from a NetworkX graph: CSV, neo4j-admin CSV, Cypher, openCypher, ISO GQL | Memgraph, Neptune, TigerGraph, any bulk loader |
| `export_graph` | GraphML | Gephi, Cytoscape, igraph |

```python
from graphfaker import GraphTables, Manifest
from graphfaker.sinks import write_ladybug, write_neo4j_admin

tables = GraphTables.read_parquet("bank")
write_neo4j_admin(tables, "bank/neo4j")
write_ladybug(tables, "bank", db_path="bank/graph.lbdb")
```

On the command line, `--sink parquet|neo4j-admin|ladybug|gen-fraud-graph` on `graphfaker fraud` and `graphfaker generate`.

## Reproducibility

A run is a function of the schema (or domain options), the seed and the shard size. The number of workers, the machine and the process hash seed do not change the bytes. The same seed gives the same graph in any process.

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

Both commands write `nodes/`, `edges/`, `truth/`, `schema.yaml` and `manifest.json` under `--out`. Options shared by every domain:

| option | meaning |
|---|---|
| `--out DIR` | where to write (default `graphfaker_out`) |
| `--seed N` | reproducible output; the same seed gives the same bytes on any machine |
| `--workers N` | processes for node sampling; faster, does not change the result |
| `--sink parquet\|neo4j-admin\|ladybug\|gen-fraud-graph` | also write a database loader layout (Parquet is always written) |

Load the bank straight into an embedded graph database, or produce Neo4j import files:

```sh
graphfaker generate fraud --scale 0.01 --seed 42 --out ./bank --sink ladybug        # ./bank/graph.lbdb, query it in Cypher
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

The real-world networks are loaded with `graphfaker gen` and exported to a single file (`--format graphml|csv|neo4j-csv|cypher|opencypher|gql`):

```sh
graphfaker gen --fetcher osm --place "Berlin, Germany" --network-type drive --export berlin.graphml
graphfaker gen --fetcher flights --country "United States" --year 2024 --month 1 --export flights.graphml
graphfaker gen --fetcher faker --total-nodes 500 --total-edges 2500 --format cypher --export social.cypher
```

`graphfaker --help` and `graphfaker <command> --help` list every option.

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

At `scale=0.01` (100K accounts, 900K transactions) the transaction process takes about two seconds; the run takes about 70 seconds single-process and about 45 seconds with `workers=12` on a six-core laptop, because Faker attribute generation costs about a millisecond per customer. The social topology model is sequential and suits graphs up to about a million edges. Full-scale fraud runs (`scale=1.0`) need a vectorised person sampler and chunked writes, both planned. Balances are not tracked as a running ledger. See the design document for the roadmap.

## Notes on network access

The `flights` fetcher downloads from BTS and OpenFlights with TLS verification enabled. If your system fails to validate the BTS certificate chain, set `GRAPHFAKER_INSECURE_TLS=1` to opt out; this logs a warning and means the downloaded data is no longer authenticated.

## Documentation

Full documentation: https://graphfaker.readthedocs.io

If you find this project useful, star the repository to support the work and help others discover it.

## License

MIT. See [LICENSE](LICENSE).

## Credits

Created with Cookiecutter and the `audreyr/cookiecutter-pypackage` project template.
