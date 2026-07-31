=======
History
=======

0.1.0 (2025-04-02)
------------------

* First release on PyPI.

0.2.0 (2025-06-08)
------------------
GraphFaker v0.2.0 – June 2025

This release expands GraphFaker’s scope with a new data sources to support graph construction and entity recognition tutorials:

* Wikipedia fetcher (WikiFetcher)
  - Retrieve raw page data (title, summary, content, sections, links, references) via the wikipedia package
  - Export JSON dumps of article fields

Upgrade now to effortlessly pull in unstructured Wikipedia data

0.4.0 (2026-07-31)
------------------

Graph-native entity resolution, reproducible generation, and several fixes.

New:

* ``graphfaker.export`` — export connectors that write files rather than
  requiring a database driver: ``export_csv`` (key-union headers, so
  heterogeneous node types keep their values in the right columns),
  ``export_neo4j_csv`` (``neo4j-admin`` typed headers), and ``export_cypher``
  for Neo4j, openCypher, and ISO GQL. Also exposed as ``--format`` on the CLI.
* ``graphfaker.corpus`` — paired text/entity corpora for measuring how many
  nodes a graph builder creates per real entity. Documents are clean prose, not
  corrupted text; ``Corpus.audit()`` verifies that no surface form could be
  claimed by two entities, and ``duplication_report()`` produces the counts.
* ``examples/duplication_experiment.py`` — runs that measurement across several
  graph-building frameworks and prints a comparison table.
* ``graphfaker.resolve`` — find duplicate entities using attribute similarity
  **and** neighbourhood overlap, then merge each cluster onto one canonical node
  with its edges rewired. Available as ``GraphFaker.resolve()`` or the
  standalone ``resolve_entities()`` / ``merge_clusters()``.
* ``evaluate_clusters()`` — pairwise and B-cubed precision/recall/F1 for scoring
  a predicted clustering against gold labels you supply.
* Seeding: ``GraphFaker(seed=...)``, ``generate_graph(..., seed=...)``,
  ``reseed()``, and ``--seed`` on the CLI. Identical seed and arguments now
  produce an identical graph, and seeding no longer touches global
  ``random`` state.

Fixed:

* **Security:** TLS certificate verification was disabled for all flight-data
  downloads. It is now enabled by default; opt out with
  ``GRAPHFAKER_INSECURE_TLS=1``, which warns loudly.
* **Packaging:** ``tqdm`` and ``urllib3`` were imported but never declared as
  dependencies, so a clean install could fail on ``import graphfaker``.
* The CLI logged through the standard library ``venv`` module's logger by
  accident (``from venv import logger``) instead of the package logger.
* ``generate_graph`` reported the wrong valid sources on error ("Use 'random' or
  'osm'").
* GraphML export now flattens list, tuple, set, and dict attributes instead of
  failing on them.
* Three CLI tests were failing on ``main`` because their mocks returned strings
  where a graph was required; tests also no longer write artifacts into the
  repository root.

Docs:

* README no longer advertises unimplemented features (RDF/JSON-LD export,
  Neo4j/Kuzu/TigerGraph integration, million-node scale, LLM-driven fetching).