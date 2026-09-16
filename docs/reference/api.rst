Python API
==========

The public surface, by layer. Everything below is importable from the module named in each heading; the most used names are also exported from ``graphfaker`` itself (``GraphFaker``, ``GraphSchema``, ``GraphTables``, ``GraphRun``, ``Manifest``, ``generate``).

Entry point
-----------

.. automodule:: graphfaker.core
   :members: GraphFaker

Schema
------

.. automodule:: graphfaker.schema.graph
   :members: GraphSchema, NodeType, EdgeType, Relationship, LatentFactor, DegreeDerived, RealismTargets

.. automodule:: graphfaker.schema.samplers
   :members: ConstantSampler, CategorySampler, SubcategorySampler, UniformSampler, GaussianSampler, LognormalSampler, PoissonSampler, BernoulliSampler, FakerSampler, ExpressionSampler, ReferenceSampler, MixtureSampler, ForeignKeySampler

.. automodule:: graphfaker.schema.topology
   :members: SocialTopology, UniformTopology, NumericAffinity

Engine
------

.. automodule:: graphfaker.engine.run
   :members: generate, GraphRun, Manifest, fingerprint

.. automodule:: graphfaker.engine.seeding
   :members: Streams

Tables
------

.. automodule:: graphfaker.backends.tables
   :members: GraphTables

Domains
-------

.. automodule:: graphfaker.domains.registry
   :members: Domain, register, available, get

.. automodule:: graphfaker.domains.social
   :members: schema, generate, SocialOptions

.. automodule:: graphfaker.domains.fraud.config
   :members: FraudConfig, HardnessProfile

.. automodule:: graphfaker.domains.fraud.generate
   :members: generate

.. automodule:: graphfaker.domains.fraud.hardness
   :members: hardness_report, realism_report, HardnessReport

.. automodule:: graphfaker.domains.fraud.evaluate
   :members: evaluate, Evaluation, Scores

Databases and files
-------------------

.. automodule:: graphfaker.sinks.ladybug
   :members: load_directory, load_tables, write_ladybug, verify_directory, verify_tables, ladybug_script

.. automodule:: graphfaker.sinks.duckdb
   :members: load_directory, load_tables, write_duckdb, verify_directory, verify_tables, duckdb_script, property_graph

.. automodule:: graphfaker.sinks.pyg
   :members: to_hetero_data, write_pyg, from_directory, arrays, encode_features, GraphArrays

.. automodule:: graphfaker.sinks.neo4j_live
   :members: Target, load_directory, load_tables, LoadReport

.. automodule:: graphfaker.sinks.neo4j_verify
   :members: verify_directory, verify_tables

.. automodule:: graphfaker.sinks.verify
   :members: verify, Backend, CypherBackend, Verification, Check, aggregate_plan

.. automodule:: graphfaker.sinks.neo4j
   :members: write_neo4j_admin

.. automodule:: graphfaker.sinks.gen_fraud_graph
   :members: write_gen_fraud_graph

.. automodule:: graphfaker.export
   :members: export_csv, export_neo4j_csv, export_cypher

Metrics, resolution, corpus
---------------------------

.. automodule:: graphfaker.metrics
   :members: graph_stats, compare_topology, numeric_assortativity

.. automodule:: graphfaker.resolve
   :members: resolve_entities, merge_clusters, evaluate_clusters, ResolutionResult

.. automodule:: graphfaker.corpus
   :members: generate_corpus, duplication_report, Corpus, DuplicationReport

Real-world sources
------------------

.. automodule:: graphfaker.fetchers.osm
   :members: OSMGraphFetcher

.. automodule:: graphfaker.fetchers.flights
   :members: FlightGraphFetcher

.. automodule:: graphfaker.fetchers.wiki
   :members: WikiFetcher
