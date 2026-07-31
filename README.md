# graphfaker

graphfaker is a Python library for generating and loading synthetic and real-world datasets tailored for graph-based applications. It supports `faker`  as social graph, OpenStreetMap (OSM) road networks, and real airline flight networks. Use it for data science, research, teaching, rapid prototyping, and more!

*Note: The authors and graphgeeks labs do not hold any responsibility for the correctness of this generator.*

[![PyPI version](https://img.shields.io/pypi/v/graphfaker.svg)](https://pypi.python.org/pypi/graphfaker)
[![Docs Status](https://readthedocs.org/projects/graphfaker/badge/?version=latest)](https://graphfaker.readthedocs.io/en/latest/?version=latest)
[![Dependency Status](https://pyup.io/repos/github/denironyx/graphfaker/shield.svg)](https://pyup.io/repos/github/denironyx/graphfaker/)
[![image](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

Join our Discord server 👇

[![](https://dcbadge.limes.pink/api/server/https://discord.gg/mQQz9bRRpH)](https://discord.gg/mQQz9bRRpH)


### Problem Statement
Graph data is essential for solving complex problems in various fields, including social network analysis, transportation modeling, recommendation systems, and fraud detection. However, many professionals, researchers, and students face a common challenge: a lack of easily accessible, realistic graph datasets for testing, learning, and benchmarking. Real-world graph data is often restricted due to privacy concerns, complexity, or large size, making experimentation difficult.

### Solution: graphfaker
GraphFaker is an open-source Python library designed to generate, load, and export synthetic graph datasets in a user-friendly and configurable way. It enables users to generate graph tailored to their specific needs, allowing for better experimentation and learning without needing to think about where the data is coming from or how to fetch the data.

## Features
- **Multiple Graph Sources:**
  - `faker`: Synthetic “social-knowledge” graphs powered by Faker (people, places, organizations, events, products with rich attributes and relationships)
  - `osm`: Real-world street networks directly from OpenStreetMap (by place name, address, or bounding box)
  - `flights`: Flight/airline networks from Bureau of Transportation Statistics (airlines ↔ airports ↔ flight legs, complete with cancellation and delay flags)
- **Unstructured Data Source:**
  - `WikiFetcher`: Raw Wikipedia page data (title, summary, content, sections, links, references) ready for custom graph or RAG pipelines
- **Entity Resolution:**
  - `resolve()`: find and merge duplicate nodes using attribute similarity **and** neighbourhood overlap — the graph signal tabular record-linkage tools cannot see
  - `evaluate_clusters()`: score a predicted clustering against gold labels you supply (pairwise and B-cubed)
- **Export Connectors:**
  - CSV, `neo4j-admin` bulk-import CSV, Cypher, openCypher, and ISO GQL — file-based, so no driver or running database is needed
- **Measurement:**
  - `generate_corpus()`: documents whose entities are known in advance, for counting how many nodes a graph builder creates per real entity
- **Reproducible:** every synthetic graph is seedable
- **Easy CLI & Python Library**

This removes friction around data acquisition, letting you focus on algorithms, teaching or rapid prototyping.

## ✨ Key Features

| Source        | What It Gives You                                                                                                                                                                                     |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Faker**     | Synthetic social-knowledge graphs with configurable sizes, weighted and directional relationships.                                                      |
| **OSM**       | Real road or walking networks via OSMnx under the hood—fetch by place, address, or bounding box; simplify topology; project to UTM.                                                |
| **Flights**   | Airline/airport graph from BTS on-time performance data: nodes for carriers, airports, flights; edges for OPERATED\_BY, DEPARTS\_FROM, ARRIVES\_AT; batch or date-range support; subgraph sampling.   |
| **WikiFetcher** | Raw page dumps (title, summary, content, sections, links, references) as JSON |


---

*Disclaimer: This is still a work in progress (WIP). With logging and debugging print statement. Our goal for releasing early is to get feedback and reiterate.*

## Installation

Install from PyPI:
```sh
uv pip install graphfaker
```

For development:
```sh
git clone https://github.com/graphgeeks-lab/graphfaker.git
cd graphfaker
uv pip install -e .
```

---

## Quick Start

---

### Python Library Usage

```python
from graphfaker import GraphFaker

gf = GraphFaker()
# Synthetic social/knowledge graph
g1 = gf.generate_graph(source="faker", total_nodes=200, total_edges=800)
# OSM road network
g2 = gf.generate_graph(source="osm", place="Chinatown, San Francisco, California", network_type="drive")
# Flight network
g3 = gf.generate_graph(source="flights", year=2024, month=1)

# Fetch Wikipedia page data
from graphfaker import WikiFetcher
page = WikiFetcher.fetch_page("Graph theory")
print(page['summary'])
print(page['content'])
WikiFetcher.export_page_json(page, "graph_theory.json")

```

#### Advanced: Date Range for Flights

Note this isn't recommended and it's still being tested. We are working on ways to make this faster.

```python
g = gf.generate_graph(source="flights", date_range=("2024-01-01", "2024-01-15"))
```


### CLI Usage (WIP)

Show help:
```sh
python -m graphfaker.cli --help
```

#### Generate a Synthetic Social Graph
```sh
python -m graphfaker.cli  \
    --fetcher faker \
    --total-nodes 100 \
    --total-edges 500
```

#### Generate a Real-World Road Network (OSM)
```sh
python -m graphfaker.cli  \
    --fetcher osm \
    --place "Berlin, Germany" \
    --network-type drive
```

#### Generate a Flight Network (Airlines/Airports/Flights)
```sh
python -m graphfaker.cli \
    --fetcher flights \
    --country "United States" \
    --year 2024 \
    --month 1
```

You can also use `--date-range` for custom time spans (e.g., `--date-range "2024-01-01,2024-01-15"`).

---

## Entity Resolution

LLM-built knowledge graphs routinely emit the same real-world entity as several
nodes, and every edge attached to a false node is a false edge. Tabular
record-linkage tools compare *rows*, so they cannot use the strongest signal a
graph offers: **two nodes that share most of their neighbours are probably the
same entity, however differently their names are spelled.**

`resolve()` scores candidate pairs on attribute similarity *and* neighbourhood
overlap, clusters the survivors, and merges each cluster onto one canonical node
— rewiring its edges, dropping self-loops the merge creates, and recording what
was absorbed.

```python
from graphfaker import GraphFaker

gf = GraphFaker(seed=42)
gf.generate_graph(source="faker", total_nodes=500, total_edges=2000)

result = gf.resolve(on=["name", "email"], threshold=0.85)
print(result.report())
#   candidate pairs scored : 1284
#   pairs above threshold  : 12
#   clusters found         : 5
#   duplicate nodes        : 7

clean = result.apply()   # merged copy; the original is untouched
```

`structural_weight` controls how much shared-neighbour evidence may lift a
pair's score. Structure can only *raise* a score, never lower it, so isolated
nodes are never penalised for having few neighbours — set it to `0` to fall back
to plain attribute matching:

```python
gf.resolve(on=["name"], structural_weight=0.0)   # attributes only
gf.resolve(on=["name"], structural_weight=0.8)   # trust the graph structure
```

Already have labelled clusters? Score a prediction against them. This computes
metrics only — it does not manufacture ground truth:

```python
from graphfaker import evaluate_clusters

scores = evaluate_clusters(result.clusters, my_known_duplicates)
print(scores["pairwise_f1"], scores["b_cubed_f1"])
```

---

## Reproducibility

Synthetic generation is seedable, per instance. The same seed and the same
arguments always produce an identical graph, and seeding does not disturb the
global `random` module:

```python
GraphFaker(seed=42).generate_graph(source="faker", total_nodes=100)
# or per call:
gf.generate_graph(source="faker", total_nodes=100, seed=42)
```

```sh
python -m graphfaker.cli --fetcher faker --total-nodes 100 --seed 42
```

---

## Getting the graph into a database

Rather than shipping a driver per database — each needing credentials, a version
matrix, and a live service to test against — GraphFaker writes files that every
engine's own loader already understands.

```python
from graphfaker.export import export_csv, export_neo4j_csv, export_cypher

export_csv(G, "nodes.csv", "edges.csv")      # pandas, Gephi, any bulk loader
export_neo4j_csv(G, "import/")               # neo4j-admin bulk import headers
export_cypher(G, "load.cypher")              # Neo4j, Memgraph, Kuzu
export_cypher(G, "load.gql", dialect="gql")  # ISO GQL
```

Or from the CLI:

```sh
python -m graphfaker.cli --fetcher faker --total-nodes 500 --format cypher --export load.cypher
python -m graphfaker.cli --fetcher faker --total-nodes 500 --format neo4j-csv --export import/
```

| Format | Loads into |
| --- | --- |
| `graphml` | Gephi, Cytoscape, NetworkX, igraph |
| `csv` | pandas, TigerGraph `LOAD`, Amazon Neptune bulk loader, Spark/GraphFrames |
| `neo4j-csv` | `neo4j-admin database import` — the fast path for large graphs |
| `cypher` | Neo4j, Memgraph, Kuzu |
| `opencypher` | Amazon Neptune |
| `gql` | ISO GQL engines (`INSERT` in place of `CREATE`) |

Node labels come from the `type` attribute and relationship types from
`relationship`, both configurable. Nodes of different types carry different
attributes, so CSV headers are the **union** of all keys seen — a node missing a
column gets an empty cell rather than having its values shifted into the wrong
one. Container values (coordinate tuples, merge provenance) are flattened, and
labels containing punctuation are sanitised.

Once loaded, Neo4j Graph Data Science works directly on the result:

```cypher
CALL gds.graph.project('g', '*', '*');
CALL gds.pageRank.stream('g') YIELD nodeId, score
RETURN gds.util.asNode(nodeId).name AS name, score ORDER BY score DESC LIMIT 10;
```

---

## Measuring entity duplication

`graphfaker.corpus` builds documents whose entities are known in advance, so you
can count how many nodes a graph builder creates for entities that are singular.

```python
from graphfaker.corpus import generate_corpus, duplication_report

corpus = generate_corpus(seed=42, n_entities=60, n_documents=80)
assert corpus.audit()["clean"]      # no surface form belongs to two entities
corpus.write("corpus/")             # documents + gold.json

# ...run any graph builder over corpus/, then:
report = duplication_report(extracted_graph, corpus, framework="my-pipeline")
print(report.summary())
```

Nothing is corrupted. The text is clean, well-formed English and every entity is
unambiguous to a human reader, so a correct pipeline scores zero. Entities are
referred to by the surface forms a normal writer uses — full name, surname
alone, an accepted abbreviation — which is ordinary prose, not injected noise.

That restraint is deliberate. Synthetic *corruption* is far easier than
real-world error (Lam et al., IJPDS 2024, measured roughly a hundredfold gap),
so a benchmark built on guessed error rates mostly measures its own noise model.
Counting splits of entities a human would never split is a weaker claim, and one
a generator can actually support.

`examples/duplication_experiment.py` runs this across several frameworks and
prints a comparison table. It refuses to run on an ambiguous corpus, includes a
perfect-extractor control that must score zero, and names any framework it
skipped rather than omitting it silently.

---

## Scope

Anything not documented above is not implemented. Please open an issue if you
need something specific rather than assuming it is on the way.

---

## Notes on network access

The `flights` fetcher downloads from BTS and OpenFlights with TLS verification
enabled. Some systems fail to validate the BTS certificate chain; if you hit
that, you can opt out with `GRAPHFAKER_INSECURE_TLS=1`, which logs a warning and
means the downloaded data is no longer authenticated. Verification is never
disabled silently.

---

## Documentation

Full documentation: https://graphfaker.readthedocs.io

---
⭐ Star the Repo

If you find this project valuable, star ⭐ this repository to support the work and help others discover it!

---

## License
MIT License

## Credits
Created with Cookiecutter and the `audreyr/cookiecutter-pypackage` project template.
