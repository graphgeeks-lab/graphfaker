# GraphFaker

GraphFaker is a Python library for generating, and loading synthetic and real-world graph datasets. It supports `faker`  as social graph, OpenStreetMap (OSM) road networks, and real airline flight networks. Use it for data science, research, teaching, rapid prototyping, and more!

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
python -m graphfaker.cli gen \
    --source faker \
    --total-nodes 100 \
    --total-edges 500
```

#### Generate a Real-World Road Network (OSM)
```sh
python -m graphfaker.cli gen \
    --source osm \
    --place "Berlin, Germany" \
    --network-type drive
```

#### Generate a Flight Network (Airlines/Airports/Flights)
```sh
python -m graphfaker.cli gen \
    --source flights \
    --country "United States" \
    --year 2024 \
    --month 1
```

You can also use `--date-range` for custom time spans (e.g., `--date-range "2024-01-01,2024-01-15"`).

---

## Future Plans: Graph Export Formats

- **GraphML**: General graph analysis/visualization (`--export graph.graphml`)
- **JSON/JSON-LD**: Knowledge graphs/web apps (`--export data.json`)
- **CSV**: Tabular analysis/database imports (`--export edges.csv`)
- **RDF**: Semantic web/linked data (`--export graph.ttl`)

---

## Future Plans: Integration with Graph Tools

GraphFaker generates NetworkX graph objects that can be easily integrated with:
- **Graph databases**: Neo4j, Kuzu, TigerGraph
- **Analysis tools**: NetworkX, SNAP, graph-tool
- **ML frameworks**: PyTorch Geometric, DGL, TensorFlow GNN
- **Visualization**: G.V, Gephi, Cytoscape, D3.js

---

## On the Horizon:

- Handling large graph -> millions of nodes
- Using NLP/LLM to fetch graph data -> "Fetch flight data for Jan 2024"
- Connects to any graph database/engine of choice -> "Establish connections to graph database/engine of choice"


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
