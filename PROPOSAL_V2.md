# GraphFaker v2 — Strategy Proposal

**Status:** Draft for discussion · **Date:** 2026-07-31 · **Branch:** `new_feature_branch`

Supersedes the framing in [`Proposal.md`](Proposal.md). All figures were measured on
2026-07-31 and are cited so they can be re-checked. This document went through an
adversarial review that **killed its own original recommendation**; §4 records that, and §0
states the revised position. The discarded thesis is kept in §3 because the reasoning for
discarding it is the most useful content here.

---

## 0. TL;DR

**Three findings, in order of confidence.**

1. **All four ideas currently on the table should be dropped.** Not narrowed — dropped. §2
   gives the evidence for each. This is the highest-confidence conclusion in the document.
2. **The attractive-looking alternative — "GraphFaker becomes the ground-truth substrate for
   evaluating graph systems" — also fails.** It survived first-principles reasoning and then
   died under evidence. Three independent kills, §4.
3. **What survives is small, and it is not a repositioning.** Ship a **deduplication
   capability upstream into an existing framework**, use corruption/ground-truth only as
   *internal test fixtures inside that PR*, and never ship it as the product. §6.

**The single sentence that reorganized this document:**

> Splink — 2,308★, actively developed, the exact use case — **built a synthetic
> linkage-data generator and then deleted it.** `splink_synthetic_data` is a 404, and
> [#110](https://github.com/moj-analytical-services/splink/issues/110) plus #292, #316, #632
> are all closed. They ship static CSVs with a `cluster` column instead.

That is not an unserved market. That is a market that was tried and rejected by the
best-positioned team in the world.

**Second sentence, nearly as damaging:** the people in acute pain about duplicate entities
never asked to *measure* them. In [microsoft/graphrag#1718](https://github.com/microsoft/graphrag/issues/1718)
— closed as "not planned," the flagship evidence for the whole thesis — **no commenter
mentions benchmarks, ground truth, or evaluation.** They asked for the bug to stop. Revealed
preference on solution-vs-measurement: Splink 2,308★ and Zingg 1.2k★ are *solutions*; the
best-maintained synthetic ER data generator is **2★**. A ~600× ratio.

**On "lucrative":** no, not directly, and §7 does the arithmetic rather than gesturing.
Direct revenue >$50k/yr: **5–10%**. Reputational return converting to consulting or
employment: **~70%**. If direct revenue is the real goal, it lives in the application layer,
and that is a different project than this one.

---

## 1. Honest audit: what GraphFaker is today

### The code

| Component | LOC | Assessment |
|---|---|---|
| `core.py` faker generator | 382 | **Demo, not a product.** See below. |
| `fetchers/flights.py` | 336 | The only real engineering. BTS + carrier lookup → typed heterogeneous graph. |
| `fetchers/osm.py` | 120 | Thin pass-through to OSMnx. No moat — users would call OSMnx directly. |
| `fetchers/wiki.py` | 82 | Orphan. Emits raw text, no graph. Disconnected from the story. |
| `cli.py` | 123 | Works, with the bug below. |

**The synthetic generator is not "realistic."**
[`core.py:generate_edges`](graphfaker/core.py) picks *both* endpoints with uniform
`random.choice` inside each type-pair. That is Erdős–Rényi attachment: Poisson degree
distribution, no preferential attachment, no clustering, no community structure, and no
correlation between attributes and topology (an organization's `industry` is literally
`fake.job()`). This matters beyond aesthetics — it is the first reason the ground-truth
thesis fails (§4, Front 2).

**Three verified defects:**

1. **No `seed` parameter anywhere** (`grep -rn seed graphfaker/` → no matches). Output is not
   reproducible. Table stakes for anything calling itself a test-data library.
2. [`cli.py:5`](graphfaker/cli.py) is `from venv import logger` — the CLI logs through the
   **standard library `venv` module's** logger by accident.
3. [`flights.py:128`](graphfaker/fetchers/flights.py) and
   [`:187`](graphfaker/fetchers/flights.py) pass `verify=False` to `requests.get`. TLS
   verification disabled — disqualifying inside any enterprise, and a genuine MITM exposure.

### The traction, and the throughput constraint

- **36 stars, 4 forks. 89 PyPI downloads in the last 30 days** (lifetime peak 194/mo).
  Last commit 2025-08-26 — **11 months idle.** 11 open issues, 1 open PR.
- One effective maintainer (58 of 71 commits), 2 occasional contributors, no funding.
- GraphGeeks parent org: ~1,200 Discord members (2.2× YoY), YouTube 2k→24k views, flagship
  in-person event **capped at 75 attendees**.

**The uncomfortable observation, and it is the one that should drive planning:** the README
already publishes a roadmap — GraphML/JSON-LD/RDF export, Neo4j/Kuzu/TigerGraph
integration, million-node scale, LLM-powered fetching. **None of it shipped.** The binding
constraint is not strategy, it is throughput. A plan that is *more* ambitious than the
unshipped one is not a pivot; it is an escape from the real problem. Any credible plan must
be *smaller* than what has already failed to ship.

### What is actually an asset

1. **The name.** "Faker, but for graphs" explains itself and owns the PyPI slot. Worth more
   than any code in the repo.
2. **The GraphGeeks channel.** ~1,200 graph practitioners reachable in a day.
3. **The flights pipeline** — real work, a good teaching dataset.
4. Everything else, including the synthetic generator, is replaceable.

**The defensible asset is positioning plus audience, not code.**

---

## 2. The four candidate ideas — and why each one dies

### (a) Hosted notebook for schema-less graphs, "like DSPy" — **DROP**

A distribution tactic mistaken for a product. The DSPy analogy doesn't parse (DSPy is a
programming model, not a notebook). No unique leverage, trivially cloneable, and hosting is
a recurring cost against a $0 budget.

*Salvageable kernel:* "schema-less" really means *let users bring their own ontology instead
of hardcoded module-level constants.* A legitimate library feature — a Colab link, not a
service.

### (b) A v2 that validates/lints someone's knowledge graph — **DROP**

Validating *someone else's* graph requires *their* schema and expectations, which GraphFaker
does not have — so the generator asset provides **zero** leverage. And property graphs have
no dominant constraint standard (SHACL is RDF-only; a 2026 survey of 94 practitioners found
22 explicitly asking for property-graph support that does not exist). This means inventing a
spec language solo: a multi-year standards fight.

### (c) Lightweight graph observability / "GraphOps" — **DROP, hardest**

**The discipline does not exist.** "Graph observability," "graph data quality," and "graph
drift detection" are not terms — no vendor category, no analyst coverage, no conference
track. Great Expectations, Soda, Elementary, and Monte Carlo have **zero** graph connectors.
There is **no dbt adapter for Neo4j or Cypher** on dbt's community or trusted lists. Public
pain is single-digit forum threads. Zero job postings mention graph testing or observability.

No startup died attempting this **because nobody tried.** And the closest analogue market is
a warning: Gartner sized *all* of data observability at **$346M in 2024 against $1B+ of VC
invested.** Great Expectations broke up (GX Cloud → FICO, shut down 2026-06-01; GX Core
handed to Fivetran). Monte Carlo has raised nothing since 2022.

The vacuum is far more likely absence of demand than unmet demand. And it is a platform play
— integrations per graph DB, dashboards, on-call ergonomics — requiring exactly the
resources this project lacks.

### (d) A graph-engineering workflow on LangGraph — **DROP**

Maximally crowded (LightRAG 38.4k★, Microsoft GraphRAG 35.1k★, Cognee 29.6k★, Graphiti
29.4k★, LlamaIndex, neo4j-graphrag), tightly coupled to a framework that may not exist in
its current form in 18 months, discards the existing codebase, and the target user is
undefined. Trend-surfing, not asset-compounding.

---

## 3. The thesis that looked right (and was tested to destruction)

Stated fairly, because it is genuinely appealing:

> A generator is the only artifact in the graph stack with **gold labels for free, because it
> created them.** GraphRAG quality, entity-resolution quality, and KG validation are all
> *measurement* problems blocked on ground truth that real graphs never carry. So GraphFaker
> should become the ground-truth substrate: `corrupt(G, spec) -> (G_dirty, gold_labels)`,
> then paired text↔graph corpora, then `score(pipeline_fn, corpus) -> report`.

Supporting arguments that are still true: **contamination immunity** (a graph generated this
morning cannot be in any LLM's training data, unlike HotpotQA or Wikidata subsets), and
**difficulty as a dial** (sweep duplicate rate or hop depth and plot a degradation curve —
no fixed dataset allows that).

There was also a real-looking gap. Every flavour of graph ground truth exists **in
isolation**: LFR/ABCD for communities (topology only), SynthKGQA for QA paths (derives from
Wikidata, doesn't synthesize the graph), TravelFraudBench for fraud rings (single vertical),
FEBRL/GeCo for duplicates (**flat record tables, not graphs**), PyGraft for schema
consistency (**713★, rigorous OWL reasoning, but no attribute values**), LDBC SNB for scale
(Scala/Spark, emits files not objects). Nothing combined typed graph + realistic attributes
+ gold labels + in-process NetworkX.

And the go-to-market looked sharp: don't sell a benchmark to end users, sell fixtures to the
~6 framework maintainers with public, dated, unfixed dedup bugs — MS GraphRAG #1718 (closed
"not planned," outsiders still re-patching 16 months later via #2339/#2411), Neo4j shipping
`perform_entity_resolution` **off by default** while publicly saying it isn't happy with it,
Cognee's three-way dedup hackathon in Jun 2026 (#3627/#3628/#3629, all open), LangChain
`LLMGraphTransformer` producing a typed node on one run and an untyped node on the next
([#26614](https://github.com/langchain-ai/langchain/issues/26614)), `PropertyGraphIndex`
deduping against the store but not within a batch.

**All of that is true, and the thesis still fails.** §4.

---

## 4. Pressure test: what actually killed it

Five attacks were run. Three land decisively. Two were conceded by the adversary itself and
are recorded as *not* killing anything — kept because a pressure test that only reports hits
is worthless.

### KILL 1 — Splink built this and deleted it *(most lethal)*

- `moj-analytical-services/splink_synthetic_data` → **404**, absent from the org's repo list.
- [#110 "Generate artificial linkage data to benchmark splink performance"](https://github.com/moj-analytical-services/splink/issues/110)
  opened 2020-06-19, **closed**, punted to an external R package. #292, #316, #632: also closed.
- Splink today: **2,308★**, shipped 2026-07-31. It chose **static CSVs with a `cluster`
  column** (`fake_1000`: `unique_id,first_name,surname,dob,city,email,cluster`).

Corroborating: the canonical [Leipzig ER benchmark](https://dbs.uni-leipzig.de/research/projects/benchmark-datasets-for-entity-resolution)
**already used GeCo and DAPO to synthesize its duplicates** — then froze in 2019. Modern
Python GeCo ports: [geco3](https://github.com/T-Strojny/geco3) **2★**,
[GeCoWrapper](https://github.com/dobraczka/GeCoWrapper) **1★**,
[geco_data_generator_corruptor](https://github.com/sashaostr/geco_data_generator_corruptor) **0★**.

Graph-shaped ER benchmark requests found across GitHub, Stack Overflow, r/dataengineering,
HN, five query variants: **zero.**

> ⚠️ **One caveat worth checking before fully accepting this.** A deleted repo is not
> definitionally "rejected" — it could have been consolidated into `splink_datasets` or
> dropped for maintenance load rather than lack of demand. That specific inference is the
> load-bearing part of the strongest kill, so it deserves ten minutes of verification (ask in
> the Splink discussions) before anyone acts on it. The four independently closed issues make
> the inference reasonable, not certain.

### KILL 2 — Ragas already ships Phase 2, at 400× the audience

[Ragas](https://github.com/explodinggradients/ragas) — **15.1k★** — already
[builds a knowledge graph from documents](https://docs.ragas.io/en/stable/concepts/test_data_generation/rag/)
(hierarchical nodes, entity-similarity relations), then runs `QuerySynthesizer` to emit
**single-hop and multi-hop** queries as `EvalSample (Query, Context, Reference)` with
ground-truth `reference` answers. That *is* the proposed text↔graph paired corpus with gold
labels, shipping today.

Phase 3 is occupied too:
[GraphRAG-Bench](https://github.com/GraphRAG-Bench/GraphRAG-Benchmark) — **471★**, active
2026-05-17, four difficulty tiers — and it uses **real** novel and medical corpora
*specifically because synthetic wouldn't be credible*.

### KILL 3 — Nobody in pain asked for measurement

[microsoft/graphrag#1718](https://github.com/microsoft/graphrag/issues/1718), the flagship
evidence: commenters discuss **the bug only**. No commenter mentions benchmarks, ground
truth, or evaluation. Solution-vs-measurement revealed preference: Splink **2,308★** and
Zingg **1.2k★** are solutions; the best synthetic-ER generator is **2★**. Cognee's dedup
hackathon signals they want an *implementation*, not a scoreboard.

A benchmark is a vitamin sold to people with a broken leg.

### KILL 4 — Synthetic corruption is ~100× too easy *(lands on fidelity, partially conceded)*

[Lam et al., IJPDS 9:1:18 (2024)](https://discovery.ucl.ac.uk/id/eprint/10194216/1/ijpds-09-2389.pdf):
real ALSPAC linkage missed-match rates **4.59% / 2.61% / 2.40%**. Under naive corruption —
guessed error rates, errors independent of attributes, no co-occurrence, i.e. *exactly what a
`corrupt(G, spec)` API does* — the same methods score **0.12% / 0.03% / 0.02%**.

[Alsadeeqi 2020](https://www.ros.hw.ac.uk/server/api/core/bitstreams/43d977dc-4acc-4b63-8f74-2e2cdbb50bec/content)
is worse: on real data all four string comparators returned **identical F=0.896091**, while
on corrupted synthetic data Jaro-Winkler "delivered the highest linkage." **The corruptor
manufactured a ranking the real data does not support.**

The multi-hop QA half is independently dead. [Min et al., ACL 2019](https://arxiv.org/abs/1906.02900):
single-hop BERT hits **67 F1** on HotpotQA. [MuSiQue](https://arxiv.org/abs/2108.00573):
"existing multihop benchmarks are known to be largely solvable via shortcuts." *Human-curated*
multi-hop benchmarks are shortcut-riddled; questions mechanically generated by traversing a
graph you built will be strictly worse — the generator's traversal *is* the shortcut.

Also: ER benchmarks are saturated —
[Primpeli & Bizer, CIKM 2020](https://www.uni-mannheim.de/media/Einrichtungen/dws/Files_Research/Web-based_Systems/pub/CIKM2020_Primpeli_Bizer.pdf):
"six benchmark tasks are perfectly solved by the baseline method (F1=1.00)."

**Concession recorded:** no paper was found showing matcher *rankings invert* between
synthetic and real data; Lam et al. preserve the top-line ordering. So the lethal form of
this attack is unproven. The provable form — absolute numbers inflated ~100×, and
fine-grained comparator differences that are artifacts of the corruption model — is still
enough to gut the value proposition. Lam et al. also names the only fix: model **error
co-occurrence and error/attribute dependence** — which requires real labeled data you don't
have. That is circular, and it is a research program, not 200 lines.

### KILL 5 — The unshipped roadmap *(execution, not strategy)*

Covered in §1. The README's existing modest roadmap never shipped; 11 months idle. Proposing
something harder is an escape fantasy. **The corruption *model*, not the API, is the whole
product** — and §4 Kill 4 shows the model is the expensive part.

### ATTACKS THAT FAILED — recorded honestly

- **PyGraft will not eat this.** 713★, 53 forks, **2 open issues**; "Add support for
  literals" is explicitly **LOW priority**;
  [#6](https://github.com/nicolas-hbt/pygraft/issues) asking about the next release has been
  open and unanswered since 2025-03-26. PyGraft is itself stalling. *But read the signal:* the
  713★ incumbent doesn't think attribute values are worth prioritizing, and its users aren't
  pushing.
- **The base-rate argument is unsupported.** No data exists on solo-maintainer OSS pivot
  success rates. The adversary withdrew it rather than invent a number. §1's throughput
  argument replaces it and is stronger because it's specific to this repo.
- **There is one live thread of real demand.** Splink
  [#2191 "Cluster evaluation — with ground truth data"](https://github.com/moj-analytical-services/splink/issues/2191)
  and [#2274](https://github.com/moj-analytical-services/splink/issues/2274) are **both
  open**. Note precisely what is wanted: better *scoring against labels users already have* —
  a metrics API, **not** a generator to manufacture labels. §5.

---

## 5. What survives

Not a substrate, not a corpus, not a harness. Roughly 300 lines, under a different name:

**Graph-aware cluster-evaluation metrics for NetworkX.** Given a predicted node clustering
and a gold clustering, compute pairwise and cluster-level precision/recall/F1, B-cubed, plus
split/merge diagnostics.

It survives for three reasons: it is the only piece with a **verified public request**
(Splink #2191/#2274, both open — and Splink is Spark/DuckDB-native, so a NetworkX-side
implementation isn't redundant); it takes labels the user **already has** rather than
manufacturing labels whose realism is indefensible; and it is immune to Kill 4 entirely —
**a metric cannot be too easy, only the data can.**

It is also not a business, not a pivot, and not an identity. It is a weekend PR, ideally
submitted to an existing library. **That the surviving fragment is too small to justify
repositioning a project around is itself the answer to the thesis.**

---

## 6. The revised plan

**Chosen objective: reputation and career.** Not revenue (§7 explains why that would be a
different project), and not stars. That choice sets the optimisation target: reputation comes
from artifacts *other people cite or use* — a merged PR in a large repo, a novel measurement,
a conference talk — not from a package you maintain alone.

The sequencing inverts the discarded thesis: **solution first, measurement never as the
product.**

### The forcing function

The binding constraint is throughput, not strategy (§1). Strategy documents do not fix that;
deadlines do. Two real ones sit ~3.5 months out:

- **NODES 2026** — Nov 12, free, virtual, CFP via sessionize
- **Connected Data London** — Nov 11–12, 10th anniversary

Work backwards from a November talk. Every step below yields an artifact, and each feeds the
next.

### Step 0 — Hygiene (½ day, unconditional)

See Step 1. It comes first because the measurement in Step 1 is worthless if made with a tool
that has no `seed` and disables TLS. Reproducibility is the credibility of the result, not
housekeeping.

### Step 1 — Publish the measurement nobody has published (2 weeks)

Generate one controlled corpus. Run it through five frameworks — Microsoft GraphRAG, LightRAG,
Cognee, Graphiti, LlamaIndex `PropertyGraphIndex`. **Count how many nodes each creates for
entities known to be singular.** Publish the table.

Why this specific experiment survives the §4 kills, which the discarded eval thesis did not:

- **No corruption model is involved.** You feed *clean* input containing one
  `Acme Corporation` and observe that a framework emitted five nodes. Kill 4 (Lam et al.,
  synthetic corruption ~100× too easy) is a critique of simulated *errors*; there are none
  here.
- **It is not answer-quality evaluation.** Ragas and GraphRAG-Bench measure generation
  quality. Per-framework duplicate rates on identical input are unoccupied, so Kill 2 does
  not apply.
- **It is diagnosis, not a benchmark product.** Kill 3 says nobody wants to buy a scoreboard.
  A published finding is not a product.

This is the one place the ground-truth property is genuinely load-bearing and unattackable:
you know what you put in, so you can count what came out.

### Step 2 — That post is the CFP abstract

Submit it. "I measured entity duplication across five GraphRAG frameworks" is accepted because
it is a number, not an opinion.

### Step 3 — Build the second act (weeks 3–8)

A talk that is only a diagnosis is a complaint. Ship `resolve()` so the arc becomes
*problem → measurement → fix*, then take it to Cognee as a PR. A merged PR in a 29k-star repo
is the highest reputation-per-hour trade available, and it is the talk's climax.

### Step 4 — November

Give the talk, holding a novel measurement, a working tool, and ideally a merged upstream
contribution — from roughly eight weeks of work.

### Step 1 detail — Hygiene (½ day, do regardless of everything else)

1. Remove `verify=False` from [`flights.py`](graphfaker/fetchers/flights.py) or fence it
   behind an explicit opt-in flag.
2. Fix `from venv import logger` at [`cli.py:5`](graphfaker/cli.py) and the stale
   `"Use 'random' or 'osm'"` error message.
3. Add `seed` to `generate_graph`, threaded through `random` and `Faker`.

These are unconditional. The library is published; #1 is a security defect and #3 is table
stakes.

### Step 2 — Ship a dedup fix upstream (the actual play)

1. **Target Cognee.** Its Jun 2026 dedup hackathon (#3627/#3628/#3629) is the only case where
   *both* demand and maintainer receptivity are already verified. Second choice: LightRAG or
   Graphiti (largest audiences). MS GraphRAG is the worst target — it closed its dedup issue
   "not planned."
2. Write a working entity-resolution pass for their KG pipeline as a real PR. **Do not build
   an ER engine from scratch** — Splink owns that (1.14M downloads/month). Wrap or borrow.
3. **Use GraphFaker inside that PR's tests** as the fixture proving the fix. The corruption
   code gets written — as a means, never as the product, and never load-bearing on realism
   claims.
4. If it merges, repeat on the next framework. Then, and only then, extract `graph-dedup` as
   a standalone library — with verified users on day one instead of a hopeful launch.
5. Write the GraphGeeks post about it. At ~1,200 members that is the one distribution asset
   that reliably works.

**Why this beats the thesis on every constraint:** it borrows distribution from a
15k–30k-star ecosystem instead of hoping for adoption at 36★; it is immune to Kill 4 because
a dedup implementation is judged by whether it fixes the user's graph, so synthetic realism
never becomes load-bearing; it fits the throughput constraint (one PR, not an 18-month
research program); it sits on the 600×-larger side of the solution/measurement ratio; and a
merged PR in a 15k-star repo delivers the reputational payoff — the thesis's actual stated
monetization — far faster than a benchmark nobody cites.

### Step 3 — Optional, opportunistic

Contribute the §5 cluster-evaluation metrics as a PR to an existing library. One weekend.
Don't launch it as a project.

### Standing kill list

Hosted notebook service · KG linter · GraphOps platform · LangGraph workflow · "ground-truth
substrate" positioning · public `corrupt()` API as a product · multi-hop QA generation ·
a GraphRAG eval harness.

### What to do about `graphfaker` itself

It becomes the **instrument**, which is a real role rather than a demotion. Keep the name and
the PyPI slot — the best assets here — and let it be a small, honest library that is credible
enough to make a measurement with. Fix the defects, keep the flights pipeline, add
`resolve()`, and delete the roadmap promises; the existing ones never shipped and unkept
promises read worse than a modest scope.

The story stops being "I maintain a synthetic graph library" and becomes "I found and fixed a
bug class in the GraphRAG ecosystem, and built the tooling that proves it." For a career in a
field with ~245 open roles at $150–280K, the second sentence is worth an order of magnitude
more.

### Shipped in this branch

- Seeding (`GraphFaker(seed=...)`, `generate_graph(..., seed=...)`, `reseed()`, `--seed`),
  without touching global `random` state.
- TLS verification restored by default; `GRAPHFAKER_INSECURE_TLS=1` opts out loudly.
- `tqdm` and `urllib3` declared — they were imported but missing from dependencies, so a clean
  install could fail on `import graphfaker`.
- CLI logger no longer resolves to the stdlib `venv` module's logger.
- `graphfaker.resolve`: `resolve_entities()`, `merge_clusters()`, `evaluate_clusters()`, and
  `GraphFaker.resolve()`.
- 45 new tests; the 3 CLI tests that were failing on `main` now pass.
- README stripped of unimplemented promises.

---

## 7. The "lucrative" question, answered with arithmetic

Where money actually flowed in graph/KG in 2025–26 (all verified):

- **Applications, not tooling.** Glean: $150M Series F at $7.2B, ~$300M ARR, on a
  permissions-aware knowledge graph. Palantir: FY2025 revenue $4.40B, US commercial +104%,
  sold as "Ontology." Quantexa: $175M Series F at $2.6B, $100M+ ARR, entity-resolution-as-a-product.
- **Graph infrastructure is being harvested, not scaled.** Kuzu archived 2025-10-10, team
  acqui-hired by Apple. Illumex → NVIDIA (~$60–75M on $13M raised). Linkurious → Nuix
  (≤€20M). GraphAware/Hume → Neo4j. TigerGraph → PE. Memgraph, Stardog, FalkorDB, ArangoDB
  financially frozen. Neo4j: $200M+ ARR at a $2B mark — but that was Nov 2024, no round
  since, and secondary marks are $1.1–1.2B.
- **The eval category out-raised the entire graph long tail:** ~$230M in 12 months
  (Braintrust $80M at $800M, LangChain $125M at $1.25B on $12–16M ARR, Arize $70M, Patronus
  $50M). A solo project cannot enter there — and note this cuts *against* the eval thesis,
  not for it.
- **Jobs are real, senior, and tiny.** Dice.com US, 2026-07-31: "knowledge graph" 245,
  Neo4j 244, GraphRAG 50, ontology engineer 15 — versus data engineer 3,027. $150–280K.
  LinkedIn's own *Skills on the Rise 2026* mentions none of: knowledge graph, ontology,
  semantic, graph, taxonomy.

| Outcome | Odds |
|---|---|
| Direct revenue > $50k/yr | **5–10%** |
| Vendor sponsorship of a neutral eval suite | **10–15%** (revised down from 20–30% — GraphRAG-Bench already occupies the neutral-benchmark slot with real data) |
| Reputational return → consulting / employment / talks | **~70%**, and Step 2 is the fastest route to it |

**Nobody in 2025–26 paid meaningfully for graph dev tools or benchmarks.** Plan for the
reputational return; treat revenue as upside. If direct revenue is the goal, it is in the
application layer — a different project.

---

## 8. GraphRAG deflation risk

Treat GraphRAG as a **customer segment, not an identity.** Entity resolution predates it by
decades and will outlive it — which is a further argument for Step 2 over the eval thesis.

The research does not support imminent deflation: arXiv GraphRAG papers went 13 (2024) → 91
(2025) → **114 in 2026 YTD**, and Gartner places knowledge graphs on the Slope of
Enlightenment heading to the Plateau of Productivity. But the center of gravity has already
moved once — from batch GraphRAG to incremental agent memory (Graphiti's downloads went 311K
→ **1.59M/month** Feb→Jul 2026 while Microsoft GraphRAG's stayed flat and its Azure solution
accelerator was archived). It will move again. Anything coupled tightly to a single framework
inherits that risk; a dedup PR does not.

---

## 9. Verify before acting

1. **Why did Splink delete `splink_synthetic_data`?** This is the load-bearing inference in
   the strongest kill (§4 Kill 1). Ten minutes in their discussions resolves it. If the answer
   is "consolidated for maintenance reasons," Kill 1 weakens materially — though Kills 2, 3,
   and 5 stand on their own and are individually sufficient.
2. **Confirm Cognee would accept an external dedup PR** before writing it. One comment on
   #3627. This is the cheapest possible de-risking of Step 2 and should happen first.
3. **Do not fund the corruption-realism problem.** If Step 2 somehow creates pull for a public
   `corrupt()` API, remember Lam et al.: credible realism needs error co-occurrence modeled
   from real labeled data. Price that as research, not as a feature.
