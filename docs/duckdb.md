# GraphFaker and DuckDB (SQL/PGQ)

A generated dataset is a set of tables, and DuckDB reads Parquet in place. So this is the sink that needs no graph database at all: the tables land as tables, one statement declares which of them are vertices and which are edges, and the DuckPGQ extension answers graph pattern queries in SQL/PGQ, the graph syntax that is part of the ISO SQL:2023 standard. Two commands:

```bash
pip install "graphfaker[duckdb]"
graphfaker fraud --scale 0.01 --seed 42 --out ./bank
graphfaker load duckdb ./bank                     # creates ./bank/graph.duckdb, loads it, verifies it
```

`load` builds the database from the Parquet on disk (DuckDB reads the files itself), adds the ground truth, declares the property graph, and then verifies the result against the files it came from. It exits non-zero if they disagree.

```
loaded into database 'bank/graph.duckdb' in 5.2s (257,131 rows/s)
  nodes    255,772  Account=100000, Counterparty=200, Customer=71429, Device=82143, Merchant=2000
  edges  1,088,552  OWNS=100000, PAYS=619689, TRANSFERS=247336, USES=87093, WIRES=34434
  truth        492  Pattern=33, IN_PATTERN=205, PAYS.is_fraud=31, TRANSFERS.is_fraud=215, WIRES.is_fraud=0, Region=8

PASS: 154/154 checks on bank/graph.duckdb
```

The same thing happens at generation time with `--sink duckdb`:

```bash
graphfaker generate fraud --scale 0.01 --seed 42 --out ./bank --sink duckdb
```

## What you get

One table per node type and per relationship, named as in the dataset, so this works for any domain. Node tables have `id` as their primary key; relationship tables have `source` and `target` plus the relationship's own columns. That is the whole schema, and it is ordinary SQL:

```sql
SELECT a.id, count(*) AS transfers, round(sum(t.amount)) AS sent
FROM "TRANSFERS" t JOIN "Account" a ON t.source = a.id
GROUP BY a.id ORDER BY sent DESC LIMIT 5;
```

The property graph sits on top of the same tables. `load` declares it, and `load.sql` records it:

```sql
CREATE OR REPLACE PROPERTY GRAPH "fraud"
  VERTEX TABLES ("Customer", "Account", "Merchant", "Device", "Counterparty", "Pattern", "Region")
  EDGE TABLES (
    "OWNS" SOURCE KEY ("source") REFERENCES "Customer" ("id") DESTINATION KEY ("target") REFERENCES "Account" ("id"),
    "PAYS" SOURCE KEY ("source") REFERENCES "Account" ("id") DESTINATION KEY ("target") REFERENCES "Merchant" ("id"),
    "TRANSFERS" SOURCE KEY ("source") REFERENCES "Account" ("id") DESTINATION KEY ("target") REFERENCES "Account" ("id"),
    ...
  );
```

The graph is named after the dataset's schema (`fraud`, `social`; `--graph` overrides it). With it, a pattern is written as a pattern:

```sql
FROM GRAPH_TABLE (fraud
  MATCH (a:Account)-[t:TRANSFERS]->(b:Account)
  WHERE t.amount > 9000
  COLUMNS (a.id AS payer, b.id AS payee, t.amount))
ORDER BY amount DESC LIMIT 5;
```

`GRAPH_TABLE` returns a table, so everything after it is SQL again: `GROUP BY`, window functions, joins back to the tables, `COPY ... TO 'flagged.parquet'`.

### The truth

`truth/` lands the way it does in the other databases:

```
"Pattern"    (id, typology, is_fraud, n_accounts, n_transactions, start, end, accounts, roles)
"IN_PATTERN" (source -> Account.id, target -> Pattern.id, typology, role, is_fraud)
"Region"     (id, group, log_income, log_balance)
```

and the money relationships (`PAYS`, `TRANSFERS`, `WIRES`) gain `pattern_id`, `typology` and `is_fraud` columns, set from `truth/transactions.parquet`. Membership is a table of its own because an account can be in several patterns with a different role in each.

```sql
FROM GRAPH_TABLE (fraud
  MATCH (a:Account)-[m:IN_PATTERN]->(p:Pattern)
  COLUMNS (p.typology, p.is_fraud, m.role))
SELECT typology, is_fraud, count(*) AS members GROUP BY ALL ORDER BY members DESC;
```

```
typology        is_fraud  members
fan_out         false     44        <- decoys: the same shape, legitimately
gather_scatter  true      26
fan_out         true      21
scatter_gather  true      19
fan_in          true      18
mule_network    true      16
```

### `--blind`

`--blind` loads the graph only. There is no `Pattern`, `IN_PATTERN` or `Region` table, and the money relationships have no `is_fraud` column at all, so a detector run against the database cannot read the answer out of it. Verify it with `graphfaker verify duckdb ./bank --blind`; the truth checks are simply not run.

## How the data gets in

From a directory, DuckDB reads the Parquet files directly (`INSERT INTO "Account" BY NAME SELECT * FROM read_parquet('bank/nodes/Account.parquet')`); nothing passes through Python. From memory (`write_duckdb`, `--sink duckdb`), each frame is registered as an Arrow view for the statement that reads it. Both paths produce the same tables, and a test checks that they do.

`load.sql` is written alongside with the file paths, so the whole load, property graph included, can be replayed from the DuckDB shell or any client:

```bash
duckdb bank/graph.duckdb < bank/load.sql
```

The property graph needs the DuckPGQ community extension, which `load` installs on first use (`INSTALL duckpgq FROM community`). The extension is built per DuckDB release, and not every release gets a build, so `graphfaker[duckdb]` pins a DuckDB version that has one. If the extension cannot be installed (no network, or a DuckDB it has no build for), the tables still load and verify, a warning says so, and the `CREATE PROPERTY GRAPH` statement in `load.sql` can be run later.

