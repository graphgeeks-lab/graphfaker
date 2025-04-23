==========
graphfaker
==========


.. image:: https://img.shields.io/pypi/v/graphfaker.svg
        :target: https://pypi.python.org/pypi/graphfaker

.. image:: https://img.shields.io/travis/denironyx/graphfaker.svg
        :target: https://travis-ci.com/denironyx/graphfaker

.. image:: https://readthedocs.org/projects/graphfaker/badge/?version=latest
        :target: https://graphfaker.readthedocs.io/en/latest/?version=latest
        :alt: Documentation Status


.. image:: https://pyup.io/repos/github/denironyx/graphfaker/shield.svg
     :target: https://pyup.io/repos/github/denironyx/graphfaker/
     :alt: Updates


Note: The authors or graphgeeks labs do not hold any responsibility for the correctness of this generator.

an open-source python library designed to generate, load, and export synthetic graph datasets in a user-friendly and configurable way. It enables users to create realistic yet customizable graph structures tailored to their specific needs, allowing for better experimentation and learning without relying on sensitive or proprietary data.

Synthetic Graph Generation
  -  Create graphs for social networks, transportation systems, knowledge graphs, and more.
  -  Configurable number of nodes, edges, and relationships.
  -  Support for weighted, directed, and attributed graphs.

Prebuilt Benchmark Graphs
  -  Load small, structured datasets for graph learning and algorithm testing.
  -  Support for loading into NetworkX, Pandas, Kuzu, and Neo4j.
  -  Export to formats like CSV, JSON, GraphML, and RDF.

Knowledge Graph Creation
  -  Generate knowledge graphs with predefined schemas (people, organizations, locations, etc.).
  -  Randomized entity and relationship generation.
  -  Output in JSON-LD, RDF, or Neo4j formats.

Impact

GraphFaker simplifies graph data experimentation by providing an accessible, open-source solution for professionals and students alike. It helps researchers test algorithms, developers prototype applications, and educators teach graph concepts without dealing with data access barriers.


## How to get Started


```sh
#Show help
python -m graphfaker.cli --help
```

using python to generate graph
```
# Generate a 100-node, 500-edge synthetic graph, visualize it, and export to `out.graphml`
python -m graphfaker.cli gen \
    --mode synthetic \
    --total-nodes 100 \
    --total-edges 500 \
    --visualize \
    --export out.graphml
```

doing the same with bash.
```
graphfaker gen --mode synthetic --total-nodes 100 --total-edges 500 --visualize --export out.graphml

```


* Free software: MIT license
* Documentation: https://graphfaker.readthedocs.io.


Features
--------

* TODO

Credits
-------

This package was created with Cookiecutter_ and the `audreyr/cookiecutter-pypackage`_ project template.

.. _Cookiecutter: https://github.com/audreyr/cookiecutter
.. _`audreyr/cookiecutter-pypackage`: https://github.com/audreyr/cookiecutter-pypackage
