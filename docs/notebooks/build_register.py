"""Build and execute ``register_resolution.ipynb``. Generated from this script
so the narrative stays reviewable as text, then executed so the committed
notebook carries its outputs:

    python docs/notebooks/build_register.py

The real-register section runs only if ``GRAPHFAKER_REGISTER`` points at one.
It prints counts and never records: a register is somebody else's data and
real people's, so nothing from it belongs in a committed file.
"""

import re
from pathlib import Path

import nbformat
from nbclient import NotebookClient
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "notebooks" / "register_resolution.ipynb"

cells = []


def unwrap(text: str) -> str:
    """One paragraph per line: join lines that are not list items, headings or table rows."""
    out, buf = [], []
    for line in text.strip().splitlines():
        s = line.strip()
        block = not s or re.match(r"^(#|\||[-*]\s|\d+\.\s|```)", s)
        if block:
            if buf:
                out.append(" ".join(buf))
                buf = []
            out.append(line)
        else:
            buf.append(s)
    if buf:
        out.append(" ".join(buf))
    return "\n".join(out)


md = lambda s: cells.append(new_markdown_cell(unwrap(s)))
code = lambda s: cells.append(new_code_cell(s.strip()))

md("""
# A real register, and a generated one you can score

Entity resolution has an awkward property: the datasets where it matters have no answer key, and the datasets with
an answer key are usually too easy to be worth scoring on. This notebook works both halves. It measures a real
company register to find out what the problem actually looks like, generates a dataset with that shape and a known
answer, and runs the same resolver against both.

What the register decides, and what this notebook does with it:

| measured on a real register | what it means for a resolver | what the generator does with it |
|---|---|---|
| one address holds 99,522 companies | address is not a blocking key | suppliers share addresses on the measured curve |
| 1.02% of name stems are shared by two companies | names are nearly unique, so near-matches are mostly the same firm | name reuse held at that rate at any scale |
| `LLC` 53.9%, `L.L.C` 2.6%, comma 50/50 | the same company is written several ways | the measured spellings, and `variants` to write them |

The real register is optional. Set `GRAPHFAKER_REGISTER` to a Senzing-format file, directory or zip and the last
section runs; without it everything else still does. Nothing from a real register is printed here beyond counts,
because it is someone else's data and real people's.
""")

code("""
import os
import time
import warnings
from pathlib import Path

import networkx as nx
import numpy as np
import polars as pl

warnings.filterwarnings("ignore")
pl.Config.set_tbl_rows(12)

from graphfaker.domains.supply_chain import generate
from graphfaker.engine.names import REGISTER, collision_stats, stem, variants
from graphfaker.resolve import evaluate_clusters, resolve_entities

REGISTER_PATH = os.environ.get("GRAPHFAKER_REGISTER")
print("names profile measured on:", REGISTER.source)
""")

md("""
## 1. What the register says, as numbers

The two reference files in `benchmarks/realism/` were measured by streaming real registers and keeping counts only:
a Las Vegas file of 2,026,444 records and the 1,033,773 companies registered in Nevada inside a 330-million-record
national export. They are the input to everything below, and they are the only form of someone else's register that
should travel.
""")

code("""
import json

REALISM = Path.cwd().parents[1] / "benchmarks" / "realism"   # docs/notebooks -> repository root

refs = {}
for name in ("corporate-lasvegas", "corporate-nevada"):
    path = REALISM / f"{name}.json"
    if path.exists():
        refs[name] = json.loads(path.read_text(encoding="utf-8"))

for name, ref in refs.items():
    companies = ref["name_collision_companies"]
    records = ref["name_collision"]
    address = next(d for d in ref["distributions"] if d["metric"] == "organizations_per_address")
    print(f"{name}  ({ref['records']:,} records, licence {ref['licence']})")
    print(f"  companies per address   p50 {address['p50']}, p99 {address['p99']}, "
          f"max {address['max']:,}, top 1% hold {address['top1pct_share']:.1%}")
    print(f"  two companies, one name  {companies['repeated_without_legal_form_share']:.2%} of stems, "
          f"busiest stem {ref['shared_names']['max']}")
    print(f"  one company, many records {records['repeated_without_legal_form_share']:.1%} of stems")
    print()
""")

md("""
Those two name numbers are the thing to be careful about, and this project got it wrong once. 91.8% of name stems
in the register are used by more than one *record*, which sounds like a resolver's nightmare of colliding companies.
It is not: a location record carries its parent company's name, so a firm with five sites writes its name five
times. Counted per company, one record each, only 1.02% of stems are shared.

Both are real and they are different jobs. One company written many times is what a resolver spends its day on, and
it is tractable. Two companies with one name is rare and is what makes precision expensive. A generator has to get
both rates right or a score measured on it means nothing.
""")

