# GraphFaker and Neo4j

Three commands take a generated dataset from nothing to a queryable, verified graph:

```bash
pip install 'graphfaker[neo4j]'
export NEO4J_PASSWORD=...                        # or pass --password

graphfaker fraud --scale 0.01 --seed 42 --out ./bank
graphfaker load neo4j ./bank --database fraud --create
```

`load` writes the graph, writes the ground truth as a subgraph, and then verifies the result against the Parquet it came from. It exits non-zero if they disagree, so it is safe to put in a script.

```
loaded into database 'fraud' in 81.4s (16,511 rows/s)
  nodes    255,772  Account=100000, Counterparty=200, Customer=71429, Device=82143, Merchant=2000
  edges  1,088,552  OWNS=100000, PAYS=619689, TRANSFERS=247336, USES=87093, WIRES=34434
  truth        622  Pattern=33, IN_PATTERN=205, PAYS.is_fraud=31, TRANSFERS.is_fraud=345, Region=8

PASS: 154/154 checks on database 'fraud'
```

## Which way in

There are two paths into Neo4j and they are for different situations.

| | `graphfaker load neo4j` (Bolt) | `--sink neo4j-admin` (offline import) |
|---|---|---|
| database | running, untouched | must be **stopped** |
| needs | the Python driver | `neo4j-admin` on PATH, CSVs in the server's import dir |
| works against Aura | yes | no |
| speed | ~16k rows/s | millions of rows/s |
| good up to | a few tens of millions of rows | anything |
| verifies itself | yes | no |

Use Bolt for everything you would actually explore by hand. Use admin import when you are loading `--scale 1.0` (10M accounts, 90M transactions) and can afford to stop the database:

```bash
graphfaker fraud --scale 1.0 --out ./big --sink neo4j-admin
cd big/neo4j && sh import.sh          # with the database stopped
```

Measurements above are from Neo4j 2026.08 Enterprise on Apple Silicon with the default Desktop memory settings (1G heap, 512M page cache). The Bolt loader is round-trip-bound, not CPU-bound, so a bigger heap does not change much; `--batch-size` does, in either direction.

## What you get

Node labels and relationship types come straight from the dataset. The loader reads them off the tables rather than hard-coding a schema, so it works for any domain, including ones you add yourself.

```
(:Customer)-[:OWNS]->(:Account)
(:Customer)-[:USES]->(:Device)
(:Account)-[:PAYS {tx_id, timestamp, amount, memo, recurring}]->(:Merchant)
(:Account)-[:TRANSFERS {tx_id, timestamp, amount, memo, recurring}]->(:Account)
(:Account)-[:WIRES {tx_id, timestamp, amount, memo, recurring}]->(:Counterparty)
```

Every node has an `id` with a uniqueness constraint on it. Each money relationship type also gets a property index on `tx_id`.

### The truth subgraph

`truth/` becomes part of the graph rather than a column on the side:

```
(:Pattern {id, typology, is_fraud, n_accounts, n_transactions, start, end})
(:Account)-[:IN_PATTERN {role, is_fraud}]->(:Pattern)
(:Region {id, group, log_income, log_balance})
```

Membership is a relationship, not a property, because **an account can belong to more than one pattern**. In the dataset above that is 205 memberships across 181 distinct accounts. Flattening `typology` onto the account would silently drop rows. The role an account plays (`source`, `collector`, `mule`, …) is a property of the membership, since the same account can be a collector in one ring and a source in another.

Labelled transactions get `is_fraud`, `pattern_id` and `typology` set on the relationship itself, so a transaction's verdict travels with the money rather than needing a join.

`(:Region)` nodes carry the *generation parameters* of each latent region. They are not linked by relationships, because every node already has a `region` property, and adding 255k more relationships to express what is already there is not worth the load time. Join on the property:

```cypher
MATCH (r:Region)
MATCH (c:Customer) WHERE c.region = r.group
RETURN r.id, count(c) AS customers,
       round(100 * avg(c.log_income)) / 100 AS observed,
       round(100 * r.log_income) / 100 AS generated
ORDER BY r.id
```

