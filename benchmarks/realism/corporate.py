"""Corporate realism metrics: what a real company register looks like, and
what ours looks like next to it.

A generator can invent a company, a name and an address. What it cannot
invent is how often the world does a thing: how many companies share one
registered address, how many people sit on a board, how often a name is
already taken, how often an identifier is simply missing. Those are
properties of a population, and the only way to know them is to count them
somewhere real.

So this script does two things. It streams a real register in Senzing's
entity resolution format and reduces it to aggregate statistics, keeping
counts and never records, which is the only form of this data that should
travel. And it computes the same statistics for a GraphFaker dataset, so the
difference is a list rather than an opinion.

    python benchmarks/realism/corporate.py --source opendata-lasvegas.jsonl \\
        --label "Las Vegas register" --out benchmarks/realism/corporate-lasvegas.json

A source can be one file, a directory of shards or a zip, because a national
export arrives as thousands of files. Scale forces a choice there. The
category mixes (record types, pointer roles, states, identifier coverage)
need one counter per category and are measured over everything read. Anything
that needs one counter per entity (how many companies share an address, how
many people sit on a board, how often a name is taken) needs tens of millions
of counters at national scale, so ``--state`` restricts those to the
companies registered in one place, along with their locations and officers
wherever those live. The output says which numbers had which scope.

    python benchmarks/realism/corporate.py --source ODO_SENZING.zip --state NV \\
        --label "Nevada, from the national export" --out benchmarks/realism/corporate-nevada.json

The shards are the unit of work and every counter adds, so ``--workers`` runs
them in parallel without changing the answer: a pass over the national
archive goes from 150 minutes to about 20 on twelve processes. It is two
rounds rather than one, because the companies have to be counted before
anything can be decided about the records pointing at them.

    python benchmarks/realism/corporate.py --compare benchmarks/realism/corporate-lasvegas.json \\
        --domain supply_chain --scale 0.01

Nothing about an individual company or person survives a run: the output is
distribution summaries, frequency tables of legal suffixes and name tokens,
and coverage rates. Check ``--out`` before sharing it anyway, because that
is the discipline the whole exercise is about. ``--licence`` records the
register's terms in the file, so a number can be traced back to what it was
allowed to be measured from; see "Provenance and licence" in
``benchmarks/README.md``.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from graphfaker.fetchers.senzing import read_shard, shard_names

#: Legal forms, normalised: ``L.L.C.`` and ``LLC`` are the same thing, and
#: the fact that a register contains both spellings is the entire reason
#: entity resolution is hard.
LEGAL_FORMS = {
    "LLC": "LLC", "LLC.": "LLC", "L.L.C": "LLC", "L.L.C.": "LLC",
    "INC": "INC", "INC.": "INC", "INCORPORATED": "INC",
    "CORP": "CORP", "CORP.": "CORP", "CORPORATION": "CORP",
    "LTD": "LTD", "LTD.": "LTD", "LIMITED": "LTD",
    "LP": "LP", "L.P.": "LP", "LLP": "LLP", "L.L.P.": "LLP",
    "PLLC": "PLLC", "PC": "PC", "PA": "PA",
    "CO": "CO", "CO.": "CO", "COMPANY": "CO",
    "TRUST": "TRUST", "FOUNDATION": "FOUNDATION", "ASSOCIATION": "ASSOCIATION",
}

#: Dropped before asking whether two names collide.
NOISE_TOKENS = set(LEGAL_FORMS) | {"THE", "AND", "OF", "A"}

#: Identifiers worth knowing the coverage of. Real registers have most of
#: them missing most of the time, and a generator that fills every column is
#: easier to work with and less like the world.
IDENTIFIERS = (
    "GEO_LATITUDE", "PLACEKEY", "LINKEDIN", "WEBSITE_ADDRESS",
    "ADDR_LINE2", "ADDR_POSTAL_CODE", "OTHER_ID_NUMBER", "NPI_NUMBER", "LEI_NUMBER",
)


#: A legal form at the end of a name, in any of the spellings a register
#: writes it in. Matched before the punctuation goes, because removing the
#: dots first turns ``L.L.C`` into ``L L C`` and then nothing strips it: the
#: variant spelling this whole exercise is about was the one that got through.
TRAILING_FORM = re.compile(
    r"[\s,]+(L\.?L\.?C\.?|INC\.?|INCORPORATED|CORP\.?|CORPORATION|LTD\.?|LIMITED"
    r"|PLLC|LLP|L\.?L\.?P\.?|LP|L\.?P\.?|PC|PA|CO\.?|COMPANY|FOUNDATION"
    r"|ASSOCIATION|TRUST|PLC)$",
    re.IGNORECASE,
)


def normalise(name: str) -> str:
    bare = TRAILING_FORM.sub("", str(name).upper().strip())
    cleaned = re.sub(r"[^A-Z0-9 ]", " ", bare)
    return " ".join(t for t in cleaned.split() if t not in NOISE_TOKENS)


def legal_form(name: str) -> str:
    tokens = re.sub(r"[,]", " ", name.upper()).split()
    return LEGAL_FORMS.get(tokens[-1], "(none)") if tokens else "(none)"


def suffix_spelling(name: str) -> str:
    """The legal form as it was actually written.

    ``LLC`` and ``L.L.C.`` are the same form and different strings, and the
    fact that a register holds both is the variant problem itself. The mix of
    spellings is a thing a generator has to reproduce separately from the mix
    of forms.
    """
    tokens = re.sub(r"[,]", " ", name.upper()).split()
    if not tokens or tokens[-1] not in LEGAL_FORMS:
        return "(none)"
    return tokens[-1]


def summarise(counter: collections.Counter, name: str) -> dict[str, Any]:
    """A heavy tail in the five numbers that describe one.

    The mean is nearly useless on its own here: a register where the median
    address holds one company and the busiest holds a hundred thousand has a
    perfectly ordinary-looking mean. The percentiles and the top 1% share are
    what a generator has to reproduce.
    """
    values = sorted(counter.values(), reverse=True)
    if not values:
        return {"metric": name, "keys": 0, "rows": 0}
    total = len(values)
    rows = sum(values)
    head = max(1, total // 100)
    return {
        "metric": name,
        "keys": total,
        "rows": rows,
        "mean": round(rows / total, 3),
        "p50": values[min(total - 1, total // 2)],
        "p90": values[min(total - 1, total // 10)],
        "p99": values[min(total - 1, total // 100)],
        "max": values[0],
        "top1pct_share": round(sum(values[:head]) / rows, 4),
        "gini": round(_gini(values), 4),
    }


def _gini(sorted_desc: list[int]) -> float:
    values = sorted(sorted_desc)
    n = len(values)
    total = sum(values)
    if n == 0 or total == 0:
        return 0.0
    weighted = sum((i + 1) * v for i, v in enumerate(values))
    return (2 * weighted) / (n * total) - (n + 1) / n


def _ordered(counter: collections.Counter, top: int | None = None) -> dict[str, Any]:
    """A frequency table, largest first, ties broken by name.

    ``Counter.most_common`` leaves ties in insertion order, which depends on
    the order the shards came back in. Sorting the ties makes the written
    file the same whether the pass ran in one process or twelve.
    """
    items = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return dict(items[:top] if top else items)


def _shares(counter: collections.Counter, top: int = 12) -> dict[str, float]:
    total = sum(counter.values()) or 1
    return {key: round(value / total, 4) for key, value in _ordered(counter, top).items()}


# ----------------------------------------------------------------- real

#: The counters one shard contributes. Everything here adds, which is what
#: makes a shard a unit of work: a pass over 2,493 of them is the same
#: arithmetic whether one process does it or ten.
TALLIES = (
    "per_address", "per_company_people", "per_company_sites", "per_person_name",
    "forms", "form_spellings", "tokens", "roles", "seen_states",
    "exact_names", "bare_names", "company_names", "company_stems",
    "coverage", "kinds",
)


def _empty() -> dict[str, Any]:
    tallies: dict[str, Any] = {name: collections.Counter() for name in TALLIES}
    tallies["rows"] = 0
    tallies["kept_rows"] = 0
    tallies["anchors"] = set()
    return tallies


def _merge(into: dict[str, Any], part: dict[str, Any]) -> dict[str, Any]:
    for name in TALLIES:
        into[name].update(part[name])
    into["rows"] += part["rows"]
    into["kept_rows"] += part["kept_rows"]
    into["anchors"] |= part["anchors"]
    return into


def measure_shard(
    path: Path,
    name: str,
    wanted: tuple[str, ...] = (),
    keep: set[Any] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """One shard, reduced to counters.

    ``wanted`` is the state filter and ``keep`` the anchor keys already
    accepted by it, which is how a pointer shard knows whether a record
    belongs to a company in scope. Without a filter everything is in scope
    and ``keep`` is not consulted.
    """
    out = _empty()
    kinds, roles, seen_states, coverage = (
        out["kinds"], out["roles"], out["seen_states"], out["coverage"]
    )
    accepted = keep if keep is not None else set()
    found = out["anchors"]

    for record in read_shard(path, name, limit):
        out["rows"] += 1
        kind = name_value = state = role = None
        anchor = pointer = address_feature = None
        for feature in record.get("FEATURES", []):
            anchor = feature.get("REL_ANCHOR_KEY", anchor)
            kind = feature.get("RECORD_TYPE", kind)
            name_value = feature.get("NAME_ORG") or feature.get("NAME_FULL") or name_value
            state = feature.get("ADDR_STATE", state)
            role = feature.get("REL_POINTER_ROLE", role)
            pointer = feature.get("REL_POINTER_KEY", pointer)
            if "ADDR_LINE1" in feature:
                address_feature = feature
            for key in IDENTIFIERS:
                if key in feature:
                    coverage[key] += 1
        kinds[kind or "(unknown)"] += 1
        if role:
            roles[role] += 1
        if state:
            seen_states[state] += 1

        # Scope: the per-entity counters below are the ones that cannot be
        # held at national scale.
        if wanted:
            if anchor is not None:
                if str(state or "").upper() not in wanted:
                    continue
                found.add(anchor)
            elif pointer not in accepted and pointer not in found:
                continue
        out["kept_rows"] += 1

        if kind == "ORGANIZATION":
            if address_feature is not None:
                # Built here rather than in the loop above, because joining
                # four fields for 227 million records to throw away all but
                # a million of them is most of the counting cost.
                out["per_address"]["|".join(
                    str(address_feature.get(k, "")).strip().upper()
                    for k in ("ADDR_LINE1", "ADDR_CITY", "ADDR_STATE", "ADDR_POSTAL_CODE")
                )] += 1
            if name_value:
                written = name_value.strip().upper()
                bare = normalise(name_value)
                out["exact_names"][written] += 1
                out["bare_names"][bare] += 1
                if anchor is not None:
                    # Forms and tokens are properties of a company, and a
                    # location record carries its parent's name: counting
                    # them per record counts one company's LLC once per site.
                    out["forms"][legal_form(name_value)] += 1
                    out["form_spellings"][suffix_spelling(name_value)] += 1
                    out["tokens"].update(bare.split())
                    # ``exact_names`` and ``bare_names`` above see a
                    # company's locations too, and a company with five sites
                    # writes its name five times: a real resolution problem,
                    # and a different one from two different companies called
                    # the same thing. This counter is the latter.
                    out["company_names"][written] += 1
                    out["company_stems"][bare] += 1
            if pointer:
                out["per_company_sites"][pointer] += 1
        elif kind == "PERSON":
            if pointer:
                out["per_company_people"][pointer] += 1
            if name_value:
                out["per_person_name"][name_value.strip().upper()] += 1
    return out


#: The anchor keys a worker is allowed to attach records to. It arrives once,
#: when the process starts, because the alternative is sending a million keys
#: along with every one of two thousand jobs: 16 GB of pickling to do 20
#: minutes of work.
_KEEP: set[Any] = set()


def _init(keep: set[Any]) -> None:
    global _KEEP
    _KEEP = keep


def _run_shard(job: tuple[Path, str, tuple[str, ...], bool, int | None]) -> dict[str, Any]:
    """A worker's whole job: one shard, as counters. Module level, so it pickles."""
    path, name, wanted, use_keep, limit = job
    return measure_shard(path, name, wanted, _KEEP if use_keep else None, limit)


