# GraphFaker

GraphFaker is a Python library for generating, loading, and exporting synthetic graph datasets. This guide will help you get started, generate different types of graphs, and integrate with popular graph tools.

*Note: The authors or graphgeeks labs do not hold any responsibility for the correctness of this generator.*

[![PyPI version](https://img.shields.io/pypi/v/graphfaker.svg)](https://pypi.python.org/pypi/graphfaker)
[![Build Status](https://img.shields.io/travis/denironyx/graphfaker.svg)](https://travis-ci.com/denironyx/graphfaker)
[![Docs Status](https://readthedocs.org/projects/graphfaker/badge/?version=latest)](https://graphfaker.readthedocs.io/en/latest/?version=latest)
[![Dependency Status](https://pyup.io/repos/github/denironyx/graphfaker/shield.svg)](https://pyup.io/repos/github/denironyx/graphfaker/)

## Why Use GraphFaker?

GraphFaker solves several important challenges for data scientists, researchers, and developers working with graph data:

- **No need for sensitive or proprietary data**: Generate realistic graph structures without privacy concerns
- **Rapid prototyping and testing**: Quickly create test datasets of varying sizes and properties
- **Research, teaching, and development**: Perfect for academic settings, tutorials, and building graph applications
- **Customizable graph generation**: Create specific graph structures tailored to your needs


## Installation

Install GraphFaker from PyPI:

```sh
uv pip install graphfaker
```

For development:

```sh
git clone https://github.com/denironyx/graphfaker.git
cd graphfaker
uv pip install -e .
```

## Quick Start

### Command Line Interface (CLI)

Show CLI help:
```sh
python -m graphfaker.cli --help
```

Generate a synthetic graph:
```sh
python -m graphfaker.cli gen \
    --mode synthetic \
    --total-nodes 100 \
    --total-edges 500 \
    --visualize \
    --export out.graphml
```

Or using the bash entrypoint:
```sh
graphfaker gen --mode synthetic --total-nodes 100 --total-edges 500 --visualize --export out.graphml
```

Generate a knowledge graph:
```sh
python -m graphfaker.cli gen --mode knowledge --schema people_orgs --total-nodes 50 --export kg.jsonld
```

Load a prebuilt benchmark graph:
```sh
python -m graphfaker.cli load --dataset karate_club --export karate.graphml
```

### Python API

Use GraphFaker directly in your Python code:
```python
from graphfaker import generate
G = generate(mode='synthetic', total_nodes=100, total_edges=500)
# G is a NetworkX graph object
```

## Graph Export Formats

GraphFaker supports exporting to various formats for different use cases:

- **GraphML**: For general graph analysis and visualization (`--export graph.graphml`) 
- **JSON/JSON-LD**: For knowledge graphs and web applications (`--export data.json`)
- **CSV**: For tabular analysis and database imports (`--export edges.csv`)
- **RDF**: For semantic web and linked data applications (`--export graph.ttl`)

Example:
```sh
python -m graphfaker.cli gen --mode synthetic --export out.csv
```

## Integration with Graph Tools

GraphFaker generates NetworkX graph objects that can be easily integrated with:

- **Graph databases**: Neo4j, Kuzu, TigerGraph
- **Analysis tools**: NetworkX, SNAP, graph-tool
- **ML frameworks**: PyTorch Geometric, DGL, TensorFlow GNN
- **Visualization**: Gephi, Cytoscape, D3.js

## Documentation

Full documentation is available at: https://graphfaker.readthedocs.io

## License

MIT License

## Credits

Created with Cookiecutter and the `audreyr/cookiecutter-pypackage` project template.

---