```
region_0  8944  10.79  10.79
region_4  9158  11.34  11.34
region_7  8880  10.19  10.19
```

Observed matches generated to two decimals across all eight regions. That is a check on the generator, not on Neo4j: the latent structure the sampler drew survived generation, Parquet, and the load.

### `--blind`

Loading the truth is convenient and it also ruins the dataset as a benchmark, because anyone can read the answer out of the graph. `--blind` loads the graph only: no `(:Pattern)`, no `(:Region)`, no `is_fraud` anywhere.

```bash
graphfaker load neo4j ./bank --database fraud-blind --create --blind
graphfaker verify neo4j ./bank --database fraud-blind --blind
```

The usual arrangement is a blind database for whoever is building the detector and a truth-loaded one for whoever is scoring it. Both come from the same Parquet, so they are the same graph.

## Validating the load

A load that prints no error is not a load that is correct. Rows go missing when a relationship's endpoint does not exist; properties vanish when a column name collides with something Cypher wants; a re-run doubles every edge; a timestamp quietly loses its time. None of that raises. It just gives you a different graph than the one whose ground truth you are about to trust.

So `graphfaker verify neo4j` treats the Parquet as the oracle and Neo4j as the thing under test. Each check asks Parquet a question, asks Neo4j the same question, and prints both when they differ.

| family | what it asks | catches |
|---|---|---|
| **cardinality** | a count per label and per relationship type | dropped rows, doubled loads |
| **structure** | constraints online, keys unique, and every relationship between the endpoint labels its type should join | mislabelled nodes, duplicate keys, edges wired to the wrong place |
| **content** | one aggregate scan per label and type: how many nodes actually carry each property, plus sum, min and max of every numeric and temporal column | type coercion, truncation, a column silently not loaded |
| **round trip** | a sample of rows fetched back and compared property by property | a value replaced by a different value of the same type |
| **truth** | pattern counts, membership counts and reachability, labelled transactions per relationship type | truth that drifted from the graph it describes |

Counts alone are weak. They cannot see a `double` that arrived as a string or a datetime that lost its clock. That is what the aggregate and round-trip families are for.

### It is tested against real breakage

A verifier that only ever passes is not evidence of anything. The test suite loads a dataset, breaks exactly one thing, and asserts the named check is the one that fails:

| injected corruption | check that catches it |
|---|---|
| `DELETE` three `TRANSFERS` | `count/TRANSFERS` |
| `REMOVE a.balance` on two accounts | `content/Account.present:balance` |
| add 1000 to one `PAYS` amount | `content/PAYS.sum:amount` |
| relabel a `Merchant` as a `Customer` | `structure/PAYS.endpoints` |
| unflag one fraud `Pattern` | `truth/Pattern.is_fraud` |
| unflag one fraud transaction | `truth/TRANSFERS.is_fraud` |

```bash
pytest tests/test_neo4j_live.py                 # offline tests only
NEO4J_PASSWORD=... pytest -m neo4j              # the integration tests too
```

The integration tests run in their own `graphfaker-pytest` database and skip themselves when no Neo4j is reachable, so a plain `pytest` stays green on a machine that has never run one.

### In a pipeline

```bash
graphfaker fraud --scale 0.01 --seed 42 --out ./bank
graphfaker load neo4j ./bank --database fraud --create --wipe || exit 1
```

`load` refuses to write into a database that already has data unless you pass `--wipe`, because it uses `CREATE`: a second pass into a populated database would double everything rather than fail. Verification runs automatically; `--no-verify` skips it and `graphfaker verify neo4j` runs it on its own later.

## Analysis

What makes this dataset worth querying is that you can score your query. Every rule below is followed by what it actually found, measured against the truth subgraph on `--scale 0.01 --seed 42 --hardness medium`, which contains **121 accounts in 22 fraud patterns** and 60 more in 11 decoy patterns.

The scoring tail is the same every time. Produce a column called `flagged` and append:

