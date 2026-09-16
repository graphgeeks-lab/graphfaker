=======
History
=======

0.6.0 (2026-09-16)
------------------

Every database we said the data would land in now takes it, verified; the same dataset trains a GNN; and a full-size bank (10M accounts, 90M transactions) generates in minutes rather than hours.

Databases:

* ``graphfaker load neo4j <dir>``: loads a dataset into a **running** Neo4j over Bolt with batched ``UNWIND`` writes: no stopped database, no staging files in the server's import directory, no ``neo4j-admin`` on the PATH, and it works against Aura. Roughly 16k rows/s, so about 80 seconds for the 1.34M rows of ``--scale 0.01``. The offline ``--sink neo4j-admin`` path is still there and is still the right answer above a few tens of millions of rows; ``docs/neo4j.md`` has the comparison.
* Ground truth is loaded as a subgraph rather than flattened onto nodes: ``(:Pattern)`` nodes, ``(:Account)-[:IN_PATTERN {role}]->(:Pattern)`` memberships, ``is_fraud``/``pattern_id``/``typology`` on the money relationship itself, and ``(:Region)`` nodes carrying each latent factor's generation parameters. Membership has to be a relationship because an account can belong to several patterns: 205 memberships across 181 accounts at ``--scale 0.01``.
* ``--blind`` loads the graph with none of that, so the same dataset can still be used as an unbiased benchmark. The usual arrangement is a blind database for whoever builds the detector and a truth-loaded one for whoever scores it.
* ``graphfaker verify neo4j <dir>``: treats the Parquet as the oracle and Neo4j as the thing under test, and exits non-zero when they disagree. Five families of check: per-label and per-type counts, constraints and endpoint labels and key uniqueness, a per-property aggregate scan (presence, sum, min, max) that catches coercion and truncation where counts cannot, sampled row-by-row round trips, and truth coverage. 154 checks on the ``--scale 0.01`` bank. ``load`` runs it automatically; ``--no-verify`` opts out.
* The verifier is tested against real breakage: the suite loads a dataset, injects one silent corruption at a time (a deleted relationship, a removed property, an altered amount, a relabelled node, unflagged truth) and asserts the specific check that must catch it. Integration tests are marked ``neo4j``, run in their own ``graphfaker-pytest`` database, and skip when no server is reachable.
* ``--sink neo4j`` on ``graphfaker fraud`` and ``graphfaker generate`` generates and loads in one step, configured from ``NEO4J_URI`` / ``NEO4J_USER`` / ``NEO4J_PASSWORD`` / ``NEO4J_DATABASE``.
* Database names are validated before the round trip: Neo4j allows no underscores, which is the first thing anyone types after naming an output directory ``fraud_data``.
* ``docs/neo4j.md``: the walkthrough, what the checks catch, and a Cypher cookbook for the laundering typologies in which every rule is **scored against the ground truth**. The measured result is that single-signal structural rules do badly. A fan-in rule with no time window runs at 0.3% precision, and a cycle detector catches every decoy ring and fewer than half the fraud rings. Adding one behavioural signal takes precision to 66.7%. ``examples/neo4j_detectors.py`` regenerates that table.
* New optional dependency group: ``pip install 'graphfaker[neo4j]'``.
* Fixed: relationship types with no attributes (most of the social graph) could not be loaded, because an empty polars struct is not constructible.
* ``graphfaker load ladybug <dir>`` and ``graphfaker verify ladybug <dir>`` bring the embedded LadybugDB / Kùzu sink to parity with Neo4j: the ground truth is loaded as a subgraph (``Pattern`` nodes, ``IN_PATTERN`` memberships with roles, ``is_fraud`` on the money relationships, latent-factor nodes) using the database's own ``LOAD FROM`` Parquet scan, ``--blind`` leaves it out entirely, and a dataset already on disk can be loaded without regenerating it. Closes issue #40.
* ``graphfaker.sinks.verify`` holds the load checks once, against ``GraphTables``, behind a small per-database adapter; Neo4j and LadybugDB share them, and the corruption tests run against both. A third sink gets verification for the price of the adapter.
* ``--blind`` on ``graphfaker generate`` and ``graphfaker fraud`` for the ``neo4j`` and ``ladybug`` sinks.
* A PyTorch Geometric export, ``graphfaker/sinks/pyg.py``: ``to_hetero_data``, ``write_pyg``, ``from_directory`` and ``--sink pyg``. Features are encoded per table (standardised numerics, 0/1 booleans, days for dates, one-hot for repeating categories; identifiers, foreign keys and latent factors kept out of ``x``), the truth becomes ``y`` and ``decoy`` on accounts and ``y`` plus ``edge_time`` on transactions, and stratified train/val/test masks are drawn from a seed. ``examples/pyg_baseline.py`` and ``docs/pyg.md`` show what the graph is worth to a detector: a GraphSAGE goes from AUC 0.99 to 0.85 to 0.56 across low, medium and high hardness while account features alone stay at chance.
* A DuckDB sink, ``graphfaker/sinks/duckdb.py``: ``--sink duckdb``, ``graphfaker load duckdb`` and ``graphfaker verify duckdb``. The dataset's tables land as DuckDB tables (DuckDB reads the Parquet itself), the ground truth the same way as in LadybugDB, and one ``CREATE PROPERTY GRAPH`` declares the graph for SQL/PGQ pattern queries through the DuckPGQ community extension. ``load.sql`` records the whole load. Loads scale 0.01 in 5 s and passes the same 154 checks. The ``duckdb`` extra pins the DuckDB release the extension is built for.
* The verifier's questions moved behind the ``Backend`` protocol: ``CypherBackend`` asks them in Cypher (Neo4j and LadybugDB inherit it), ``DuckDBBackend`` in SQL. The checks themselves are unchanged.
* The LadybugDB driver is now ``ladybug`` (LadybugDB's own package): the ``examples`` extra installs it, the docs and the tour notebook import it, and ``kuzu`` still works as a fallback because the API is the same. The driver probe forces the native library to load, so a wheel that cannot load (the 0.20 Windows wheel looks for OpenSSL 3 DLLs it does not ship) is reported and skipped instead of failing on the first query.
* The LadybugDB sink loads from memory: tables are bound to ``COPY ... FROM $df`` and ``LOAD FROM $df`` as Arrow, so nothing is serialised between generation and database. ``GraphTables.to_arrow()`` / ``from_arrow()`` make Arrow the interchange boundary. Scale 0.01 loads in about 10 seconds, half the file path.

Scale:

* Node attributes are drawn a column at a time. ``graphfaker.engine.fastfaker`` draws Faker providers (names, emails, phones, addresses, cities, companies, dates, uuids) from Faker's own locale tables with numpy, a column per call, keeping the vocabulary and the weights; the simple samplers (constant, category, uniform, gaussian, lognormal, poisson, bernoulli, reference) and foreign keys are vectorised too. Only expressions, mixtures, subcategories and the few providers without a vectorised form (``iban``, ``catch_phrase``) still go row by row. Scale 0.02 went from 230 s to 21 s, scale 0.1 (a million accounts, nine million transactions) from 165 s to about 60 s single-process, and scale 1.0 (ten million accounts, ninety million transactions) from over two hours to eight minutes with four workers, plus two minutes to write 2 GB of Parquet, at a 32 GB peak.
* Foreign-key indexes are int32 positions per group rather than lists of id strings, and a worker pool receives them once through a file in its initialiser instead of with every shard: at scale 1.0 the old form spent two hours pickling seven million customer ids a thousand times. Pattern recruitment draws members in O(1) instead of a set difference over every account, and decoys reuse the account pools instead of scanning for them per pattern.
* Memory: transaction frames are built with polars columns rather than numpy arrays of Python strings, the channels are merged one at a time with the time ordering computed on a narrow frame instead of a concatenation of everything, and Parquet is written in 2M-row chunks. The true peak (sampled, workers included) is about five times the final tables: 3.7 GB at scale 0.1, 32 GB at scale 1.0. Streaming the transaction process to disk is the next step.
* **Datasets change.** Runs are still a pure function of seed and shard size, but the values drawn for a given seed differ from 0.5.0's, because the attribute columns now come from the shard's numpy stream rather than Faker's and ``random``'s call sequences. Node counts, structure and distributions are unchanged; edge counts move by about 0.15% at ``scale=0.01`` and 0.02% at ``scale=0.1``, because pattern injection consumes its randomness differently and so draws a slightly different number of transactions.

Documentation:

* The documentation site is rebuilt on pydata-sphinx-theme with a landing page, a header with six sections (Get started, Guides, Domains, Databases, Reference, Project), the full page tree in the left sidebar, the page outline on the right, breadcrumbs, previous and next links, a light and a dark variant, and an API reference generated from the docstrings. The Install, first commands, Python usage, domains overview and command-line pages are cut from the README at build time so the two cannot drift.
* The site is light only, the LadybugDB driver in every example is ``ladybug``, and the old ``readme``, ``installation``, ``quickstart`` and ``usage`` pages redirect to their new homes.
* A Credits section names what was borrowed: gen-fraud-graph's scale convention, evaluator levels and output layout; AMLworld's typology catalogue; Data Designer as the reference point. The fraud pack has a one-paragraph abstract at the top of its page and its README section.
* The OSM tests carry the ``network`` marker; CI no longer depends on Overpass answering.

0.5.0 (2026-09-15)
------------------

GraphFaker becomes a generator of synthetic graph data that behaves like the real thing: you describe the graph you need, or pick a ready-made domain, and get entities, relationships and events whose structure, attributes and timing agree, with the ground truth included. Tabular generators produce rows; GraphFaker generates the connections. This release adds schema-driven generation, the fraud domain pack, database sinks, a modular domain registry, and realistic graph topology.

Positioning and documentation:

* README, package description, package docstring and the docs index all carry the same statement of what GraphFaker is and who it is for.
* New guides: ``docs/how-it-works.md`` (schema to tables), ``docs/fraud-generation.md`` (every step of the bank and its typologies), ``docs/methods.md`` (families of synthetic graph generation and which GraphFaker uses), ``docs/adding-a-domain.md``.
* ``graphfaker.domains.registry``: every domain is a ``Domain`` with a name, an options model and a ``generate`` function; third-party domains register through the ``graphfaker.domains`` entry-point group. ``graphfaker domains`` lists them, ``graphfaker generate <domain> --option value`` runs any of them.
* The README explains what each option means (``--seed``, ``--scale``, ``--hardness`` and the rest) and when to change it.

* ``graphfaker.schema``: declarative ``GraphSchema``: node types with attribute samplers, latent factors with per-group parameters, edge families, topology models. Validated with pydantic; round-trips through YAML/JSON; content-hashed.
* ``graphfaker.engine``: ``generate(schema, seed, shard_size)`` → ``GraphRun`` with columnar tables (polars), ground truth, and a manifest. Reproducible across processes; node generation is sharded with independent seeded streams.
* ``graphfaker.backends.GraphTables``: node table per type, edge table per relationship; NetworkX view; Parquet read/write.
* ``graphfaker.domains.social``: the built-in social graph re-expressed as a schema. Same realism metrics as 0.5; every former magic number is now a schema field.
* ``GraphFaker.generate(schema)`` alongside the unchanged ``generate_graph``.
* ``graphfaker.domains.fraud``: the fraud / AML domain pack: customers, accounts, merchants, devices, counterparties; a vectorised transaction process with recurring flows, repeat partners, merchant popularity, seasonality and income scaling; eleven labelled typologies with decoys; ``hardness_report`` (single-feature AUCs), ``realism_report`` and an ``evaluate`` harness compatible with gen-fraud-graph's.
* ``graphfaker.sinks``: Neo4j admin-import files, LadybugDB/Kùzu (DDL + ``COPY`` from Parquet, loads when a driver is installed), and a gen-fraud-graph compatible layout.
* CLI is now multi-command: ``graphfaker gen`` (the previous behaviour), ``graphfaker fraud``, ``graphfaker evaluate``; ``graphfaker`` console script.
* ``workers`` parallelises node sampling across processes without changing the result. osmnx is imported on first use, halving import time.
* ``docs/notebooks/graphfaker_tour.ipynb`` (executed, with charts) and ``examples/fraud_tour.py``: schemas, the fraud pack, exploration, ground truth, hardness, detectors, read/write/query. ``graphfaker[examples]`` extra.
* Place nodes carry ``latitude`` / ``longitude`` columns instead of a ``coordinates`` tuple.
* New dependencies: pydantic, polars, pyarrow, numpy, pyyaml.
* Removed the superseded proposal documents.

Realistic graph topology (2026-08-01):

Until now both endpoints of every edge were drawn uniformly at random, so the synthetic generator produced an Erdos-Renyi graph: Poisson degree distribution, no hubs, effectively no clustering, no community structure, and attributes statistically independent of the topology. It was labelled "realistic" but was not, and anything measured against it was measured against noise.

Edges are now formed by preferential attachment, triadic closure, and homophily over latent communities. Measured on 600 nodes / 2400 edges, seed 1:

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

* ``graphfaker.metrics``: ``graph_stats()`` and ``compare_topology()``, so the claim is checkable rather than asserted. The tests are differential: each property is compared against ``topology="uniform"``, which reproduces the old behaviour and is retained solely as a baseline.
* Every node carries a latent ``community``; attributes are drawn from community-specific distributions, which is what makes homophily possible.
* Places, organizations, events, and products carry a heavy-tailed ``prominence`` driving popularity.
* ``population`` tracks connectivity and ``employee_count`` now correlates 0.95 with the ``WORKS_AT`` edges actually present, instead of contradicting them.
* ``industry`` holds an industry rather than ``fake.job()``'s job titles.
* ``LIVES_IN``, ``BORN_IN``, and ``HEADQUARTERED_IN`` are singular. A person could previously live in four cities.
* ``total_edges`` now means what ``number_of_edges()`` reports. Counting loop iterations overshot by ~14% when bidirectional friendships dominated and undershot by ~12% when skipped functional relationships did.
* No isolated nodes. Preferential attachment alone left 24% of a 600-node graph unreachable, so coverage and top-up passes guarantee a giant component.

Fixed:

* GraphML export raised on ``None`` attribute values, which broke export for any graph small enough to leave a community without a place.

Note for anyone comparing results across versions: entity resolution is measurably harder on a realistic graph. The same injected duplicates give ``resolve()`` precision 1.000 on a uniform graph and 0.88 on a realistic one, because homophily means distinct people share attributes and neighbours. Numbers produced before 0.5 were flattered by the generator.

0.4.0 (2026-07-31)
------------------

Graph-native entity resolution, reproducible generation, and several fixes.

New:

* ``graphfaker.export``: export connectors that write files rather than requiring a database driver: ``export_csv`` (key-union headers, so heterogeneous node types keep their values in the right columns), ``export_neo4j_csv`` (``neo4j-admin`` typed headers), and ``export_cypher`` for Neo4j, openCypher, and ISO GQL. Also exposed as ``--format`` on the CLI.
* ``graphfaker.corpus``: paired text/entity corpora for measuring how many nodes a graph builder creates per real entity. Documents are clean prose, not corrupted text; ``Corpus.audit()`` verifies that no surface form could be claimed by two entities, and ``duplication_report()`` produces the counts.
* ``examples/duplication_experiment.py``: runs that measurement across several graph-building frameworks and prints a comparison table. The Cognee adapter is written against the API verified in cognee 1.4.1 (``add``/``cognify``/``export`` are coroutines; ``export`` accepts ``format="graphml"``; there is no ``get_graph_data``). It writes to a run-specific dataset instead of calling ``cognee.prune``, so an existing local store is not destroyed.
* ``docs/notebooks/duplication_experiment.ipynb``: the same experiment end to end, including repairing the graph with ``resolve()``, a threshold sweep showing the precision/recall tradeoff, and export of the cleaned graph. Runs without an LLM key using a labelled simulated extraction.
* ``resolve_entities(token_subset_floor=...)``: floors the attribute score when one name's tokens are contained in the other's ("Hill" inside "Allison Hill"). Character ratios score that pair near 0.5, so it was previously discarded before neighbourhood overlap was consulted. The floor sits below the default threshold deliberately, so containment alone never merges anything.
* ``graphfaker.resolve``: find duplicate entities using attribute similarity **and** neighbourhood overlap, then merge each cluster onto one canonical node with its edges rewired. Available as ``GraphFaker.resolve()`` or the standalone ``resolve_entities()`` / ``merge_clusters()``.
* ``evaluate_clusters()``: pairwise and B-cubed precision/recall/F1 for scoring a predicted clustering against gold labels you supply.
* Seeding: ``GraphFaker(seed=...)``, ``generate_graph(..., seed=...)``, ``reseed()``, and ``--seed`` on the CLI. Identical seed and arguments now produce an identical graph, and seeding no longer touches global ``random`` state.

Fixed:

* **Security:** TLS certificate verification was disabled for all flight-data downloads. It is now enabled by default; opt out with ``GRAPHFAKER_INSECURE_TLS=1``, which warns loudly.
* **Packaging:** ``tqdm`` and ``urllib3`` were imported but never declared as dependencies, so a clean install could fail on ``import graphfaker``.
* The CLI logged through the standard library ``venv`` module's logger by accident (``from venv import logger``) instead of the package logger.
* ``generate_graph`` reported the wrong valid sources on error ("Use 'random' or 'osm'").
* GraphML export now flattens list, tuple, set, and dict attributes instead of failing on them.
* Three CLI tests were failing on ``main`` because their mocks returned strings where a graph was required; tests also no longer write artifacts into the repository root.

Docs:

* README no longer advertises unimplemented features (RDF/JSON-LD export, Neo4j/Kuzu/TigerGraph integration, million-node scale, LLM-driven fetching).

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