def _fan_out(
    jobs: list[tuple],
    workers: int,
    total: dict[str, Any],
    started: float,
    keep: set[Any] | None = None,
) -> None:
    """Run the jobs, merging each result as it lands."""
    if workers <= 1:
        _init(keep or set())
        for job in jobs:
            _merge(total, _run_shard(job))
            _progress(total, started)
        return
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=workers, initializer=_init, initargs=(keep or set(),)
    ) as pool:
        for part in pool.map(_run_shard, jobs, chunksize=1):
            _merge(total, part)
            _progress(total, started)


_LAST_REPORT = [0]


def _progress(total: dict[str, Any], started: float) -> None:
    if total["rows"] - _LAST_REPORT[0] < 10_000_000:
        return
    _LAST_REPORT[0] = total["rows"]
    elapsed = time.perf_counter() - started
    print(
        f"  {total['rows']:,} records in {elapsed / 60:.0f} min "
        f"({total['rows'] / elapsed / 1000:.0f}k/s), {total['kept_rows']:,} in scope",
        file=sys.stderr,
        flush=True,
    )


def measure_source(
    path: Path,
    label: str,
    limit: int | None = None,
    states: tuple[str, ...] = (),
    workers: int = 1,
    licence: str | None = None,
) -> dict[str, Any]:
    """Stream a Senzing-format register and reduce it to statistics.

    Counters only: the record is read, counted and dropped.

    ``states`` restricts the per-entity distributions to the companies
    registered there. A location or a person is kept when the company it
    points at was kept, not when its own address matches, because an officer
    of a Nevada company who lives in California is on that board.

    That filter decides the shape of the pass. The company shards are read
    first, in one round; the anchor keys they accept are then handed to a
    second round over everything else. With ``workers`` above one each round
    is spread over processes, one shard at a time, and the counters are
    merged as they come back. Every counter here adds, so the answer does
    not depend on the worker count, and a tie in a frequency table is broken
    by name so the written file does not either. Twelve processes take a pass
    over the national archive from 150 minutes to about 20.

    ``limit`` is per shard when the pass is sharded, because a limit spent
    on the first shard of a register that keeps its companies and its people
    in different files measures nothing.
    """
    wanted = tuple(s.upper() for s in states)
    shards = shard_names(path)
    anchor_shards = [name for name, has_anchors in shards if has_anchors]
    other_shards = [name for name, has_anchors in shards if not has_anchors]
    sharded = len(shards) > 1
    per_shard = limit if sharded else None

    total = _empty()
    _LAST_REPORT[0] = 0
    started = time.perf_counter()

    if not sharded:
        # One file, one pass, and a limit that means what it says. A file
        # like this holds its companies and its people together, so the
        # anchors a record needs are the ones already read.
        _merge(total, measure_shard(path, shards[0][0], wanted, None, limit))
    else:
        # Round one: the companies, which is where the anchors are.
        _fan_out(
            [(path, name, wanted, False, per_shard) for name in anchor_shards],
            workers,
            total,
            started,
        )
        # Round two: everything that points at them, now that it can be
        # decided. The anchors go to each worker once, as its starting state.
        _fan_out(
            [(path, name, wanted, True, per_shard) for name in other_shards],
            workers,
            total,
            started,
            keep=total["anchors"],
        )

    rows, kept_rows = total["rows"], total["kept_rows"]
    kinds = total["kinds"]
    return {
        "label": label,
        "kind": "reference",
        "measured_at": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        # The name, not the path: this file gets committed and whose
        # Downloads folder it came from is not a property of the register.
        "source": Path(path).name,
        "licence": licence,
        "scope": {
            "aggregates": "every record read",
            "per_entity": ("ADDR_STATE in " + ",".join(wanted)) if wanted else "every record read",
            "records_in_scope": kept_rows,
            "companies_in_scope": len(total["anchors"]) if wanted else None,
        },
        "records": rows,
        "record_types": _ordered(kinds),
        "relationship_roles": _ordered(total["roles"]),
        "distributions": [
            summarise(total["per_address"], "organizations_per_address"),
            summarise(total["per_company_people"], "people_per_organization"),
            summarise(total["per_company_sites"], "sites_per_organization"),
            summarise(total["per_person_name"], "organizations_per_person_name"),
        ],
        "name_collision": _collision(total["exact_names"], total["bare_names"]),
        #: Companies only, one record each: two entries here are two
        #: different companies with the same name, which is the number a
        #: generator has to answer for.
        "name_collision_companies": _collision(
            total["company_names"], total["company_stems"]
        ),
        "shared_names": summarise(total["company_stems"], "companies_per_name_stem"),
        # Companies, not records: see the comment in ``measure_shard``.
        "legal_form_mix": _shares(total["forms"]),
        "legal_form_spellings": _shares(total["form_spellings"], 20),
        "common_name_tokens": _shares(total["tokens"], 20),
        "identifier_coverage": {
            key: round(value / max(1, kinds.total()), 4)
            for key, value in _ordered(total["coverage"]).items()
        },
        "top_states": _shares(total["seen_states"], 8),
    }


