=======
History
=======

0.5.0 (2026-09-15)
------------------

GraphFaker becomes a generator of synthetic graph data that behaves like the
real thing: you describe the graph you need, or pick a ready-made domain, and
get entities, relationships and events whose structure, attributes and timing
agree, with the ground truth included. Tabular generators produce rows;
GraphFaker generates the connections. This release adds schema-driven
generation, the fraud domain pack, database sinks, a modular domain registry,
and realistic graph topology. The plan is in ``docs/design/synthetic-at-scale.md``.

Positioning and documentation:

* README, package description, package docstring and the docs index all carry
  the same statement of what GraphFaker is and who it is for.
* New guides: ``docs/how-it-works.md`` (schema to tables), ``docs/fraud-generation.md``
  (every step of the bank and its typologies), ``docs/methods.md`` (families of
  synthetic graph generation and which GraphFaker uses), ``docs/adding-a-domain.md``.
* ``graphfaker.domains.registry``: every domain is a ``Domain`` with a name, an
  options model and a ``generate`` function; third-party domains register through
  the ``graphfaker.domains`` entry-point group. ``graphfaker domains`` lists them,
  ``graphfaker generate <domain> --option value`` runs any of them.
* The README explains what each option means (``--seed``, ``--scale``,
  ``--hardness`` and the rest) and when to change it.

* ``graphfaker.schema``: declarative ``GraphSchema``: node types with
  attribute samplers, latent factors with per-group parameters, edge families,
  topology models. Validated with pydantic; round-trips through YAML/JSON;
  content-hashed.
* ``graphfaker.engine``: ``generate(schema, seed, shard_size)`` → ``GraphRun``
  with columnar tables (polars), ground truth, and a manifest. Reproducible
  across processes; node generation is sharded with independent seeded streams.
* ``graphfaker.backends.GraphTables``: node table per type, edge table per
  relationship; NetworkX view; Parquet read/write.
* ``graphfaker.domains.social``: the built-in social graph re-expressed as a
  schema. Same realism metrics as 0.5; every former magic number is now a
  schema field.
* ``GraphFaker.generate(schema)`` alongside the unchanged ``generate_graph``.
* ``graphfaker.domains.fraud``: the fraud / AML domain pack: customers,
  accounts, merchants, devices, counterparties; a vectorised transaction
  process with recurring flows, repeat partners, merchant popularity,
  seasonality and income scaling; eleven labelled typologies with decoys;
  ``hardness_report`` (single-feature AUCs), ``realism_report`` and an
  ``evaluate`` harness compatible with gen-fraud-graph's.
* ``graphfaker.sinks``: Neo4j admin-import files, LadybugDB/Kùzu (DDL +
  ``COPY`` from Parquet, loads when a driver is installed), and a
  gen-fraud-graph compatible layout.
* CLI is now multi-command: ``graphfaker gen`` (the previous behaviour),
  ``graphfaker fraud``, ``graphfaker evaluate``; ``graphfaker`` console script.
* ``workers`` parallelises node sampling across processes without changing
  the result. osmnx is imported on first use, halving import time.
* ``docs/notebooks/graphfaker_tour.ipynb`` (executed, with charts) and
  ``examples/fraud_tour.py``: schemas, the fraud pack, exploration, ground
  truth, hardness, detectors, read/write/query. ``graphfaker[examples]`` extra.
* Place nodes carry ``latitude`` / ``longitude`` columns instead of a
  ``coordinates`` tuple.
* New dependencies: pydantic, polars, pyarrow, numpy, pyyaml.
* Removed the superseded proposal documents.

Realistic graph topology (2026-08-01):

Until now both endpoints of every edge were drawn uniformly at random, so the
synthetic generator produced an Erdos-Renyi graph: Poisson degree distribution,
no hubs, effectively no clustering, no community structure, and attributes
statistically independent of the topology. It was labelled "realistic" but was
not, and anything measured against it was measured against noise.

Edges are now formed by preferential attachment, triadic closure, and homophily
over latent communities. Measured on 600 nodes / 2400 edges, seed 1:

===========================  ===========  =================
metric                       realistic    uniform (pre-0.5)
===========================  ===========  =================
degree Fano factor           7.9          1.7
max degree                   88           19
degree Gini                  0.45         0.26
average clustering           0.180        0.013
community modularity         0.72         -0.004
age homophily                0.81         0.01
isolated nodes               0            4
===========================  ===========  =================