```cypher
WITH collect(DISTINCT flagged) AS flagged
CALL () {
  MATCH (a:Account)-[:IN_PATTERN]->(p:Pattern) WHERE p.is_fraud
  RETURN collect(DISTINCT a.id) AS actual
}
WITH flagged, actual, [x IN flagged WHERE x IN actual] AS tp
RETURN size(flagged) AS flagged, size(tp) AS tp,
       round(1000.0 * size(tp) / size(flagged)) / 10 AS precision_pct,
       round(1000.0 * size(tp) / size(actual)) / 10 AS recall_pct
```

### Collectors: fan-in

The obvious rule. Accounts that receive from many distinct senders:

```cypher
MATCH (dst:Account)<-[t:TRANSFERS]-(src:Account)
WITH dst, count(DISTINCT src) AS senders, sum(t.amount) AS received,
     min(t.timestamp) AS first, max(t.timestamp) AS last
WHERE senders >= 8
RETURN dst.id AS account, senders, round(received) AS received,
       duration.inDays(first, last).days AS span_days
ORDER BY senders DESC LIMIT 10
```

It flags 2,473 accounts and catches 8 of the 121, so **0.3% precision, 6.6% recall**. Look at the output and the reason is plain: the top hits have 40 to 50 senders spread over the full 89-day period and a few thousand dollars in total. They are popular accounts, not collectors. Degree without a time window is a measure of popularity.

Add the window, meaning distinct senders inside one calendar week:

```cypher
MATCH (dst:Account)<-[t:TRANSFERS]-(src:Account)
WITH dst, t.timestamp.week AS week, count(DISTINCT src) AS senders
WHERE senders >= 5
RETURN dst.id AS flagged
```

192 flagged, 7 caught: **3.6% precision, 5.8% recall**. Ten times the precision for the same recall. Now add that the burst has to be most of what the account ever received. A collector's inflow is concentrated, a popular account's is not:

```cypher
MATCH (dst:Account)<-[t:TRANSFERS]-(:Account)
WITH dst, sum(t.amount) AS lifetime
MATCH (dst)<-[t:TRANSFERS]-(src:Account)
WITH dst, lifetime, t.timestamp.week AS week,
     count(DISTINCT src) AS senders, sum(t.amount) AS burst
WHERE senders >= 5 AND burst > 0.8 * lifetime
RETURN dst.id AS flagged
```

6 flagged, 4 caught: **66.7% precision, 3.3% recall**. That is the whole precision–recall trade-off in three queries, and you can watch it move.

### Cycles: where structure alone fails

Money that comes back to where it started. The three-hop version everyone writes first:

```cypher
MATCH (a:Account)-[t1:TRANSFERS]->(b:Account)-[t2:TRANSFERS]->(c:Account)-[t3:TRANSFERS]->(a)
WHERE a.id < b.id AND a.id < c.id
  AND t1.timestamp < t2.timestamp AND t2.timestamp < t3.timestamp
  AND duration.inDays(t1.timestamp, t3.timestamp).days <= 30
RETURN a.id AS a, b.id AS b, c.id AS c, count(*) AS paths
ORDER BY paths DESC
```

Six cycles in 247,336 transfers, and **zero of them are fraud**. The `a.id < b.id` guard is what keeps each cycle from being reported once per rotation.

The reason is structural: the injected fraud cycles in this dataset are four and five hops long. A three-hop query cannot see them at all. Bound the length instead, seed it from accounts that both took in and pushed out a large sum in the same week, and require time to move forward along the path:

```cypher
MATCH (a:Account)-[out:TRANSFERS]->(:Account)
WITH a, out.timestamp.week AS week, sum(out.amount) AS sent
WHERE sent > 5000
MATCH (a)<-[in_t:TRANSFERS]-(:Account)
WHERE in_t.timestamp.week = week
WITH DISTINCT a
MATCH path = (a)-[:TRANSFERS*3..5]->(a)
WITH nodes(path) AS ns, relationships(path) AS rs
WHERE all(i IN range(0, size(rs) - 2) WHERE rs[i].timestamp < rs[i + 1].timestamp)
  AND duration.inDays(rs[0].timestamp, rs[size(rs) - 1].timestamp).days <= 30
UNWIND ns AS n
RETURN n.id AS flagged
```