def _collision(exact: collections.Counter, bare: collections.Counter) -> dict[str, Any]:
    """How often a name is already taken.

    The second number is the one that matters: strip the legal form and the
    articles, and a register is mostly collisions. Any resolution strategy
    that treats a name as an identifier is working on the 8% that are unique.
    """
    repeated_exact = sum(1 for v in exact.values() if v > 1)
    repeated_bare = sum(1 for v in bare.values() if v > 1)
    return {
        "distinct_names": len(exact),
        "repeated_exact_share": round(repeated_exact / max(1, len(exact)), 4),
        "distinct_names_without_legal_form": len(bare),
        "repeated_without_legal_form_share": round(repeated_bare / max(1, len(bare)), 4),
    }


# ------------------------------------------------------------ generated


def measure_run(domain: str, scale: float, seed: int) -> dict[str, Any]:
    """The same statistics for a GraphFaker dataset.

    A metric the domain does not model at all is reported as ``null`` rather
    than zero, because "we do not generate addresses" and "every company has
    its own address" are different statements and only one of them is a
    realism gap we could close by tuning.
    """
    import polars as pl

    from graphfaker.domains import get

    run = get(domain).run(seed=seed, scale=scale)
    nodes, edges = run.tables.nodes, run.tables.edges
    organizations = next(
        (nodes[label] for label in ("Supplier", "Organization", "Company") if label in nodes),
        None,
    )
    if organizations is None:
        raise SystemExit(f"{domain} has no organisation-like node type to measure")

    # The key is the whole address, the way the reference builds it, so that
    # two companies on the same street at different numbers are two
    # addresses and two at the same door are one.
    per_address: collections.Counter = collections.Counter()
    parts = [
        c for c in ("address_line1", "addr_line1", "address", "street")
        if c in organizations.columns
    ]
    if parts:
        extra = [c for c in ("address_city", "address_state", "address_postal_code") if c in organizations.columns]
        keys = organizations.select(parts[:1] + extra).with_columns(
            pl.concat_str(parts[:1] + extra, separator="|").alias("_key")
        )["_key"]
        per_address.update(keys.to_list())

    forms: collections.Counter = collections.Counter()
    tokens: collections.Counter = collections.Counter()
    exact_names: collections.Counter = collections.Counter()
    bare_names: collections.Counter = collections.Counter()
    spellings: collections.Counter = collections.Counter()
    if "name" in organizations.columns:
        for name in organizations["name"].to_list():
            exact_names[str(name).strip().upper()] += 1
            bare_names[normalise(str(name))] += 1
            forms[legal_form(str(name))] += 1
            spellings[suffix_spelling(str(name))] += 1
            tokens.update(normalise(str(name)).split())

    # Sites and people, where the domain has them at all.
    per_org_sites: collections.Counter = collections.Counter()
    for relationship in ("SUBCONTRACTS", "HAS_SITE", "OWNS"):
        if relationship in edges:
            per_org_sites.update(edges[relationship]["source"].to_list())
            break

    # People per company, and companies per person name. A person record in a
    # register points at the company, so the company is the edge's target.
    per_org_people: collections.Counter = collections.Counter()
    per_person_name: collections.Counter = collections.Counter()
    people = nodes.get("Person")
    for relationship in ("CONTACT_AT", "EXECUTIVE_AT", "EMPLOYS", "WORKS_AT", "OFFICER_OF"):
        frame = edges.get(relationship)
        if frame is None:
            continue
        target = "target" if "target" in frame.columns else "source"
        per_org_people.update(frame[target].to_list())
        if people is not None and "name" in people.columns:
            named = dict(zip(people["id"].to_list(), people["name"].to_list(), strict=False))
            per_person_name.update(
                str(named[person]).strip().upper()
                for person in frame["source"].to_list()
                if person in named
            )

    coverage = {
        column: round(
            float((organizations[column].is_not_null()).mean() or 0.0), 4
        )
        for column in organizations.columns
        if column != "id"
    }
    return {
        "label": f"graphfaker {domain} scale={scale}",
        "kind": "generated",
        "measured_at": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "records": sum(frame.height for frame in nodes.values()),
        "record_types": {label: frame.height for label, frame in nodes.items()},
        "distributions": [
            summarise(per_address, "organizations_per_address"),
            summarise(per_org_people, "people_per_organization"),
            summarise(per_org_sites, "sites_per_organization"),
            summarise(per_person_name, "organizations_per_person_name"),
        ],
        # Each row here is one company, so this is the company-level number
        # and belongs under both keys. The register's ``name_collision``
        # counts every organisation record, locations included, and comparing
        # the two was what produced the claim that our names were a hundred
        # times too unique when they are in fact a little too shared.
        "name_collision": _collision(exact_names, bare_names),
        "name_collision_companies": _collision(exact_names, bare_names),
        "shared_names": summarise(bare_names, "companies_per_name_stem"),
        "legal_form_mix": _shares(forms),
        "legal_form_spellings": _shares(spellings, 20),
        "common_name_tokens": _shares(tokens, 20),
        "identifier_coverage": coverage,
        "top_states": {},
    }


