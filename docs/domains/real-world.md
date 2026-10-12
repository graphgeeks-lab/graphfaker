# Real-world networks

Sometimes the right substrate is real. GraphFaker loads four real-world sources as NetworkX graphs. They are loaded, not generated, so there is no seed and no ground truth, and they export to the same formats as the generated datasets.

## OpenStreetMap road networks

Road, walking or cycling networks by place name, address or bounding box, through OSMnx, projected to UTM.

```bash
graphfaker gen --fetcher osm --place "Berlin, Germany" --network-type drive --export berlin.graphml
```

```python
from graphfaker import GraphFaker

G = GraphFaker().generate_graph(source="osm", place="Berlin, Germany", network_type="drive")
```

The [OSM quick start notebook](../notebooks/osm_quickstart.ipynb) walks through fetching, inspecting and exporting a network.

## Flight networks

Airlines, airports and flight legs from the US Bureau of Transportation Statistics on-time data, for a month or a date range, with cancellation and delay flags.

```bash
graphfaker gen --fetcher flights --country "United States" --year 2024 --month 1 --export flights.graphml
```

```python
G = GraphFaker().generate_graph(source="flights", country="United States", year=2024, month=1)
```

The fetcher downloads with TLS verification enabled. If your system fails to validate the BTS certificate chain, set `GRAPHFAKER_INSECURE_TLS=1` to opt out; this logs a warning and means the downloaded data is no longer authenticated.

## Wikipedia pages

`WikiFetcher` returns a page's title, summary, content, sections, links and references as JSON, for building your own knowledge graph or a retrieval pipeline.

```python
from graphfaker import WikiFetcher

page = WikiFetcher.fetch_page("Graph database")
WikiFetcher.export_page_json(page, "graph_database.json")
```

## A company register

```bash
graphfaker gen --fetcher senzing --path register.jsonl --export register.graphml
```