57 flagged, 4 caught: **7.0% precision, 3.3% recall**, in about 10 seconds. The seeding matters. Unseeded, a `*3..5` cycle search over 100,000 accounts is not something you want to wait for.

Now ask where its hits came from:

```cypher
// ... the cycle query above, then:
WITH collect(DISTINCT flagged) AS flagged
MATCH (a:Account)-[:IN_PATTERN]->(p:Pattern)
WITH p.typology AS typology, p.is_fraud AS is_fraud, flagged, collect(DISTINCT a.id) AS accounts
RETURN typology, is_fraud, size(accounts) AS accounts,
       size([x IN accounts WHERE x IN flagged]) AS caught
ORDER BY is_fraud DESC, typology
```

```
cycle  true    9   4
cycle  false  12  12
```

It catches 4 of the 9 fraud-cycle accounts and **12 of the 12 decoy-cycle accounts**. The decoys are legitimate activity shaped exactly like laundering, and a purely structural detector cannot tell them apart: it finds all of the innocent ones and fewer than half of the guilty ones. This is what the decoys are in the dataset for, and it is the single most useful thing to know before trusting a graph-shape rule in production.

### Other rules, and what they score

```cypher
// structuring: amounts parked just under the reporting threshold
MATCH (a:Account)-[t:TRANSFERS|WIRES]->()
WHERE t.amount > 8500 AND t.amount < 10000
WITH a, count(t) AS near_threshold, sum(t.amount) AS total
WHERE near_threshold >= 3
RETURN a.id AS account, near_threshold, round(total) AS total
ORDER BY near_threshold DESC LIMIT 10
```

```cypher
// pass-through: in and straight back out, same amount, within a day
MATCH (:Account)-[i:TRANSFERS]->(mid:Account)-[o:TRANSFERS]->(:Account)
WHERE o.timestamp > i.timestamp
  AND duration.inSeconds(i.timestamp, o.timestamp).seconds < 86400
  AND abs(o.amount - i.amount) / i.amount < 0.1
RETURN mid.id AS mule, count(*) AS hops, round(sum(i.amount)) AS moved
ORDER BY hops DESC LIMIT 10
```

```cypher
// velocity: an account's busiest day against its own baseline
MATCH (a:Account)-[t:TRANSFERS]->()
WITH a, date(t.timestamp) AS day, count(t) AS n
WITH a, avg(n) AS mean_per_active_day, max(n) AS busiest, count(day) AS active_days
WHERE active_days >= 5 AND busiest > 4 * mean_per_active_day
RETURN a.id AS account, active_days, busiest,
       round(100 * mean_per_active_day) / 100 AS mean_per_day
ORDER BY busiest DESC LIMIT 8
```

```cypher
// shared devices: the synthetic-identity signal, and households confound it
MATCH (c1:Customer)-[:USES]->(d:Device)<-[:USES]-(c2:Customer)
WHERE c1.id < c2.id
RETURN d.id AS device, collect(c1.id) + collect(c2.id) AS customers LIMIT 10
```

```cypher
// counterparty exposure: where the wires go
MATCH (a:Account)-[w:WIRES]->(cp:Counterparty)
RETURN cp.name AS counterparty, cp.country AS country,
       count(DISTINCT a) AS accounts, count(w) AS wires, round(sum(w.amount)) AS total
ORDER BY total DESC LIMIT 8
```

All of them, scored:

| rule | flagged | true positives | precision | recall |
|---|---|---|---|---|
| fan-in ≥ 8 senders, whole period | 2,473 | 8 | 0.3% | 6.6% |
| fan-in ≥ 5 senders in one week | 192 | 7 | 3.6% | 5.8% |
| …and the burst is >80% of lifetime inflow | 6 | 4 | **66.7%** | 3.3% |
| three-hop cycle | 51 | 0 | 0% | 0% |
| seeded 3–5 hop cycle, time-ordered | 57 | 4 | 7.0% | 3.3% |
| amounts just under the threshold | 643 | 3 | 0.5% | 2.5% |
| 24-hour pass-through | 565 | 7 | 1.2% | 5.8% |