# -------------------------------------------------------------- compare

#: What each metric is for, in one line, so a gap reads as a consequence
#: rather than as a number that differs.
WHY = {
    "organizations_per_address": "address blocking: one registered agent can hold tens of thousands",
    "people_per_organization": "board and contact density",
    "sites_per_organization": "corporate hierarchy: branches and headquarters",
    "organizations_per_person_name": "name collisions between real people",
}


def compare(reference: dict[str, Any], generated: dict[str, Any]) -> str:
    lines = [
        f"{reference['label']}  vs  {generated['label']}",
        "",
        f"{'metric':<32}{'real p50':>9}{'real p99':>9}{'real max':>10}{'real top1%':>11}"
        f"{'ours p50':>10}{'ours p99':>9}{'ours max':>10}{'ours top1%':>11}",
    ]
    ours = {d["metric"]: d for d in generated["distributions"]}
    for row in reference["distributions"]:
        mine = ours.get(row["metric"], {})
        modelled = mine.get("rows", 0) > 0
        values = (
            f"{mine['p50']:>10}{mine['p99']:>9}{mine['max']:>10}{mine['top1pct_share']:>11.1%}"
            if modelled
            else f"{'not modelled':>40}"
        )
        lines.append(
            f"{row['metric']:<32}{row['p50']:>9}{row['p99']:>9}{row['max']:>10}"
            f"{row['top1pct_share']:>11.1%}{values}"
        )
    lines.append("")
    for metric, why in WHY.items():
        lines.append(f"  {metric}: {why}")

    # Companies against companies. The register's other number, over every
    # organisation record, is one company's name repeated across its
    # locations: a real resolution problem, and not this one.
    real = reference.get("name_collision_companies") or reference["name_collision"]
    mine = generated.get("name_collision_companies") or generated["name_collision"]
    records = reference["name_collision"]
    lines += [
        "",
        "two different companies with the same name (share of distinct names used twice or more)",
        f"  as written      real {real['repeated_exact_share']:>7.2%}   ours {mine['repeated_exact_share']:>7.2%}",
        f"  legal form off  real {real['repeated_without_legal_form_share']:>7.2%}"
        f"   ours {mine['repeated_without_legal_form_share']:>7.2%}",
        "",
        "one company's name written on several records (every organisation record, locations included)",
        f"  as written      real {records['repeated_exact_share']:>7.2%}   ours: locations are not separate records",
    ]
    shared_real = reference.get("shared_names") or {}
    shared_mine = generated.get("shared_names") or {}
    if shared_real.get("rows"):
        lines.append(
            f"  busiest stem    real {shared_real.get('max', 0):>7}   "
            f"ours {shared_mine.get('max', 0):>7}  companies on one name"
        )
    lines += ["", "legal form, as written"]
    spellings = reference.get("legal_form_spellings") or reference["legal_form_mix"]
    ours_spellings = generated.get("legal_form_spellings") or generated["legal_form_mix"]
    for form in sorted(set(spellings) | set(ours_spellings), key=lambda f: -spellings.get(f, 0.0)):
        lines.append(
            f"  {form:<14} real {spellings.get(form, 0.0):>7.2%}"
            f"   ours {ours_spellings.get(form, 0.0):>7.2%}"
        )

    lines += ["", "identifier coverage (share of records carrying it)"]
    for key, value in list(reference["identifier_coverage"].items())[:8]:
        lines.append(f"  {key:<20} real {value:>7.1%}")
    always = [k for k, v in generated["identifier_coverage"].items() if v >= 0.999]
    lines.append(
        f"  ours: {len(always)} of {len(generated['identifier_coverage'])} attributes are present on every row"
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--source", type=Path, help="a register in Senzing JSONL format: a file, a directory or a zip"
    )
    parser.add_argument(
        "--state",
        action="append",
        default=[],
        help="restrict the per-entity distributions to companies in this state (repeatable)",
    )
    parser.add_argument("--label", default="reference register")
    parser.add_argument(
        "--licence",
        default=None,
        help="the register's licence, recorded in the output (e.g. CDLA-Permissive-2.0)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="stop after N records, per shard when sharded"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(12, os.cpu_count() or 1),
        help="processes to spread the shards over; 1 to run in this one",
    )
    parser.add_argument("--out", type=Path, help="write the statistics here")
    parser.add_argument("--compare", type=Path, help="a statistics file to compare against")
    parser.add_argument("--domain", default="supply_chain")
    parser.add_argument("--scale", type=float, default=0.01)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if args.source:
        stats = measure_source(
            args.source, args.label, args.limit, tuple(args.state), args.workers, args.licence
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(stats, indent=1), encoding="utf-8")
            print(f"wrote {args.out} ({args.out.stat().st_size // 1024} KB, {stats['records']:,} records)")
        else:
            print(json.dumps(stats, indent=1))

    if args.compare:
        reference = json.loads(args.compare.read_text(encoding="utf-8"))
        print(compare(reference, measure_run(args.domain, args.scale, args.seed)))


if __name__ == "__main__":
    main()