Reads a register in [Senzing's](https://senzing.com) entity resolution format: one JSON object per line, with a `DATA_SOURCE`, a `RECORD_ID` and a list of `FEATURES`. Relationships are pointers rather than edges, so a record says "I am the thing with key K" (`REL_ANCHOR_KEY`) or "I belong to the thing with key K, as a branch, a headquarters, an officer" (`REL_POINTER_KEY` with a role). The loader turns those into `BRANCH_OF`, `HEADQUARTERS_OF`, `SITE_OF`, `EXECUTIVE_AT` and `CONTACT_AT` edges between organisation and person nodes.

`--path` can be one file, a directory, or a zip, because a register of any size arrives sharded. The national open data export is 2,493 JSONL files in three directories inside one archive: 87 million companies, 141 million locations and 140 million people, 158 GB unpacked. Everything is streamed, nothing is unpacked to disk, and the company shards are read first so a pointer resolves the moment its anchor is known. Which shards those are is decided by reading one line of each, not by their names, because in that archive every location shard is called `bq_organization_locations_...`.

### Cutting a national export down to a graph

An archive that size will not become one graph: resolving its pointers means holding 87 million anchors. Two options, and they answer different questions.

```bash
graphfaker register ODO_SENZING.zip --limit 500000
```

`graphfaker register` streams a register and reports what is in it: record types, pointer roles, the attributes on each kind of record and how often they are populated, and a list of what a load would not keep. It holds nothing, and a `--limit` is spread across the shards rather than spent on the first one, so half a million records out of 330 million answers the format question in half a minute. Run it before a load, not after.

```bash
graphfaker gen --fetcher senzing --path ODO_SENZING.zip --state NV --city "Las Vegas" --export vegas.graphml
```

`--state` and `--city` keep the companies registered in one place, together with their locations and their officers wherever those live: an officer of a Nevada company who lives in California is on that board. Both are repeatable, both are matched case-insensitively, and the pass is still over the whole archive, so budget hours rather than minutes for a national file.

### Into a database

A loaded register goes where a generated dataset goes. `--out` writes it as node and edge tables, one frame per record type and one per relationship, and `--sink` loads a database in the same step:

```bash
graphfaker register register.jsonl --out ./register --sink duckdb
graphfaker load ladybug ./register --db register.lbdb
graphfaker verify duckdb ./register --db ./register/graph.duckdb
```

The directory is `nodes/`, `edges/` and `manifest.json`, which is what every `graphfaker load` and `graphfaker verify` command already reads, so a register works with all of them: DuckDB with a `CREATE PROPERTY GRAPH` for SQL/PGQ pattern queries, LadybugDB, a live Neo4j, or the `neo4j-admin` import layout. Two things are missing by nature. There is no `truth/`, because nobody labelled a register, and the sinks that need labels or a seed (`pyg`, `gen-fraud-graph`) are not offered. And there is no `schema.yaml`: a schema in GraphFaker says how to *generate* something, with shares of an edge budget and a sampler per attribute, and a register was read rather than generated. The manifest's digest is of the shape that was loaded, so two loads of the same cut match and a different cut does not.

The whole Las Vegas register, 2,026,444 records, takes about 75 seconds to become tables and another 85 to become a DuckDB with 1,668,368 organisations, 358,076 people and 1,394,598 relationships. Then the question that makes a register worth having is a query:

```sql
SELECT ADDR_LINE1, ADDR_CITY, count(*) AS n FROM Organization
GROUP BY 1, 2 ORDER BY n DESC LIMIT 5;
-- 99,524  PO Box 27740, Las Vegas
-- 46,902  4730 S Fort Apache Rd , Las Vegas
-- 42,034  3225 Mcleod Dr , Las Vegas
-- 26,002  3225 McLeod Dr Ste 100, Las Vegas
```

The third and fourth rows are the same building, and that is the problem a resolution benchmark exists to be hard about.

### What the loader does with awkward records

Three things in the national export that a straight read would get wrong, all three reported by `graphfaker register`:

- **An attribute can appear twice.** 552 companies in every half million carry a second `NAME_ORG`, which is an alias, and an alias is the hardest part of resolving a register. The first value keeps the attribute's name and the rest go to `NAME_ORG_ALSO`, pipe-joined, up to five.
- **One property, two types.** `GEO_LATITUDE` and `GEO_LONGITUDE` are floats in the location shards and strings in the company shards. They are coerced to float, because an export with two types for one property is not loadable.
- **An attribute can be present and empty.** A person with no name carries `NAME_FULL: null`. Nulls are dropped rather than carried, so coverage counts mean what they say.

GraphFaker ships no register: you bring the file, and what you may do with it is between you and whoever published it. The two registers this project measured come from [OpenData.org](https://opendata.org/terms/), whose terms put its datasets under [CDLA-Permissive-2.0](https://cdla.dev/permissive-2-0/): computed output such as aggregate statistics carries no obligations under that licence, passing on the data itself carries one (ship the agreement text with it), and there is no attribution or share-alike requirement. A licence is not the whole question, though, because a register is also personal data and that is governed by something else; `benchmarks/README.md` has the longer note. Two reasons to want one. It is the reference for what a generated corporate graph should look like, which is what `benchmarks/realism/corporate.py` measures and what the [supply chain pack's addresses](supply-chain.md) are calibrated against. And it is a real entity resolution problem with no answer key, which is the gap a generated dataset fills.

A register is also real people: officers carry names, home addresses and social profiles. A loaded graph is personal data, and being public does not make it anonymous.

[A real register, and a generated one you can score](../notebooks/register_resolution.ipynb) is the notebook for this: it resolves companies on a register where nothing can be scored, then on a generated dataset with the same measured names and addresses where everything can. Name alone, blocked on the stem, scores 0.967 precision; adding the address drops it to 0.763 and matching on address alone to 0.03, which is what one address holding a hundred thousand companies does to a resolver. It runs without a register; point `GRAPHFAKER_REGISTER` at one and the last section runs too.

```{toctree}
:hidden:

../notebooks/osm_quickstart
```