md("""
## 2. A generated register with the same shape

The supply chain pack's companies carry the measured legal forms and share addresses on the measured curve. This is
the same `generate` call as anywhere else in the documentation; the names and addresses are what changed.
""")

code("""
run = generate(scale=0.01, seed=11)
companies = run.tables.nodes["Supplier"].select(
    "id", "name", "address_line1", "address_city", "address_postal_code"
)
companies.head(8)
""")

code("""
stats = collision_stats(companies["name"].to_list())
real = refs.get("corporate-lasvegas", {}).get("legal_form_spellings", {})
rows = [
    {"spelling": spelling or "(none)",
     "generated": stats["form_mix"].get(spelling or "(none)", 0.0),
     "register": real.get(spelling or "(none)")}
    for spelling, _ in REGISTER.spellings[:8]
]
print(f"name reuse: {stats['repeated_stem_share']:.2%} of stems shared "
      f"(register {REGISTER.stem_reuse:.2%}), busiest stem {stats['max_per_stem']}")
pl.DataFrame(rows)
""")

code("""
# Addresses are shared the way a register shares them, which is the part that
# makes address useless as a key.
per_address = companies.group_by("address_line1").len().sort("len", descending=True)
print(f"{companies.height} companies at {per_address.height} addresses; "
      f"median {per_address['len'].median():.0f}, busiest {per_address['len'].max()}")
per_address.head(5)
""")

md("""
## 3. One company, several records

A register does not hold one row per company. It holds a row per filing, per site, per source, and the same company
is written differently in each: with the comma and without, `LLC` and `L.L.C`, sometimes with no legal form at all.
`names.variants` produces exactly those spellings, so the records below are the problem a resolver actually gets,
and we know which of them belong together.
""")

code("""
rng = np.random.default_rng(0)
graph = nx.Graph()
gold = []

for company in companies.to_dicts():
    spellings = [company["name"], *variants(company["name"], rng, 2)]
    ids = []
    for n, spelling in enumerate(spellings):
        node = f"{company['id']}#{n}"
        graph.add_node(
            node, type="Company", name=spelling, stem=stem(spelling),
            address_line1=company["address_line1"],
            address_city=company["address_city"],
            postcode=company["address_postal_code"],
        )
        ids.append(node)
    if len(ids) > 1:
        gold.append(ids)

print(f"{graph.number_of_nodes()} records for {companies.height} companies; "
      f"{len(gold)} of them appear more than once")
example = gold[0]
pl.DataFrame([{"record": n, "name": graph.nodes[n]["name"]} for n in example])
""")

md("""
## 4. Scoring a resolver, because here we can

`resolve_entities` compares the fields you name and clusters what it thinks is one entity. `evaluate_clusters`
scores a clustering against a known-correct one and refuses to invent the gold for you. Four configurations, same
data.
""")

code("""
def score(label, **kwargs):
    started = time.perf_counter()
    result = resolve_entities(graph, structural_weight=0.0, node_types=["Company"], **kwargs)
    scores = evaluate_clusters(result.clusters, gold)
    return {
        "approach": label,
        "pairs": result.candidates_considered,
        "seconds": round(time.perf_counter() - started, 1),
        "clusters": len(result.clusters),
        "biggest": max((len(c) for c in result.clusters), default=0),
        "precision": round(scores["pairwise_precision"], 3),
        "recall": round(scores["pairwise_recall"], 3),
        "f1": round(scores["pairwise_f1"], 3),
    }

results = [
    score("name, blocked on name", on=["name"], threshold=0.85),
    score("name, blocked on stem", on=["name"], threshold=0.85, block_on=["stem"]),
    score("name, loose (0.7)", on=["name"], threshold=0.7, block_on=["stem"]),
    score("name + address", on=["name", "address_line1"], threshold=0.8, block_on=["stem"]),
]
pl.DataFrame(results)
""")

md("""
Three things in that table, and two of them are the dataset earning its keep.

**Blocking on the name does six times the work for the same answer.** The legal form is a token like any other, so a
block keyed on the name puts every company ending in `LLC` together, and 54% of them do. Blocking on the stem, the
name with the form removed, compares the same pairs that matter and skips 158,000 that do not. A dataset of
unsuffixed `Fischer-Walker` names would never have shown this, because the pile-up is a consequence of the measured
suffix distribution.

**Adding the address makes it worse, not better.** Recall goes to 1.0, which looks like a win, and precision falls
from 0.967 to 0.763: the address pulls in companies that share one, and in this dataset they share one at the rate a
register says they do. More evidence is not better evidence when the evidence is a registered agent's address.

**The loose threshold is the familiar failure.** At 0.7 recall is 0.994 and precision is 0.076, with one cluster of
151 records. 1% of stems really are shared by two different companies, so some of those merges are genuinely wrong,
and here we know which.
""")