`examples/neo4j_detectors.py` runs all of them and prints that table, so it can be regenerated rather than trusted:

```bash
NEO4J_PASSWORD=... python examples/neo4j_detectors.py --database fraud --breakdown
```

`--breakdown` splits each rule's hits by typology and by whether the pattern was fraud or a decoy, and one row of it explains the whole table:

```
fan-in >= 5 senders in one week    192   7   3.6   5.8
    fraud  fan_in           caught 2/17
    DECOY  fan_in           caught 4/4        <- every decoy
...and the burst is >80% of lifetime inflow    6   4   66.7   3.3
    fraud  fan_in           caught 1/17
    fraud  gather_scatter   caught 3/25       <- no decoys at all
seeded 3-5 hop cycle, time-ordered   57   4   7.0   3.3
    fraud  cycle            caught 4/9
    DECOY  cycle            caught 12/12      <- every decoy
```

The structural rules catch every decoy they can reach. The one rule that does not catch a single decoy is the one that stopped asking about shape and started asking about *behaviour*, whether the inflow was concentrated. That is why its precision is 66.7% and everything else is under 8%. Shape tells you where to look; it does not tell you what you found.

Nothing here gets far past 7% recall, and that is the dataset working as intended. At `scale=0.01` the graph averages about nine transactions per account over 90 days, which is thin cover; `--hardness` then tunes how closely the injected patterns imitate the legitimate process. Single-signal Cypher rules are supposed to struggle. See `docs/fraud-generation.md` for what hardness changes and what it cannot hide.

### Scoring with the built-in evaluator

Cypher can compute precision and recall, but the packaged evaluator also scores at pattern granularity and breaks results down per typology. Export what you flagged and hand it over:

```python
# run against the blind database, so the rule cannot read the answer
from graphfaker.sinks import Target

driver = Target(database="fraud-blind", password="...").connect()
records, _, _ = driver.execute_query("""
    MATCH (dst:Account)<-[t:TRANSFERS]-(:Account)
    WITH dst, sum(t.amount) AS lifetime
    MATCH (dst)<-[t:TRANSFERS]-(src:Account)
    WITH dst, lifetime, t.timestamp.week AS week,
         count(DISTINCT src) AS senders, sum(t.amount) AS burst
    WHERE senders >= 5 AND burst > 0.8 * lifetime
    RETURN DISTINCT dst.id AS id
""", database_="fraud-blind")
open("flagged.txt", "w").write("\n".join(r["id"] for r in records))
```

```bash
graphfaker evaluate ./bank --accounts flagged.txt
```

```
level          precision  recall     f1     tp     fp     fn
account            0.667   0.033  0.063      4      2    117
transaction        0.000   0.000  0.000      0      0    246
pattern            0.000   0.000  0.000      0      0     22
```

The account row is the same 66.7% / 3.3% the Cypher tail computed, which is a useful cross-check on both. The pattern row is zero because a pattern counts as found only when *every* one of its accounts is flagged, and `--ring-threshold 0.5` scores a ring as caught at half its accounts. Transactions are zero because this rule flags accounts and never names a transaction, so pass `--transactions` as well to score that level.

