# Social

The social domain is the graph GraphFaker has generated from the start: people, places, organizations, events and products, with the relationships between them. Since 0.5 it is a `GraphSchema` preset, so every choice that used to be a constant is a field you can read, change and save.

```bash
graphfaker generate social --total-nodes 2000 --total-edges 12000 --seed 42 --out ./social
```

```python
from graphfaker import GraphFaker
from graphfaker.domains import social

schema = social.schema(total_nodes=2000, total_edges=12000, communities=8)
run = GraphFaker(seed=42).generate(schema)
G = run.to_networkx()
```

## What it contains

| node type | share | attributes |
|---|---|---|
| Person | 50% | name, age (around the community's mean), occupation, email, education level, skills, subtype, home place |
| Place | 20% | name, place type, prominence, population (tracks prominence), latitude, longitude |
| Organization | 15% | name, subtype, industry (follows the subtype), prominence, revenue, employee count (derived from WORKS_AT edges) |
| Event | 10% | name, event type, start date, duration, prominence |
| Product | 5% | name, category, prominence, price, release date |

Seven edge families share the edge budget: Person to Person (FRIENDS_WITH, COLLEAGUES, MENTORS), Person to Place (LIVES_IN, VISITED, BORN_IN), Person to Organization (WORKS_AT, STUDIED_AT, OWNS), Organization to Place, Person to Event, Organization to Product, Person to Product. LIVES_IN, BORN_IN and HEADQUARTERED_IN are functional: a node has at most one of each.

## Options

| option | default | meaning |
|---|---|---|
| `total_nodes` | 100 | split across the five types by the shares above |
| `total_edges` | 1000 | the edge budget; the result matches it to within 10% |
| `communities` | about one per 25 nodes | the number of latent groups; ages, education and regions cluster by community and people mostly connect within theirs |
| `topology` | `realistic` | `uniform` connects nodes at random and exists only to show the difference |

## What makes it realistic

One latent factor, `community`, drives both attributes and structure: each community has a mean age, a preferred education level and a region, attribute samplers reference them, and the topology model prefers same-community partners. Edges form by preferential attachment (hubs), triadic closure (clustering) and homophily (communities you can recover). Measured on 600 nodes and 2,400 edges: degree Gini 0.45, average clustering 0.18 against a random baseline of 0.01, modularity 0.72, age assortativity 0.81. The mechanism is described in [How generation works](../how-it-works.md); `graphfaker.metrics.compare_topology` reproduces the numbers.

## Ground truth

`run.truth["community"]` holds each community's parameters, and every node carries its `community`. That is the answer key for community-detection benchmarks.
