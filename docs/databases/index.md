# Databases

A generated dataset is a folder of Parquet files with a manifest. These pages put it in a database, with or without the ground truth, and check that what landed is what was generated.

- [Neo4j](../neo4j.md): the offline importer and the live loader, what the verifier checks, and a scored Cypher cookbook for the laundering typologies.
- [LadybugDB](../ladybug.md): an embedded graph database in one file, loaded from Arrow, verified with the same checks.
- [DuckDB and SQL/PGQ](../duckdb.md): the dataset's tables as they are, with a property graph declared on top and pattern queries in ISO SQL/PGQ. No graph database needed.
- [Finding money laundering in a synthetic bank](../notebooks/neo4j_fraud_analysis.ipynb): every rule scored against the truth, then a model.

```{toctree}
:hidden:

Neo4j <../neo4j>
LadybugDB <../ladybug>
DuckDB and SQL/PGQ <../duckdb>
Finding laundering in Neo4j <../notebooks/neo4j_fraud_analysis>
```
