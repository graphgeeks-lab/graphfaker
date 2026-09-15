# GraphFaker and LadybugDB

LadybugDB (formerly Kùzu) is an embedded graph database: no server, one file on disk, Cypher, and it reads Parquet natively. That makes it the shortest path from a generated dataset to a queryable graph. Two commands:

```bash
pip install kuzu                                  # or: pip install ladybug (same API)
graphfaker fraud --scale 0.01 --seed 42 --out ./bank
graphfaker load ladybug ./bank                    # creates ./bank/graph.lbdb, loads it, verifies it
```

`load` builds the database from the Parquet on disk, adds the ground truth as a subgraph, and then verifies the result against the files it came from. It exits non-zero if they disagree.

```
loaded into database 'bank/graph.lbdb' in 10.6s (127,116 rows/s)
  nodes    255,772  Account=100000, Counterparty=200, Customer=71429, Device=82143, Merchant=2000
  edges  1,088,552  OWNS=100000, PAYS=619689, TRANSFERS=247336, USES=87093, WIRES=34434
  truth        492  Pattern=33, IN_PATTERN=205, PAYS.is_fraud=31, TRANSFERS.is_fraud=215, WIRES.is_fraud=0, Region=8

PASS: 154/154 checks on bank/graph.lbdb
```

The same thing happens at generation time with `--sink ladybug`:

```bash
graphfaker generate fraud --scale 0.01 --seed 42 --out ./bank --sink ladybug
```

## What you get

Node and relationship tables come from the dataset, one per node type and per relationship, so this works for any domain. Every node table has `id` as its primary key; relationship tables declare their endpoint types, so an edge cannot join the wrong tables.

```
(:Customer)-[:OWNS]->(:Account)
(:Customer)-[:USES]->(:Device)
(:Account)-[:PAYS {tx_id, timestamp, amount, memo, recurring}]->(:Merchant)
(:Account)-[:TRANSFERS {tx_id, timestamp, amount, memo, recurring}]->(:Account)
(:Account)-[:WIRES {tx_id, timestamp, amount, memo, recurring}]->(:Counterparty)
```

### The truth subgraph

`truth/` becomes part of the graph, with the same model as the Neo4j loader:

```
(:Pattern {id, typology, is_fraud, n_accounts, n_transactions, start, end, accounts, roles})
(:Account)-[:IN_PATTERN {typology, role, is_fraud}]->(:Pattern)
(:Region {id, group, log_income, log_balance})
```

Membership is a relationship rather than a property because an account can belong to more than one pattern, and the role it plays (`source`, `collector`, `mule`, ...) differs per pattern. Labelled transactions get `is_fraud`, `pattern_id` and `typology` on the money relationship itself. `Region` nodes carry each latent region's generation parameters; join them on the property (`c.region = r.group`).

## How the data gets in

Nothing is serialised on the way in. The database accepts an in-memory Arrow table as a statement parameter, and GraphFaker's tables are Arrow memory already, so the loader runs `COPY Account FROM $df` with the frame bound as `$df`. The same goes for the truth: `LOAD FROM $df CREATE (:Pattern {...})`. At scale 0.01 that is about twice as fast as going through Parquet, and at large scales it avoids writing and reading the dataset a second time.

`load.cypher` is written alongside, with file paths in place of the parameters (`COPY Account FROM "bank/nodes/Account.parquet"`), so the load can be reproduced by hand or from another tool from the dataset directory alone. A test checks that the script and the in-memory path produce the same database.

### `--blind`

`--blind` loads the graph only. There is no `Pattern` or `Region` table, no `IN_PATTERN`, and the money relationships have no `is_fraud` column at all, so there is nothing to read the answer out of:

```bash
graphfaker load ladybug ./bank --db ./bank/blind.lbdb --blind
graphfaker verify ladybug ./bank --db ./bank/blind.lbdb --blind
```

The usual arrangement is a blind database for whoever builds the detector and a truth-loaded one for whoever scores it. Both come from the same Parquet.

## Validating the load

`COPY ... FROM` Parquet can drop rows or coerce types without raising. `graphfaker verify ladybug` treats the Parquet as the oracle and the database as the thing under test, using the same checks as the Neo4j verifier (they share one implementation; only a small adapter differs per database):