(If you prefer the shell, `cypher-shell` ships inside the Neo4j install rather than on your PATH; Neo4j Desktop puts it under the DBMS's `bin/`.)

## Other domains

Nothing in the loader is specific to fraud. The social graph has 17 relationship types, most of them carrying no properties at all:

```bash
graphfaker generate social --total-nodes 20000 --total-edges 120000 --seed 11 --out ./people
graphfaker load neo4j ./people --database social --create
```

```
loaded into database 'social' in 5.6s (25,208 rows/s)
  nodes     20,000  Event=2000, Organization=3000, Person=10000, Place=4000, Product=1000
  edges    122,099  ATTENDED=5291, BORN_IN=6755, COLLEAGUES=19186, FRIENDS_WITH=19237, ...
  truth         12  Community=12

PASS: 168/168 checks on database 'social'
```

`truth/community.parquet` becomes `(:Community)` nodes carrying the parameters each community was drawn with, and every `Person` keeps a `community` property, so the generated structure can be compared against whatever a community-detection algorithm finds.

Doing that comparison is also how you find out what the generator actually produced, which is the point of loading a dataset into a database you can ask questions of:

```cypher
MATCH (a:Person)-[r]->(b:Person)
WITH type(r) AS rel, count(*) AS total,
     sum(CASE WHEN a.community <> b.community THEN 1 ELSE 0 END) AS crossing
RETURN rel, total, crossing, round(1000.0 * crossing / total) / 10 AS crossing_pct
ORDER BY rel
```

```
COLLEAGUES     19186  0  0.0
FRIENDS_WITH   19237  0  0.0
MENTORS         9577  0  0.0
```

Not one person-to-person edge crosses a community boundary: the twelve communities are twelve disconnected islands. That is higher modularity than any real social network, where weak ties between groups are what make the network one network. It also makes the graph unsuitable as a community-detection benchmark, since any algorithm scores perfectly. It follows from `SocialTopology`: candidates are drawn from the source's own group 75% of the time and then the *highest-affinity* one wins, and since `group_affinity` (1.0) always beats `prominence_affinity` (0.45), a same-group candidate wins whenever one is in the sample. "Mostly local" becomes "always local". Tracked separately from this page.

## From Python

```python
from graphfaker.domains import get
from graphfaker.sinks import Target, load_tables, verify_tables

run = get("fraud").run(seed=42, scale=0.01, hardness="medium")
target = Target(uri="neo4j://127.0.0.1:7687", password="...", database="fraud")

report = load_tables(run.tables, target, run.truth, wipe_first=True)
print(report.summary())

result = verify_tables(run.tables, target, run.truth)
assert result.ok, result.summary()
```

`load_directory` and `verify_directory` take a dataset directory instead of a run. Pass `driver=` to either to reuse a connection you already have.

Generating and loading in one step, for when you do not need the Parquet:

```bash
NEO4J_DATABASE=fraud graphfaker fraud --scale 0.01 --seed 42 --out ./bank --sink neo4j
```

## Troubleshooting

**`'fraud_data' is not a usable Neo4j database name`**. Neo4j allows letters, digits, dots and dashes, and no underscores. `fraud-data` works. Checked before the round trip, since naming the database after the output directory is the natural thing to do.

**`could not create database`**. `--create` needs Enterprise or Aura. On Community there is one database; use `--database neo4j`.

**`database 'fraud' already holds 255,772 nodes`**. This is intentional. `--wipe` to replace it, or pick another `--database`.

**A load that slows down or the server runs out of heap**. Lower `--batch-size`. The default 10,000 fits comfortably in a 1G heap at these widths; a schema with wide string properties may want 2,000.

**`verify` fails only on `structure/labels`**. A label left over from a previous dataset still has nodes in it. `--wipe` and reload.

**Queries that were fast are suddenly slow**. Check the constraints survived: `SHOW CONSTRAINTS`. Relationship loading matches endpoints by `id`, and without the constraint-backed index every row is a scan.

## Graph Data Science

The queries above are deliberately plain Cypher: they run on Community, on Aura Free, and on a Desktop instance with no plugins. Graph algorithms (PageRank, Louvain, weakly connected components, node similarity) need the [Graph Data Science](https://neo4j.com/docs/graph-data-science/current/installation/) plugin.

Install it from Neo4j Desktop's **Plugins** tab for the DBMS, which picks a compatible version and writes the config, or drop the matching jar in `plugins/` and add `dbms.security.procedures.unrestricted=gds.*` to `neo4j.conf`. Either way the DBMS restarts. GDS follows the same calendar versioning as the database, so Neo4j 2026.08 wants GDS 2026.08; the [compatibility matrix](https://neo4j.com/docs/graph-data-science/current/installation/supported-neo4j-versions/) has the rest.

Project the transfer network once, then run algorithms over the projection. Pin `concurrency: 1` if you want the same answer twice: Louvain starts from a random assignment and PageRank's float summation depends on how work is split across threads, so without it the numbers move on every run.

```cypher
CALL gds.graph.project('transfers', 'Account', {TRANSFERS: {properties: 'amount'}})
```

Measured on the `--scale 0.01` bank, 100,000 accounts and 247,336 transfers, scored against the truth subgraph the same way as the Cypher rules:

| algorithm | result | verdict |
|---|---|---|
| weakly connected components | one component of 75,240 accounts, then a tail of 2s and 3s. **100% of patterns, fraud and decoy alike, sit inside a single component** | no discriminating power at all |
| PageRank, amount-weighted, top 200 | 3 true positives: 1.5% precision, 2.5% recall | worse than a one-line Cypher rule |
| Louvain | modularity 0.469 over 27,864 communities. Fraud rings place **2.58 accounts per community against the decoys' 1.05** | real signal, but a feature rather than a detector |

```cypher
// WCC: is a ring distinguishable by which component it lives in? (No.)
CALL gds.wcc.stream('transfers') YIELD nodeId, componentId
WITH gds.util.asNode(nodeId) AS a, componentId
MATCH (a)-[:IN_PATTERN]->(p:Pattern)
WITH p.id AS pattern, p.is_fraud AS is_fraud, count(DISTINCT componentId) AS components
RETURN is_fraud, count(*) AS patterns,
       round(100.0 * sum(CASE WHEN components = 1 THEN 1 ELSE 0 END) / count(*)) AS pct_one_component
```

```cypher
// PageRank: collectors should be structurally important. They are not.
CALL gds.pageRank.stream('transfers', {relationshipWeightProperty: 'amount', concurrency: 1})
YIELD nodeId, score
WITH gds.util.asNode(nodeId) AS a, score ORDER BY score DESC LIMIT 200
WITH collect(a.id) AS flagged
CALL () { MATCH (x:Account)-[:IN_PATTERN]->(p:Pattern) WHERE p.is_fraud
          RETURN collect(DISTINCT x.id) AS actual }
WITH flagged, actual, [x IN flagged WHERE x IN actual] AS tp
RETURN size(flagged) AS flagged, size(tp) AS tp,
       round(1000.0 * size(tp) / size(flagged)) / 10 AS precision_pct
```

```cypher
// Louvain: are a ring's accounts concentrated in one community? For fraud, yes.
CALL gds.louvain.write('transfers', {writeProperty: 'louvain', concurrency: 1})
YIELD communityCount, modularity RETURN communityCount, modularity
```

Two of the three most popular graph algorithms contributed nothing here, and that is worth knowing before building on them. The honest use of GDS on this dataset is as a feature source: write `pagerank` and `louvain` back onto the accounts, then let a model weigh them against everything else. `docs/notebooks/neo4j_fraud_analysis.ipynb` does exactly that, and both features end up carrying weight even though neither works alone.

Score every algorithm the same way as the Cypher rules. An algorithm that ranks well on a projection is not the same as an algorithm that finds fraud, and the truth subgraph is how you tell the difference.

## A worked analysis

[`docs/notebooks/neo4j_fraud_analysis.ipynb`](notebooks/neo4j_fraud_analysis.ipynb) takes this dataset through a full investigation: exploration, then rules, then graph algorithms, then a model, then a recommendation, with every step scored against the truth subgraph. It reads features from the blind database and labels from the truth one, so nothing leaks.

The short version of what it finds:

- Fraud accounts here receive from **fewer** distinct senders than the legitimate lookalikes (2.6 against 4.3) and nearly triple the value. Counting distinct senders, the most popular fan-in signal there is, points at the innocent group.
- All the Cypher rules together reach 16.5% recall. A gradient-boosted model's top 500 reaches 25.6%. Running both reaches 37.2%, because they fail on different typologies: the rules catch all four structuring accounts and none of the bipartite ones, the model the reverse.
- Logistic regression scores a ROC AUC of 0.82 against the model's 0.58 and puts **zero** fraud in the top 25, where the other puts 8. On a 0.1% base rate, ask for precision at a fixed queue length, not AUC.

Rebuild it with `python docs/notebooks/build_neo4j_analysis.py`, which needs both databases loaded.
