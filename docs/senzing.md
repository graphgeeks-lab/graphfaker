# Entity resolution datasets in Senzing format

Entity resolution has no public benchmark worth the name, and the reason is the data. The datasets where resolution matters are company registers, and a register has no answer key: nobody has labelled which of its rows are the same company. The datasets that do carry labels are usually clean enough that resolving them is free. So a resolver's score is either unmeasurable or uninteresting.

GraphFaker writes the other thing: records in the format Senzing takes, where the same company appears several times under several spellings and at addresses it shares with other companies, plus a separate file saying which records belong together.

```bash
graphfaker generate supply_chain --scale 0.01 --seed 42 --out ./chain --sink senzing
```

```text
./chain/senzing/records.jsonl      the dataset, with no answer in it
./chain/senzing/entities.parquet   record_id -> entity_id, the answer
```

Point Senzing, or anything else, at `records.jsonl`, and score whatever it returns against `entities.parquet`.

## What a record looks like

One JSON object per line: a `DATA_SOURCE`, a `RECORD_ID` and a list of `FEATURES`. Relationships are pointers rather than edges, which is how Senzing's input format expresses them, so the first record of a company carries `REL_ANCHOR_KEY` and the rest point at it with a role. Three records of one company:

| record | name | address | role |
|---|---|---|---|
| `sup_1` | `KELLY LLC` | 116 Erin Mountain | anchor |
| `sup_1-1` | `KELLY, LLC` | 116 Erin Mountain | `HEADQUARTERS` |
| `sup_1-2` | `KELLY` | 27861 Moore Branch | `BRANCH` |

```json
{"DATA_SOURCE": "GRAPHFAKER", "RECORD_ID": "sup_1-2", "FEATURES": [
  {"NAME_ORG": "KELLY", "NAME_TYPE": "PRIMARY"},
  {"RECORD_TYPE": "ORGANIZATION"},
  {"ADDR_LINE1": "27861 Moore Branch", "ADDR_CITY": "Port Catherine", "ADDR_TYPE": "BUSINESS"},
  {"REL_POINTER_DOMAIN": "GF", "REL_POINTER_KEY": 3, "REL_POINTER_ROLE": "BRANCH"}
]}
```

Nothing in `records.jsonl` names the entity. That is the point of the second file: the dataset you hand a resolver has to be the dataset without the answer, and a column nobody meant to read is how an answer leaks.

## Why it is hard, and in which directions

