# Benchmarks

The harness behind [docs/scaling.md](../docs/scaling.md), the raw results, and the detail that does not belong on a documentation page: per-interval slopes, the fit caveat, worker traces, and the before/after of the 0.6.1 worker fixes.

## Files

- `bench_scale.py`: one run, one line of JSON (generation time, write time, sampled peak memory of the process tree, machine). Dependency-light so it runs against an old release in another venv.
- `compare.py`: a set of scales across versions, prints the comparison.
- `scaling.py`: fits the growth exponent, prints per-interval log-log slopes and a split fit, and says when the two halves disagree.
- `scaling-m3pro.json`: 0.5.0 against 0.6.0, scales 0.005 to 0.3, Apple M3 Pro.
- `scaling-m3pro-high.json`: 0.6.0 alone, scales 0.1 to 0.8, M3 Pro.
- `scaling-win-i7.json`: 0.6.0 and 0.6.1, Intel Core i7-9850H laptop, single process and by worker count.

## Growth exponents

Local log-log slope between adjacent points; 1.0 means time grows in step with the data.

| range | 0.5.0, M3 | 0.6.0, M3 | 0.6.0, i7 |
|---|---|---|---|
| 0.005 to 0.01 | 1.01 | 0.87 | 0.86 |
| 0.01 to 0.02 | 0.99 | 1.06 | 0.91 |
| 0.02 to 0.05 | 1.05 | 1.03 | 0.85 |
| 0.05 to 0.1 | 1.08 | 1.05 | 1.02 |
| 0.1 to 0.2 | 1.26 | 1.02 | |
| 0.2 to 0.3 | 1.52 | 1.17 | 0.99 |

Fitting each half separately, 0.5.0 goes from an exponent of 1.03 below `scale=0.1` to 1.35 at and above it; 0.6.0 moves from 1.01 to 1.07. A single power law across the whole range gives 1.10 and 1.03 with r-squared above 0.996 and is misleading: the residuals are structured and it under-predicts `scale=0.3` by 15%, because it averages two regimes. `scaling.py` prints both.

0.6.0 alone from `scale=0.1` to `0.8` on the M3 (single runs at the expensive end, generation only, write skipped):

| scale | generate | local slope | peak memory (Mac reading) |
|---|---|---|---|
| 0.1 | 12.3 s | | 2.93 GB |
| 0.2 | 25.9 s | 1.07 | 4.65 GB |
| 0.3 | 39.8 s | 1.06 | 5.62 GB |
| 0.4 | 55.3 s | 1.14 | 5.84 GB |
| 0.5 | 66.9 s | 0.85 | 7.83 GB |
| 0.6 | 80.5 s | 1.02 | 7.97 GB |
| 0.8 | 118.8 s | 1.35 | 8.16 GB |

Exponent across the range 1.072, r-squared 0.9987. The 0.85 and the 1.35 are single-run noise; the memory column is the macOS resident size, which undercounts for the reasons on the scaling page (the i7 reads 10.1 GB at `scale=0.3` against 5.6 here).

## Phase split

`scale=0.1`, single process.

| phase | M3 | i7 |
|---|---|---|
| node attributes | 5.5 s (57%) | 20.2 s (49%) |
| transaction process | 3.7 s (38%) | 11 s (27%) |
| population index, merge, apply | 0.4 s | 9 s |
| pattern injection | 0.1 s | 0.4 s |

## Workers

Generation only, best of three, `scale=0.1` unless stated.

| machine, version | 1 | 2 | 4 | 8 |
|---|---|---|---|---|
| M3, 0.6.0 | 12.4 s | | 10.4 s | 10.7 s |
| i7, 0.6.0 | 40.9 s | 37.5 s | 39.0 s | 48.0 s |
| i7, 0.6.1 (later session, laptop warm) | 49.5 s | | 34.2 s | |
| i7, 0.6.0, scale 0.3 | 121.9 s | | 107.8 s | |
| i7, 0.6.1, scale 0.3 | 141.4 s | | 92.8 s | |