## Validating the load

`graphfaker verify duckdb ./bank` runs the same checks as the Neo4j and LadybugDB verifiers, asked in SQL: counts per table, primary keys present, no duplicated ids, every relationship's endpoints present in the tables it should join (a `LEFT JOIN` that must find nothing), per-column aggregates (how many rows carry each column, the sum, minimum and maximum of every numeric and temporal one), a sample of rows fetched back and compared column by column, and the truth's coverage. The dataset on disk is the oracle; the database is the thing under test. `--verbose` prints every check.

## Querying

Some of the rules from the [Neo4j cookbook](neo4j.md), written in SQL/PGQ against the same dataset. The times are for scale 0.01 on a laptop.

Fan-in, the collector with many payers in a month (0.2 s):

```sql
FROM GRAPH_TABLE (fraud
  MATCH (a:Account)-[t:TRANSFERS]->(c:Account)
  COLUMNS (c.id AS collector, a.id AS payer, t.timestamp AS ts, t.amount AS amount))
SELECT collector, date_trunc('month', ts) AS month, count(DISTINCT payer) AS payers, round(sum(amount)) AS received
GROUP BY ALL HAVING payers >= 6
ORDER BY payers DESC LIMIT 5;
```

Scored against the truth, the way every rule should be (0.1 s):

```sql
WITH flagged AS (
  FROM GRAPH_TABLE (fraud MATCH (a:Account)-[t:TRANSFERS]->(c:Account)
    COLUMNS (c.id AS collector, a.id AS payer, t.timestamp AS ts))
  SELECT collector, date_trunc('month', ts) AS month, count(DISTINCT payer) AS payers
  GROUP BY ALL HAVING payers >= 6
),
actual AS (
  FROM GRAPH_TABLE (fraud MATCH (a:Account)-[m:IN_PATTERN]->(p:Pattern) WHERE p.is_fraud
    COLUMNS (a.id AS account))
  SELECT DISTINCT account
)
SELECT count(DISTINCT collector) AS flagged,
       count(DISTINCT collector) FILTER (collector IN (SELECT account FROM actual)) AS true_positives,
       (SELECT count(*) FROM actual) AS fraud_accounts
FROM flagged;
```

```
flagged  true_positives  fraud_accounts
1366     9               121
```

Nine of 121, for 1,366 alerts: employers paying salaries and marketplaces collecting payments are fan-ins too, and the dataset produces them from the process rather than scripting them. That is the false positive every analyst knows, and it is what makes a rule worth measuring.

Pass-through, money in and straight back out (0.15 s):

```sql
FROM GRAPH_TABLE (fraud
  MATCH (x:Account)-[i:TRANSFERS]->(mid:Account)-[o:TRANSFERS]->(y:Account)
  WHERE o.timestamp > i.timestamp
    AND o.timestamp < i.timestamp + INTERVAL 1 DAY
    AND abs(o.amount - i.amount) / i.amount < 0.1
  COLUMNS (mid.id AS mule, i.amount AS moved))
SELECT mule, count(*) AS hops, round(sum(moved)) AS moved
GROUP BY mule ORDER BY hops DESC LIMIT 5;
```

Two things to know about DuckPGQ 0.3. A SQL comment cannot be the first thing in a statement that contains `GRAPH_TABLE`; put comments after the query or drop them. And path finding (`MATCH p = ANY SHORTEST (a)-[t:TRANSFERS]->{3,5}(b)`) works on small graphs but did not return on 247,000 transfers in the time we gave it; for cycle and path queries at that size use the [LadybugDB](ladybug.md) or [Neo4j](neo4j.md) sink of the same dataset. Pattern matching, which is joins underneath, is fast at any scale DuckDB handles.

## From Python

```python
import duckdb
from graphfaker.sinks.duckdb import load_directory, verify_directory

print(load_directory("bank", "bank/graph.duckdb").summary())
assert verify_directory("bank", "bank/graph.duckdb").ok

conn = duckdb.connect("bank/graph.duckdb")
conn.execute("LOAD duckpgq")
conn.sql("""
  FROM GRAPH_TABLE (fraud
    MATCH (a:Account)-[t:TRANSFERS]->(b:Account) WHERE t.is_fraud
    COLUMNS (a.id AS payer, b.id AS payee, t.amount, t.typology))
""").pl()   # a Polars frame; .df() for pandas, .arrow() for Arrow
```

At generation time, with the truth or without:

```python
from graphfaker.domains import fraud
from graphfaker.sinks.duckdb import write_duckdb

run = fraud.generate(scale=0.01, seed=42)
write_duckdb(run.tables, "bank", db_path="bank/graph.duckdb", truth=run.truth, graph="fraud")
```

## DuckDB, LadybugDB or Neo4j

| | DuckDB | LadybugDB | Neo4j |
|---|---|---|---|
| setup | `pip install "graphfaker[duckdb]"`, nothing to run | `pip install "graphfaker[ladybug]"`, nothing to run | a server or Aura |
| query language | SQL, with SQL/PGQ for patterns | Cypher | Cypher |
| load at scale 0.01 | 5 s, reads the Parquet directly | 11 s, from Arrow | minutes over Bolt, or the offline importer |
| path queries | pattern matching yes; path finding slow at this size | yes | yes, plus GDS |
| where it fits | analytics teams already in DuckDB; the tables are the graph | a single-file graph database | production graph tooling, visualisation |

The verifier's checks are the same on all three, so a detector can be developed against one and its scores compared on another.
