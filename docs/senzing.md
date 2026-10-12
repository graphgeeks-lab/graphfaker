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

## Reading a register back

The same format goes the other way. [`graphfaker register`](domains/real-world.md) reads a register someone else published, including the national open data export, so the generated dataset and the real one can be put through the same pipeline. That pairing is the subject of [a notebook](notebooks/register_resolution.ipynb): the same resolver on real records where nothing can be scored, and on generated records where everything can.

## What is not in it yet

- **Missing attributes on companies.** Person records withhold their name parts and their address at the measured rates, but a company record here carries every column it could. A register runs geo on 67% of records, LinkedIn on 33% and an LEI on 0.06%, and a pipeline tuned on complete data will not survive contact with that.
- **A person's own hierarchy.** People are attached to companies, and in a register they are also related to each other: the same household, the same address, successive filings. None of that is modelled.
- **Typos.** Deliberately. Synthetic corruption is known to be far easier than real-world error, so the variation here is limited to the spellings a register actually contains.