Karp-Flatt effective serial fraction, `e = (1/S - 1/p) / (1 - 1/p)`, against the phase-measured serial share of 0.43 (M3) and 0.51 (i7):

| machine, scale, version | p | speed-up | Amdahl bound | `e` |
|---|---|---|---|---|
| M3, 0.1, 0.6.0 | 4 | 1.19x | 1.75x | 0.78 |
| M3, 0.1, 0.6.0 | 8 | 1.16x | 2.00x | 0.84 |
| i7, 0.1, 0.6.0 | 2 | 1.09x | 1.32x | 0.83 |
| i7, 0.1, 0.6.0 | 4 | 1.05x | 1.58x | 0.94 |
| i7, 0.1, 0.6.0 | 8 | 0.85x | 1.75x | 1.20 |
| i7, 0.3, 0.6.0 | 4 | 1.13x | 1.77x | 0.85 |
| i7, 0.1, 0.6.1 | 4 | 1.45x | 1.6x | 0.59 |
| i7, 0.3, 0.6.1 | 4 | 1.52x | 1.6x | 0.54 |

Node phase alone on the i7 by worker count, which is where the 0.6.0 overhead was visible:

| workers | node phase, 0.6.0 | Customer (72 shards) | Account (100 shards, foreign key) | node phase, 0.6.1 |
|---|---|---|---|---|
| 1 | 20.2 s | 13.8 s | 1.9 s | 16.5 s |
| 2 | 22.9 s | 13.5 s | 5.9 s | |
| 4 | 20.6 s | 11.5 s | 6.5 s | 13.3 s |
| 8 | 26.7 s | 13.2 s | 11.1 s | 14.2 s |