* ``graphfaker.metrics``: ``graph_stats()`` and ``compare_topology()``, so the
  claim is checkable rather than asserted. The tests are differential: each
  property is compared against ``topology="uniform"``, which reproduces the old
  behaviour and is retained solely as a baseline.
* Every node carries a latent ``community``; attributes are drawn from
  community-specific distributions, which is what makes homophily possible.
* Places, organizations, events, and products carry a heavy-tailed ``prominence``
  driving popularity.
* ``population`` tracks connectivity and ``employee_count`` now correlates 0.95
  with the ``WORKS_AT`` edges actually present, instead of contradicting them.
* ``industry`` holds an industry rather than ``fake.job()``'s job titles.
* ``LIVES_IN``, ``BORN_IN``, and ``HEADQUARTERED_IN`` are singular. A person
  could previously live in four cities.
* ``total_edges`` now means what ``number_of_edges()`` reports. Counting loop
  iterations overshot by ~14% when bidirectional friendships dominated and
  undershot by ~12% when skipped functional relationships did.
* No isolated nodes. Preferential attachment alone left 24% of a 600-node graph
  unreachable, so coverage and top-up passes guarantee a giant component.

Fixed:

* GraphML export raised on ``None`` attribute values, which broke export for any
  graph small enough to leave a community without a place.

Note for anyone comparing results across versions: entity resolution is
measurably harder on a realistic graph. The same injected duplicates give
``resolve()`` precision 1.000 on a uniform graph and 0.88 on a realistic one,
because homophily means distinct people share attributes and neighbours. Numbers
produced before 0.5 were flattered by the generator.

0.4.0 (2026-07-31)
------------------

Graph-native entity resolution, reproducible generation, and several fixes.

New:

* ``graphfaker.export``: export connectors that write files rather than
  requiring a database driver: ``export_csv`` (key-union headers, so
  heterogeneous node types keep their values in the right columns),
  ``export_neo4j_csv`` (``neo4j-admin`` typed headers), and ``export_cypher``
  for Neo4j, openCypher, and ISO GQL. Also exposed as ``--format`` on the CLI.
* ``graphfaker.corpus``: paired text/entity corpora for measuring how many
  nodes a graph builder creates per real entity. Documents are clean prose, not
  corrupted text; ``Corpus.audit()`` verifies that no surface form could be
  claimed by two entities, and ``duplication_report()`` produces the counts.
* ``examples/duplication_experiment.py``: runs that measurement across several
  graph-building frameworks and prints a comparison table. The Cognee adapter is
  written against the API verified in cognee 1.4.1 (``add``/``cognify``/``export``
  are coroutines; ``export`` accepts ``format="graphml"``; there is no
  ``get_graph_data``). It writes to a run-specific dataset instead of calling
  ``cognee.prune``, so an existing local store is not destroyed.
* ``docs/notebooks/duplication_experiment.ipynb``: the same experiment end to
  end, including repairing the graph with ``resolve()``, a threshold sweep
  showing the precision/recall tradeoff, and export of the cleaned graph. Runs
  without an LLM key using a labelled simulated extraction.
* ``resolve_entities(token_subset_floor=...)``: floors the attribute score when
  one name's tokens are contained in the other's ("Hill" inside "Allison Hill").
  Character ratios score that pair near 0.5, so it was previously discarded
  before neighbourhood overlap was consulted. The floor sits below the default
  threshold deliberately, so containment alone never merges anything.
* ``graphfaker.resolve``: find duplicate entities using attribute similarity
  **and** neighbourhood overlap, then merge each cluster onto one canonical node
  with its edges rewired. Available as ``GraphFaker.resolve()`` or the
  standalone ``resolve_entities()`` / ``merge_clusters()``.
* ``evaluate_clusters()``: pairwise and B-cubed precision/recall/F1 for scoring
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

0.2.0 (2025-06-08)
------------------
GraphFaker v0.2.0 – June 2025

This release expands GraphFaker’s scope with a new data sources to support graph construction and entity recognition tutorials:

* Wikipedia fetcher (WikiFetcher)
  - Retrieve raw page data (title, summary, content, sections, links, references) via the wikipedia package
  - Export JSON dumps of article fields

Upgrade now to effortlessly pull in unstructured Wikipedia data

0.1.0 (2025-04-02)
------------------

* First release on PyPI.