| family | what it asks | catches |
|---|---|---|
| **cardinality** | a count per table | dropped rows, doubled loads |
| **structure** | no tables beyond the schema, keys unique, no transaction loaded twice | duplicate keys, a second load on top of the first |
| **content** | one aggregate scan per table: how many rows carry each property, plus sum, min and max of every numeric and temporal column | type coercion, truncation, a column silently not loaded |
| **round trip** | a sample of rows fetched back and compared property by property | a value replaced by a different value of the same type |
| **truth** | pattern counts, membership counts and reachability, labelled transactions per relationship type | truth that drifted from the graph it describes |

Two Neo4j checks do not apply and are answered by the schema: there is no constraint catalogue (primary keys are part of the table definition), and relationship endpoints are fixed by the table.

The verifier is tested by breaking things on purpose: delete relationships, null a column, change an amount, flip a pattern's `is_fraud`, load a transaction twice, alter a sampled value. Each corruption has a named check that must be the one to fail.

```bash
graphfaker verify ladybug ./bank --verbose      # every check, not only failures
```

## Querying

```python
import kuzu
conn = kuzu.Connection(kuzu.Database("bank/graph.lbdb"))
```

Who collects from many senders, and were they planted?

```cypher
MATCH (s:Account)-[t:TRANSFERS]->(c:Account)
WITH c, s, sum(t.amount) AS from_sender
WITH c, count(s) AS senders, round(sum(from_sender), 0) AS volume
WHERE senders >= 6
RETURN c.id, senders, volume, EXISTS { MATCH (c)-[:IN_PATTERN]->(:Pattern) } AS planted
ORDER BY senders DESC LIMIT 10
```

The biggest collectors are not planted: they are marketplaces, employers and popular people. Rings are small on purpose (see the hardness levels in [fraud-generation.md](fraud-generation.md)).

Near-threshold structuring, with the answer next to the guess:

```cypher
MATCH (a:Account)-[t:TRANSFERS]->()
WHERE t.amount >= 8500 AND t.amount < 10000
WITH a, count(t) AS n
WHERE n >= 3
RETURN a.id, n, EXISTS { MATCH (a)-[:IN_PATTERN]->(:Pattern {typology: 'structuring'}) } AS planted
ORDER BY n DESC
```

Most of the rows that come back are businesses paying salaries, which is the false positive every AML analyst knows; the fraud pack produces it from the process rather than scripting it.

One Kùzu 0.11 quirk worth knowing: an `OPTIONAL MATCH` that finds nothing, placed after a `WITH` that mixes `count(DISTINCT ...)` with another aggregate, returns null for the other aggregate. `EXISTS { }` and a two-stage `WITH`, as above, avoid it.

Score a query as a detector with the built-in evaluator:

```python
from graphfaker.domains.fraud.evaluate import evaluate

rows = conn.execute("MATCH (s)-[:TRANSFERS]->(c:Account) WITH c, count(DISTINCT s) AS k WHERE k >= 6 RETURN c.id").get_all()
print(evaluate("bank", flagged_accounts=[r[0] for r in rows], ring_threshold=0.5).summary())
```

## From Python

```python
from graphfaker.sinks.ladybug import load_directory, verify_directory, write_ladybug

report = load_directory("bank", "bank/graph.lbdb", truth=True)     # or truth=False for blind
print(report.summary())
result = verify_directory("bank", "bank/graph.lbdb")
assert result.ok, result.summary()

# at generation time
run = fraud.generate(scale=0.01, seed=42)
write_ladybug(run.tables, "bank", db_path="bank/graph.lbdb", truth=run.truth)
```

## Ladybug or Neo4j

| | LadybugDB | Neo4j (Bolt) |
|---|---|---|
| setup | `pip install kuzu`, nothing to run | a server or Aura |
| load speed at scale 0.01 | about 10 seconds, from memory | about a minute and a half |
| good for | exploring, notebooks, CI, scoring detectors, shipping a dataset as one file | a graph others connect to, Graph Data Science, Bloom |
| verification | same checks | same checks |

They load the same Parquet into the same model, so a query written against one runs against the other with at most a function name changed.
