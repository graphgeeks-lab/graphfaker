# GraphFaker

GraphFaker is a powerful Python library and CLI tool for generating, loading, and exporting synthetic and real-world graph datasets. It supports social/knowledge graphs, OpenStreetMap (OSM) road networks, and real airline flight networks. Use it for data science, research, teaching, rapid prototyping, and more!

*Note: The authors and graphgeeks labs do not hold any responsibility for the correctness of this generator.*

[![PyPI version](https://img.shields.io/pypi/v/graphfaker.svg)](https://pypi.python.org/pypi/graphfaker)
[![Build Status](https://img.shields.io/travis/denironyx/graphfaker.svg)](https://travis-ci.com/denironyx/graphfaker)
[![Docs Status](https://readthedocs.org/projects/graphfaker/badge/?version=latest)](https://graphfaker.readthedocs.io/en/latest/?version=latest)
[![Dependency Status](https://pyup.io/repos/github/denironyx/graphfaker/shield.svg)](https://pyup.io/repos/github/denironyx/graphfaker/)

---

## Features
- **Multiple Graph Sources:**
  - `faker`: Synthetic social/knowledge graphs with rich node/edge types
  - `osm`: Real-world road networks from OpenStreetMap
  - `flights`: Real airline, airport, and flight networks
- **Flexible Export:** GraphML, JSON, CSV, RDF, and more
- **Easy CLI & Python API**
- **Visualization**: Built-in graph visualization
- **Integration**: Works with NetworkX, Gephi, Neo4j, PyTorch Geometric, and more

---

## Installation

Install from PyPI:
```sh
uv pip install graphfaker
```

For development:
```sh
git clone https://github.com/denironyx/graphfaker.git
cd graphfaker
uv pip install -e .
```

---

## Quick Start

### CLI Usage

Show help:
```sh
python -m graphfaker.cli --help
```

#### Generate a Synthetic Social Graph
```sh
python -m graphfaker.cli gen \
    --source faker \
    --total-nodes 100 \
    --total-edges 500 \
    --visualize \
    --export out.graphml
```

#### Generate a Real-World Road Network (OSM)
```sh
python -m graphfaker.cli gen \
    --source osm \
    --place "Berlin, Germany" \
    --network-type drive \
    --export berlin.graphml
```

#### Generate a Flight Network (Airlines/Airports/Flights)
```sh
python -m graphfaker.cli gen \
    --source flights \
    --country "United States" \
    --year 2024 \
    --month 1 \
    --export flights.graphml
```

You can also use `--date-range` for custom time spans (e.g., `--date-range "2024-01-01,2024-01-15"`).

---

### Python API Usage

```python
from graphfaker import GraphFaker

gf = GraphFaker()
# Synthetic social/knowledge graph
g1 = gf.generate_graph(source="faker", total_nodes=200, total_edges=800)
# OSM road network
g2 = gf.generate_graph(source="osm", place="Berlin, Germany", network_type="drive")
# Flight network
g3 = gf.generate_graph(source="flights", country="United States", year=2024, month=1)

# Visualize and export
gf.visualize_graph(title="Synthetic Graph")
gf.export_graph("synthetic.graphml")
```

#### Advanced: Date Range for Flights
```python
g = gf.generate_graph(source="flights", country="United States", date_range=("2024-01-01", "2024-01-15"))
```

---

## Graph Export Formats

- **GraphML**: General graph analysis/visualization (`--export graph.graphml`)
- **JSON/JSON-LD**: Knowledge graphs/web apps (`--export data.json`)
- **CSV**: Tabular analysis/database imports (`--export edges.csv`)
- **RDF**: Semantic web/linked data (`--export graph.ttl`)

---

## Integration with Graph Tools

GraphFaker generates NetworkX graph objects that can be easily integrated with:
- **Graph databases**: Neo4j, Kuzu, TigerGraph
- **Analysis tools**: NetworkX, SNAP, graph-tool
- **ML frameworks**: PyTorch Geometric, DGL, TensorFlow GNN
- **Visualization**: Gephi, Cytoscape, D3.js

---

## Documentation

Full documentation: https://graphfaker.readthedocs.io

---

## License
MIT License

## Credits
Created with Cookiecutter and the `audreyr/cookiecutter-pypackage` project template.