md("""
## 5. The trap the addresses are there for

If adding an address costs precision, matching on one alone should be a disaster, and it is. Blocking on address is
the obvious optimisation, since companies at the same door are probably the same company. On a real register it is
catastrophic, because one address holds 99,522 companies. A generated dataset where every company has its own
address would report this as a free win.
""")

code("""
trap = score("address alone", on=["address_line1"], threshold=0.95, block_on=["address_line1"])
pl.DataFrame([results[1], trap])
""")

code("""
# Where the precision went: the biggest cluster the address rule produced,
# against the biggest real company.
result = resolve_entities(
    graph, on=["address_line1"], threshold=0.95, block_on=["address_line1"],
    structural_weight=0.0, node_types=["Company"],
)
biggest = max(result.clusters, key=len)
names = {stem(graph.nodes[n]["name"]) for n in biggest}
print(f"one cluster of {len(biggest)} records covering {len(names)} different companies, "
      f"all at {graph.nodes[biggest[0]]['address_line1']}")
""")

md("""
## 6. The same resolver on a real register

Everything above had an answer key. This section does not, which is the point of running it: the output is a shape,
not a score, and no amount of looking at it tells you how much was missed.

Set `GRAPHFAKER_REGISTER` to a Senzing-format file, directory or zip to run it. The cell prints counts only.
""")

code("""
if not REGISTER_PATH:
    print("GRAPHFAKER_REGISTER is not set, so this section is skipped.")
    print("Point it at a register and re-run:  graphfaker register <path> --limit 200000")
""")

code("""
if REGISTER_PATH:
    from graphfaker.fetchers.senzing import SenzingFetcher

    real = SenzingFetcher.fetch_graph(REGISTER_PATH, limit=20_000, record_types=("ORGANIZATION",))
    named = [n for n, d in real.nodes(data=True) if d.get("NAME_ORG")]
    # 5,000 records, deliberately: comparison inside a block is quadratic, and
    # 20,000 of them takes a quarter of an hour. Resolving a whole register is
    # a job for a resolver, not for a notebook cell.
    slice_ = real.subgraph(named[:5_000]).copy()
    for node, data in slice_.nodes(data=True):
        data["stem"] = stem(data["NAME_ORG"])
    print(f"{real.number_of_nodes():,} organisation records read, {len(named):,} with a name, "
          f"{slice_.number_of_nodes():,} resolved")

    found = resolve_entities(
        slice_, on=["NAME_ORG", "ADDR_LINE1"], threshold=0.88,
        block_on=["stem"], block_prefix=10,
        structural_weight=0.0, node_types=["ORGANIZATION"],
    )
    sizes = sorted((len(c) for c in found.clusters), reverse=True)
    print(f"{len(found.clusters):,} clusters covering {sum(sizes):,} records; "
          f"largest {sizes[0] if sizes else 0}, median {sizes[len(sizes) // 2] if sizes else 0}")
    print()
    print("No ground truth here, so no precision and no recall. Those clusters may be")
    print("right or may be a tenth wrong and nothing in this output distinguishes the")
    print("two: that is the gap the generated half of this notebook fills.")
""")

md("""
## What to take from this

- The best configuration here was the simplest: the name alone, blocked on its stem, at 0.967 precision and 0.932
F1. Both of the obvious improvements, more fields and a looser threshold, cost precision, and each cost it for a
reason the register measured.
- A resolver's score depends entirely on which of the two name problems the data contains: one company written many
ways is tractable, two companies with one name is not. Measure them separately or the number means nothing.
- Blocking on address is not a shortcut, and only a dataset with a register's address curve will tell you so.
- The real register gives the shape; the generated one gives the score. Neither is sufficient alone, which is the
whole argument for keeping both in one toolkit.

Next steps:

- `graphfaker register <path> --out ./register --sink duckdb` loads a real register into a database, after which
every `graphfaker load` and `verify` command works on it.
- `graphfaker register <path> --limit 500000` reports what a register contains before you spend an hour on it.
- `python benchmarks/realism/corporate.py --source <path> --state NV --workers 12` measures a register into the
reference format used in section 1.
""")

nb = new_notebook(cells=cells, metadata={
    "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
    "language_info": {"name": "python"},
})
OUT.parent.mkdir(parents=True, exist_ok=True)
nbformat.write(nb, OUT)
print("built", OUT)

client = NotebookClient(nb, timeout=3600, kernel_name="python3", resources={"metadata": {"path": str(OUT.parent)}})
client.execute()
nbformat.write(nb, OUT)
print("executed; cells:", len(nb.cells), "| size", OUT.stat().st_size // 1024, "KB")