Tracing the shards individually on the i7 with four workers, 0.6.0: the first Customer shard started 7.0 s after the pool was created (the workers' `import graphfaker`), then 72 shards ran at 3.8x parallelism for 7.2 s; in-worker busy time was 27 s for 13.8 s of single-process work, so a shard took 0.37 s in a worker against 0.15 s alone. With two workers 0.22 s. Pinning polars to one thread (`POLARS_MAX_THREADS=1`) changed nothing, so it is the clock under load, not thread oversubscription. Account, which had a pool of its own in 0.6.0 for the foreign-key index, started its shards at +5.2, +10.7, +15.8 and +21.3 s: one worker at a time, because the index travelled in `initargs` and on Windows the parent's write of those arguments blocks until the child has imported its main module. 0.6.1 moves the index to a file, uses one pool for the run, skips the pool under 250,000 rows, and imports the package lazily; the first shard now starts at 4.3 s, and a worker's import is 1.5 s warm instead of 2.9 s.

## Memory

Sampled peak of the process tree every 0.2 s.

| scale | i7 0.6.0, generation only | i7 0.6.0, with write | i7 0.6.1, with write | M3 0.6.0, generation only |
|---|---|---|---|---|
| 0.1 | 3.37 GB | 3.7 GB | 2.5 GB | 2.93 GB |
| 0.3 | 9.38 GB | 10.1 GB | 5.2 GB | 5.62 GB |
| 1.0 | | 32.4 GB (4 workers) | 15.1 GB (single process) | |

0.6.1 at `scale=1.0`, single process: nodes 244 s, patterns 27 s, transactions 129 s, generate 426 s, write 95 s, 2.23 GB of Parquet.

The i7 is linear in both versions: about 33 GB per unit of scale in 0.6.0, about 15 GB in 0.6.1. The M3 reads lower and increasingly so with scale; macOS compresses idle pages out of the resident set and its allocator returns freed memory sooner. A short spike between samples is invisible to both.

## Corporate realism

`realism/corporate.py` measures the things a generator cannot invent about a population of companies: how many share
an address, how many people sit on a board, how often a name is already taken, how often an identifier is simply
absent. It reads a register in Senzing's entity resolution format, streams it once, and writes aggregate statistics.
Counts only: the record is read, counted and dropped, and the output has no company, person, name or address in it.

A source can be one file, a directory of shards or a zip. Scale forces a choice at national size. The category mixes
(record types, pointer roles, states, identifier coverage) need one counter per category and are measured over
everything read. Anything that needs one counter per entity (companies per address, people per company, names
already taken) would need tens of millions of counters over a 368-million-record export, so `--state` restricts
those to the companies registered in one place along with their locations and officers wherever those live. Every
output file carries a `scope` saying which numbers had which treatment.

The shards are the unit of work and every counter adds, so `--workers` spreads them over processes without changing
the answer. A pass over the 158 GB national export went from 150 minutes in one process to 26 in twelve, 37,000
records a second to 207,000, and the result is identical on every measured key: same 330,553,048 records, same
distributions, same name collisions, same legal form mix. It is two rounds rather than one, because the companies
have to be counted before anything can be decided about the records pointing at them, and the accepted anchor keys
go to each worker once when it starts rather than with every job. Ties in a frequency table are broken by name so
the written file does not depend on the order the shards came back in either.

Three things made the single-process pass faster too, which is where the 37,000 came from: `orjson` when it is
installed (of a pass, 18% is decompression, 59% is parsing JSON and 23% is the counting, and this halves the middle
one), one zip handle for the whole archive rather than one per shard, and building an address key only for the
records a filter kept rather than for all 227 million.

There are two references. `realism/corporate-lasvegas.json` was measured from a Las Vegas register of 2,026,444
records (1,668,368 organisations and 358,076 people, with their locations and officers).
`realism/corporate-nevada.json` was measured from the national open data export of 2026-03-05: 330,553,048 records
in 2,493 shards, 150 minutes at 37,000 records a second, with the per-entity distributions scoped to the 1,033,773
companies registered in Nevada and the 3,398,586 records attached to them. The same script scores a GraphFaker
dataset on the same metrics, so the gap is a list rather than an opinion:

```sh
python benchmarks/realism/corporate.py --source register.jsonl --label "..." --out realism/corporate-lasvegas.json
python benchmarks/realism/corporate.py --source ODO_SENZING.zip --state NV --workers 12 \
    --out realism/corporate-nevada.json
python benchmarks/realism/corporate.py --compare realism/corporate-nevada.json --domain supply_chain --scale 0.01
```

The two agree, which is the point of having both. One is a city file somebody prepared; the other is a state cut out
of a national archive by a different route, and the two extracts are not the same records. Every distribution lands
in the same place anyway, so these are properties of the population rather than of the extract:

### Provenance and licence

Both references are measured from the same publisher. The archive is OpenData.org's U.S. entity dataset of
2026-03-05, distributed in Senzing's JSON format, and the counts line up with what was announced for it: 86 million
organisations, 142 million locations and 101 million people-company links, against 330,553,048 records measured
here. The Las Vegas file is an extract of the same thing and says so in every record (`DATA_SOURCE` of `OPENDATA`).

OpenData.org's [terms](https://opendata.org/terms/) place its datasets under
[CDLA-Permissive-2.0](https://cdla.dev/permissive-2-0/), checked on 2026-10-08. Three things in that licence decide
what is in this directory:

- **Computed output has no conditions.** Section 3.1 says the agreement imposes no restriction or obligation on
  Results, which is what it calls anything obtained by computational use of the data. The two reference files are
  counts, distribution summaries and frequency tables, so they travel freely. They name their source anyway, in a
  `provenance` block, because a number nobody can trace is not a reference.
- **Sharing the data itself has one condition**, in section 2.1: pass on the text of the agreement with it. That is
  the rule that would apply if GraphFaker ever shipped records, and it is one reason it does not. The fixture in
  `tests/test_senzing.py` is written by the test, copying the shape of a real file and none of its rows.
- **There is no attribution requirement and no share-alike.** Commercial use is allowed. This is a permissive
  licence, which is why the dataset is a reasonable thing to build a public reference on.

A licence is not permission for everything, though. It covers copyright and database rights, not privacy law, and
the people records here are named individuals with home addresses and social profiles. OpenData.org's
[privacy page](https://opendata.org/privacy/) puts the lawful basis on whoever contributed a dataset and offers a
contact for improper inclusion; anyone who republishes records takes on their own obligations, whatever the licence
says. So the rule for this repository is the one the files already follow: aggregate statistics yes, records no, and
no copy of anybody's register in the package.

Note also that the same data redistributed by other projects can carry different terms. OpenSanctions publishes a
build of it under a non-commercial licence; that governs their copy, not the publisher's, and the two should not be
confused. None of this is legal advice, and the terms page is dated above because it can change.

| metric | Las Vegas p50 / p99 / max / top 1% | Nevada p50 / p99 / max / top 1% | supply_chain 1.2 |
|---|---|---|---|
| organisations per address | 1 / 31 / 99,522 / 53.7% | 1 / 27 / 99,794 / 51.0% | 1 / 43 / 86 / 17.2% |
| people per organisation | 1 / 139 / 11,351 / 58.3% | 1 / 151 / 68,945 / 61.4% | 4 / 218 / 1,146 / 32.9% |
| sites per organisation | 1 / 5 / 4,229 / 5.1% | 1 / 5 / 4,283 / 5.3% | 1 / 7 / 13 / 5.5% |
| organisations per person name | 1 / 3 / 44 / 4.1% | 1 / 4 / 84 / 5.0% | 1 / 4 / 8 / 4.5% |
| one name on several records, form off | 91.8% | 90.5% | locations are not separate records |
| two companies sharing a name, form off | 1.02% | 1.37% | 0.91% |
| busiest name stem | 18 companies | 36 companies | 3 companies |
| LLC share, as written, companies only | 53.9% | 51.9% | 54.0% |

The national pass also answers the questions that need one counter per category rather than per entity, over all
330 million records: 227.2M organisation records against 103.4M person records; pointer roles running
INDEPENDENT 115.1M, Contact 79.9M, Executive 21.6M, HEADQUARTERS 13.8M, BRANCH 12.9M, BUSINESS 0.6M; and identifier
coverage of geo 67.3%, postcode 61.9%, LinkedIn 33.1%, Placekey 29.2%, website 14.7%, LEI 0.06%. The registered
address is spread across every state, led by FL 11.6%, CA 10.9% and TX 8.3%, which is what makes the Nevada cut a
cut of companies rather than of addresses.

Four things worth taking from it, of which the first is now fixed.

**One address holds 99,522 companies** in Las Vegas and 99,794 in Nevada, and the top 1% of addresses carry over
half of all records in both. That is the registered agent, and it means address is not a blocking key: a resolver
that merges on it merges a city. The first measurement found suppliers with a country and no address at all, so
none of this was modelled. `graphfaker.engine.addresses` now allocates a shared pool on a Zipf curve fitted to the
register, and the supply chain pack draws its suppliers, plants and warehouses from one pool. At the engine's own
scale of 200,000 organisations it reproduces the mean (4.76 against 4.76 and 4.49), the median (1) and the 90th
percentile (4), and gets the top 1% share within a few points (0.56 against 0.537 and 0.510), but runs hot in the
tail: p99 of about 40 against 31 and 27. One Zipf curve cannot have both, because the exponent that sets the mean
also sets the tail; a sweep against both references puts the mean at 3.7 by the time p99 is right. Two components,
a heavy head of agents over a nearly unique body, is what that costs to fix, and it is in the plan rather than
tuned away. The row in the table above is lower again because a small run cannot have the real head, 1% of a few
hundred addresses being one address, and because customers are in the same count and draw from a nearly unique
pool: a retail base is not registered through agents.

**Two companies rarely share a name, and this was measured wrong at first.** 86.7% of distinct organisation
names in the Las Vegas register are used by more than one record, and an earlier version of this file read that as
"different companies collide 86.7% of the time, ours 1.4%, so our names are two orders of magnitude too unique".
That was wrong, and the mistake was in the counting: a location record carries its parent company's name, so a
company with five sites writes its name five times. Counting companies only, one record each, **0.14% of names and
1.02% of stems are used by more than one company, and the busiest stem belongs to 18 of them.** Nevada says 0.18%
and 1.37%, with 36 on the busiest stem, so this is a property of registers rather than of one file.

Both numbers are real and they are different problems. One entity written on many records is what an entity
resolver spends its time on; two entities with one name is the rarer trap that makes precision hard. The file now
reports them separately, and the comparison puts companies against companies.

Measured honestly, the generator's names were wrong in the other direction. Faker's company provider draws from
about a thousand surnames, so at 500 rows 1.6% of names repeated and at 200,000 rows **10.3% repeated with one stem
carrying 1,451 companies**. `graphfaker.engine.names` replaces it: the reuse rate now holds between 0.97% and 1.01%
from 500 rows to 200,000, against the measured 1.02%, and exact repeats sit at 0.2% to 0.4% against 0.14%. The
busiest stem carries 3 companies where the register's carries 18, which is the part still missing.

**The legal form is a distribution and its spelling varies, and this we had plainly wrong.** On company records:
LLC 53.9%, INC 20.5%, no suffix at all 11.9%, `L.L.C` 2.6%, `CORPORATION` 2.4%, `LTD` 1.8%, `CORP` 1.5%. Half of
all names put a comma before the suffix and half do not. Faker offers `PLC`, `Group` and `and Sons` instead, and
before this 81% of our companies had no suffix and 5.6% were LLCs. Every spelling is now generated at its measured
rate, and `names.variants` writes one company's name the several ways a register writes it, which is the thing a
resolver has to survive and a dataset of unique names cannot test.

**Legal forms are a distribution, and the spellings vary.** LLC 54.4%, INC 21.0%, no suffix at all 15.0%, CORP 3.8%.
The register contains `LLC` 865,779 times and `L.L.C` 42,567 times, which is the variant problem in the open. Our
names end in a suffix 19% of the time and never in LLC at a realistic rate.

**Identifiers are mostly missing.** Geo on 82.7% of Las Vegas records, Placekey 27.2%, LinkedIn 19.4%, website
16.1%, LEI on 0.02%; nationally 67.3%, 29.2%, 33.1%, 14.7% and 0.06%. Every attribute on every one of our suppliers
is populated. A pipeline tuned on data where everything is present will not survive contact with a register where a
fifth of the rows have a website.

**Most companies have nobody attached, and this one is now modelled.** Only 46,201 of the 1,033,773 Nevada
companies have a single person pointing at them, and the ones that do have a mean of 14.5, a 99th percentile of 151
and a maximum of 68,945. Board density is not a number, it is a distribution with a floor of zero, and a generator
that gives every company an officer gets the easy part wrong twice over. `graphfaker.engine.people` attaches people
on a rank curve fitted to the measured mean and concentration: at the register's own 46,201 attached companies the
busiest holds 10.2% of every person-company link against a measured 10.3%, and the top 1% hold 63% against 61%. The
row in the table above is a 1,100-company run, where a median of 4 rather than 1 is the same small-scale limit the
addresses have: a few hundred companies cannot carry a head measured over a million.

These are gaps in the generator, not in the metric, and they are the input to the corporate work in the plan. The
reference file is derived statistics rather than data, which is the only form of someone else's register that should
travel.

## Running it

```sh
uv venv --python 3.12 /tmp/gf050
uv pip install --python /tmp/gf050/bin/python graphfaker==0.5.0 psutil

python benchmarks/scaling.py \
    --version 0.5.0=/tmp/gf050/bin/python \
    --version 0.6.0=.venv/bin/python \
    --scale 0.005 --scale 0.01 --scale 0.02 --scale 0.05 --scale 0.1

python benchmarks/bench_scale.py --scale 0.1 --workers 4 --label 0.6.1 --no-write
```