Every hard part here was measured on a real register rather than invented, and the two references are in [benchmarks/realism](https://github.com/graphgeeks-lab/graphfaker/tree/main/benchmarks/realism).

- **Names vary across a company's records.** LLC and `L.L.C`, with the comma and without, sometimes with no legal form at all, at the rates a register writes them: LLC 53.9%, INC 20.5%, nothing 11.9%, `L.L.C` 2.6%. Half of all names put a comma before the suffix.
- **Addresses are shared between companies.** The curve comes from a register where the median address holds one organisation and the busiest holds 99,522. That is the registered agent, and it is why address is not a blocking key.
- **A company's own records are mostly at different addresses.** A branch being somewhere else is the reason a register holds a record for it, so requiring two records to agree on an address loses most of them.
- **Two different companies share a name about 1% of the time** once the legal form is stripped, which a register does at 1.02% and 1.37% in the two places it was measured. Some near-matches really are different companies, and the answer key knows which.

Scored with `graphfaker.resolve` on a dataset of 4,409 records for 2,207 companies:

| approach | precision | recall | F1 |
|---|---|---|---|
| name, blocked on the stem | 0.925 | 0.932 | 0.928 |
| name and address together | 0.955 | 0.119 | 0.211 |
| address alone | 0.058 | 0.118 | 0.078 |

The name gets you most of the way. Adding the address takes precision up and recall through the floor, because a company's records sit at different addresses. Matching on the address alone fails in both directions at once: it misses a company's own records and merges other companies that share a door. A generated dataset where every company had its own unique address and name would have reported all three approaches as a success.

## People, and why they are the harder half

A register has no person entity. It has a row saying "this person is a contact at that company", so somebody attached to three companies is three rows, written differently in each, and nothing in the data links them to each other. Joining them up is the resolution problem in its purest form: the pointers rebuild every company's own records, and they cannot rebuild a person's.

| record | name | parts | address | points at |
|---|---|---|---|---|
| `per_49` | `WILLIAM BARTON` | | 84412 Waters Manors | key 1618 as `Contact` |
| `per_49-1` | `W BARTON` | | | key 1087 as `Contact` |
| `per_49-2` | `BARTON, WILLIAM` | | 84412 Waters Manors | key 2892 as `Executive` |
| `per_49-3` | `WILLIAM BARTON` | | | key 1890 as `Executive` |

Three measured facts shape this, and the first is the one a generator usually gets wrong by not thinking about it:

- **Most companies have nobody.** 4.5% of a register's companies have a single person attached: 46,201 of Nevada's 1,033,773 and 27,727 of the Las Vegas file's 631,846. Board density is a distribution with a floor of zero.
- **The ones that do have a crowd.** Among companies with anybody, the median is 1, the 90th percentile 14, the 99th 151, and the busiest holds 68,945, which is a tenth of every person-company link in the register on its own. Those are filing agents, and they are why "shares a director" is not evidence by itself.
- **The name parts are mostly missing.** A register records `NAME_FIRST` and `NAME_LAST` separately on 23% of person rows and a home address on 75%. A resolver with the parts has an easier job than one without, and real data mostly withholds them.

The variations are conventions rather than corruption: the surname leading, an initial for the first name, a middle initial where there is a middle name. There are no typos here, deliberately, because synthetic error is far easier than real error and a benchmark built on it measures its own noise model.

## Scoring a resolver

`entities.parquet` is `record_id`, `entity_id` and `node_type`. `read_gold` turns it into the clustering shape that `evaluate_clusters` takes, leaving out companies with a single record, since a resolver is not credited for failing to merge something that appears once.

```python
from graphfaker.resolve import evaluate_clusters
from graphfaker.sinks.senzing import read_gold

gold = read_gold("./chain/senzing")
scores = evaluate_clusters(my_resolver_output, gold)
print(scores["pairwise_precision"], scores["b3_f1"])
```

`my_resolver_output` is a list of clusters of record ids, or a `{record_id: cluster_id}` mapping. Both pairwise and B-cubed precision, recall and F1 come back.

## In Python

```python
from graphfaker.domains.supply_chain import generate
from graphfaker.sinks.senzing import write_senzing

run = generate(scale=0.01, seed=42)
export = write_senzing(run, "./chain/senzing")
print(export.summary())
# senzing: 4,409 records for 2,207 entities (1,202 with more than one record,
# 1,833 spelled differently, 1,795 at another address); answer in entities.parquet
```

`write_senzing` takes `seed` to vary the export independently of the dataset, and `data_source` to change the `DATA_SOURCE` on every record. How many records a company gets is drawn from the sites-per-organisation distribution measured on the references: median one, 90th percentile three, 99th five.

## Six commands: generate it, load it, ask it something

The export reads back through GraphFaker's own register loader, so a generated dataset can go into a graph database and be queried like a real one. Every output below is from this run.

```bash
# 1. generate, and write the Senzing records beside the Parquet
graphfaker generate supply_chain --scale 0.004 --seed 42 --out ./chain --sink senzing

# 2. see what is in the records before loading them
graphfaker register ./chain/senzing/records.jsonl --limit 5000

# 3. load the records into LadybugDB
graphfaker register ./chain/senzing/records.jsonl --out ./records --sink ladybug
```

Step 3 takes about two seconds and says what it did:

```text
senzing: 5833 records (0 skipped), 2207 anchors, 3626 edges, 0 pointers with no anchor
wrote ./records (Organization=4,397, Person=1,436)
ladybug: loaded ./records/graph.lbdb in 1.5s
```

Now the queries. `graphfaker` ships the driver with the `ladybug` extra, and these run in a Python shell or any LadybugDB client:

```python
from graphfaker.sinks.ladybug import connect

conn = connect("./records/graph.lbdb")
rows = conn.execute("MATCH (n) RETURN label(n) AS kind, count(*) AS records")
while rows.has_next():
    print(rows.get_next())
```

**4. Who is the registered agent.** One address, many companies, which is the thing that makes address useless as a key.

```cypher
MATCH (o:Organization)
WHERE o.ADDR_LINE1 IS NOT NULL
RETURN o.ADDR_LINE1 AS address, o.ADDR_CITY AS city, count(*) AS records
ORDER BY records DESC LIMIT 5
```

```text
554 Miller Extension Suite 702   Pattersonfort      81
4250 Katrina Ville               North Kristaport   39
9312 Gomez Plains Apt. 629       South Vanessa      30
```

**5. One company, as a register holds it.** Take whichever company has the most filed against it, and show them all.

```cypher
MATCH (r:Organization)-[]->(c:Organization)
WITH c, count(*) AS filings
ORDER BY filings DESC LIMIT 1
MATCH (r:Organization)-[e]->(c)
RETURN r.id AS record, r.NAME_ORG AS filed_as, label(e) AS role, r.ADDR_LINE1 AS at
```

```text
cst_1994-7   PETERSON PAULFURT GROUP, INC            BRANCH_OF         143 Danny Squares
cst_1994-6   PETERSON PAULFURT GROUP INC             SITE_OF           96479 Thompson Passage
cst_1994-5   PETERSON PAULFURT GROUP, INC.           BRANCH_OF         665 John Mount
cst_1994-4   PETERSON PAULFURT GROUP INC.            SITE_OF           49212 James Meadow Suite 038
cst_1994-3   PETERSON PAULFURT GROUP                 BRANCH_OF         96479 Thompson Passage
cst_1994-2   PETERSON PAULFURT GROUP INCORPORATED    BRANCH_OF         07992 Jay Corners
cst_1994-1   PETERSON PAULFURT GROUP, INCORPORATED   HEADQUARTERS_OF   9603 Kimberly Motorway
```

Seven records, seven spellings of one name, seven addresses. That is one company, and the answer key says so; nothing in the records does.

**6. The trap, counted.** Pairs of records at the same address that are *not* the same company.

```cypher
MATCH (a:Organization), (b:Organization)
WHERE a.ADDR_LINE1 = b.ADDR_LINE1 AND a.id < b.id AND a.NAME_ORG <> b.NAME_ORG
RETURN a.ADDR_LINE1 AS address, count(*) AS wrong_pairs
ORDER BY wrong_pairs DESC LIMIT 3
```

```text
554 Miller Extension Suite 702   3240
4250 Katrina Ville                741
9312 Gomez Plains Apt. 629        434
```

3,240 wrong pairs from one address. Block on address and that is the work a resolver has to do before it gets anything right.

One more worth running, because it is the signal people actually want from a register:

```cypher
MATCH (p:Person)-[:EXECUTIVE_AT]->(c:Organization)
WITH p.NAME_FULL AS person, count(*) AS boards
WHERE boards > 1
RETURN person, boards ORDER BY boards DESC LIMIT 5
```

```text
STEVEN PAGE      4
HEATHER ROGERS   3
JO ROACH         2
```

Those are people on several boards. In a register they are several rows written several ways, and whether they are one person is exactly what is being asked.

### Or take it somewhere else

```bash
graphfaker gen --fetcher senzing --path ./chain/senzing/records.jsonl --export records.graphml
```

5,833 nodes and 3,626 edges as GraphML, about 3 MB, for Gephi, Cytoscape or igraph. `--format csv|neo4j-csv|cypher|opencypher|gql` writes the other single-file forms, and `graphfaker register ... --sink duckdb` loads it into DuckDB with a property graph for SQL/PGQ instead.

## Reading a register you have

The same commands work on somebody else's register. These outputs are from the Las Vegas open data file, `opendata-lasvegas.jsonl`, 981 MB and 2,026,444 records in one JSONL file.

Look before you load. This reads 200,000 records in 25 seconds and keeps nothing:

```bash
graphfaker register opendata-lasvegas.jsonl --limit 200000
```

```text
register: opendata-lasvegas.jsonl
shards: 1 read of 1  (anchor 1)
records: 200,000  anchors: 200,000  pointers: 0

record types: ORGANIZATION 200,000
datasets:     COMPANY 200,000

attributes on ORGANIZATION records (200,000)
  NAME_ORG                      200,728  100.4%
  ADDR_CITY                     200,000  100.0%
  GEO_LATITUDE                  200,000  100.0%
  ADDR_POSTAL_CODE              199,312   99.7%
  ADDR_LINE1                    198,942   99.5%
  PLACEKEY                       84,046   42.0%
  WEBSITE_ADDRESS                17,623    8.8%
```

`NAME_ORG` over 100% is the alias problem showing up in the report: some records carry the name twice.

Then load the whole file:

```bash
graphfaker register opendata-lasvegas.jsonl --out ./lasvegas --sink ladybug
```

```text
senzing: 2026444 records (0 skipped), 631846 anchors, 1394598 edges, 0 pointers with no anchor
wrote ./lasvegas (Organization=1,668,368, Person=358,076)
ladybug: loaded ./lasvegas/graph.lbdb in 36.2s
```

Just under four minutes in total, most of it reading the gigabyte. Now ask it things:

```cypher
MATCH ()-[e]->() RETURN label(e) AS relationship, count(*) AS edges ORDER BY edges DESC
```

```text
SITE_OF           819108
CONTACT_AT        265577
HEADQUARTERS_OF   159221
EXECUTIVE_AT       85331
BRANCH_OF          65361
```

**A company is not a record.** 1,668,368 organisation records are 631,846 companies and their locations. A company is a record that nothing was filed from:

```cypher
MATCH (c:Organization)
WHERE NOT (c)-[]->(:Organization)
RETURN count(*) AS companies
```

```text
631846
```

That distinction is the one to get right before computing anything. Counted per record, a name looks shared 86.7% of the time; counted per company, 1%.

**The registered agent.** One address, a hundred thousand companies:

```cypher
MATCH (o:Organization)
WHERE o.ADDR_LINE1 IS NOT NULL
RETURN o.ADDR_LINE1 AS address, o.ADDR_CITY AS city, count(*) AS records
ORDER BY records DESC LIMIT 5
```

```text
PO Box 27740             Las Vegas   99524
4730 S Fort Apache Rd    Las Vegas   46902
3225 Mcleod Dr           Las Vegas   42034
3225 McLeod Dr Ste 100   Las Vegas   26002
500 N Rainbow Blvd       Las Vegas   19584
```

Rows three and four are the same building, which is the variant problem with no generator involved. The count differs slightly from the 99,522 in `benchmarks/realism` because that measurement keys on the whole address and this query keys on the street line alone.

**Most companies have nobody attached.**

```cypher
MATCH (c:Organization)
WHERE NOT (c)-[]->(:Organization)
OPTIONAL MATCH (p:Person)-[:CONTACT_AT|EXECUTIVE_AT]->(c)
WITH c, count(p) AS people
RETURN CASE WHEN people = 0 THEN 'nobody' ELSE 'at least one' END AS attached,
       count(*) AS companies
```

```text
nobody          604147
at least one     27699
```

4% of companies have anybody at all, and among those the mean is 12.7 with a busiest of 11,351. That is the distribution `graphfaker.engine.people` reproduces, and the reason a generated dataset that gives every company a board is wrong twice over.

### Where it does not fit

A register also files a few thousand *people* as a company's location, so `SITE_OF` and `HEADQUARTERS_OF` each run between two different pairs of node types:

```cypher
MATCH (p:Person)-[e]->(:Organization)
RETURN label(e) AS relationship, count(*) AS edges ORDER BY edges DESC
```

```text
CONTACT_AT        265577
EXECUTIVE_AT       85331
SITE_OF             5079
HEADQUARTERS_OF     2089
```

GraphFaker's own datasets never do that, and until 1.2.0 the LadybugDB sink declared one pair per relationship, so a real register loaded as Parquet and then failed on the database with "unable to find primary key value". The sink now declares every pair a relationship actually connects and loads each one separately.

### Other ways out

```bash
graphfaker register opendata-lasvegas.jsonl --out ./lasvegas --sink duckdb
graphfaker gen --fetcher senzing --path opendata-lasvegas.jsonl --export vegas.graphml
```

DuckDB gives the same tables with a property graph for SQL/PGQ. GraphML gives one file for Gephi or igraph, though two million nodes is a lot to lay out; `--limit` or `--state`/`--city` cut it down first.

### A register is personal data

The officers in that file are named people with addresses and social profiles. Being public does not make it anonymous, and a loaded copy is personal data wherever you put it. The outputs above are counts for that reason. What GraphFaker ships from this file is `benchmarks/realism/corporate-lasvegas.json`, which is aggregate statistics and nothing else; see [the licence note](https://github.com/graphgeeks-lab/graphfaker/tree/main/benchmarks#provenance-and-licence) for why that is the form that travels.

The pairing of a real register and a generated one is the subject of [a notebook](notebooks/register_resolution.ipynb): the same resolver on real records where nothing can be scored, and on generated records where everything can.

## What is not in it yet

- **Missing attributes on companies.** Person records withhold their name parts and their address at the measured rates, but a company record here carries every column it could. A register runs geo on 67% of records, LinkedIn on 33% and an LEI on 0.06%, and a pipeline tuned on complete data will not survive contact with that.
- **A person's own hierarchy.** People are attached to companies, and in a register they are also related to each other: the same household, the same address, successive filings. None of that is modelled.
- **Typos.** Deliberately. Synthetic corruption is known to be far easier than real-world error, so the variation here is limited to the spellings a register actually contains.
