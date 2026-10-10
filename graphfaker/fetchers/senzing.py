"""Load a register in Senzing's entity resolution format.

Senzing's input format is a JSON object per line: a ``DATA_SOURCE``, a
``RECORD_ID`` and a list of ``FEATURES``, each feature a small dictionary of
attributes. Relationships are expressed by pointer rather than by edge: a
record carries ``REL_ANCHOR_KEY`` to say "this is the thing with key K", or
``REL_POINTER_KEY`` with a ``REL_POINTER_ROLE`` to say "this belongs to the
thing with key K, as a branch, a headquarters, an officer, a contact".

This reads that into node and edge tables. It is a loader, not a generator:
there is no seed, no ground truth and no labels, the same deal as the
OpenStreetMap and flight fetchers. You bring the file; GraphFaker does not
ship one, and nothing here uploads or downloads anything.

    graphfaker gen --fetcher senzing --path register.jsonl --export register.graphml

A path can be one file, a directory of them, or a zip, because an export of
any size arrives sharded: the national open data set is 2,493 files in three
directories inside one archive. The shards that carry companies are read
first, decided by reading one line of each rather than by their names, since
a pointer resolves the moment its anchor is known and holding 80 million
unresolved pointers is not an option. ``--state`` and
``--city`` keep the organisations in one place along with their locations and
their officers wherever those live, which is how a national export becomes a
graph that fits in memory.

    graphfaker gen --fetcher senzing --path ODO_SENZING.zip --state NV --city "Las Vegas"

A register also goes where a generated dataset goes. ``read_tables`` gives
node and edge frames and ``write_dataset`` writes them with a manifest, so
DuckDB, LadybugDB, a live Neo4j and the admin import layout all take a
register through the same sinks:

    graphfaker register ODO_SENZING.zip --state NV --out ./nevada --sink duckdb
    graphfaker load ladybug ./nevada --db nevada.lbdb

Two reasons to want it. A real register is the reference for what a
generated one should look like, which is what ``benchmarks/realism/
corporate.py`` measures. And it is a genuine entity resolution problem with
no answer key, which is the gap a generated dataset fills: see
``docs/domains/supply-chain.md`` and the corporate work in the plan.

Reading is where the time goes at this size: of a pass over the national
archive, 18% is decompression, 59% is parsing JSON and 23% is the work being
done with the records. So ``orjson`` is used when it is installed, which is
most of the parsing cost halved, and nothing else changes; stdlib ``json``
is the fallback and the results are identical either way.

A register is also real people. Officers carry names, home addresses and
social profiles, so treat a loaded graph as personal data: it is not
anonymous because it is public.
"""

from __future__ import annotations

import collections
import datetime as dt
import gzip
import hashlib
import io
import json
import zipfile
from collections.abc import Callable, Iterator
from importlib.metadata import version
from pathlib import Path
from typing import Any

import networkx as nx
import polars as pl

from graphfaker.backends.tables import ID, SOURCE, TARGET, GraphTables
from graphfaker.engine.run import Manifest
from graphfaker.logger import logger

try:  # pragma: no cover - a speed-up, not a feature
    import orjson

    def _loads(line: str) -> Any:
        return orjson.loads(line)

    _DECODE_ERROR: tuple[type[Exception], ...] = (orjson.JSONDecodeError, ValueError)
except ModuleNotFoundError:  # pragma: no cover
    def _loads(line: str) -> Any:
        return json.loads(line)

    _DECODE_ERROR = (json.JSONDecodeError,)

#: Attributes lifted onto a node when present, in the order they are tried.
#: Senzing spreads one record's attributes over several feature dictionaries,
#: so a node is the union of its features rather than any single one.
NODE_ATTRIBUTES = (
    "NAME_ORG", "NAME_FULL", "NAME_FIRST", "NAME_MIDDLE", "NAME_LAST", "NAME_TYPE",
    "RECORD_TYPE", "ADDR_LINE1", "ADDR_LINE2", "ADDR_CITY", "ADDR_STATE",
    "ADDR_POSTAL_CODE", "ADDR_COUNTRY", "ADDR_TYPE",
    "GEO_LATITUDE", "GEO_LONGITUDE", "PLACEKEY",
    "WEBSITE_ADDRESS", "LINKEDIN", "LEI_NUMBER", "NPI_NUMBER",
    "OTHER_ID_TYPE", "OTHER_ID_NUMBER",
    "BQ_ID", "GROUP_ASSN_ID_TYPE", "GROUP_ASSN_ID_NUMBER",
)

#: Attributes a register writes inconsistently: a float in one shard and a
#: string in the next. Coerced on the way in, because an exporter that gets
#: two types for one property writes a broken file.
NUMERIC_ATTRIBUTES = ("GEO_LATITUDE", "GEO_LONGITUDE")

#: Suffix for the values of an attribute a record carries more than once.
#: A company with two ``NAME_ORG`` features has an alias, and an alias is
#: the whole problem in entity resolution, so the extras are kept as a
#: pipe-joined string beside the first rather than dropped.
ALSO_SUFFIX = "_ALSO"

#: How many repeats are kept, so one pathological record cannot put a
#: kilobyte of aliases on a node.
ALSO_LIMIT = 5

#: The relationship a pointer role becomes. Anything not listed keeps its
#: own name, upper-cased, because a register may carry roles this does not
#: know about and dropping them would be worse than guessing.
ROLE_RELATIONSHIPS = {
    "HEADQUARTERS": "HEADQUARTERS_OF",
    "BRANCH": "BRANCH_OF",
    "BUSINESS": "SITE_OF",
    "INDEPENDENT": "SITE_OF",
    "Contact": "CONTACT_AT",
    "Executive": "EXECUTIVE_AT",
}

#: What counts as a shard. ``.json`` is in the list because a register's
#: shards are JSON lines whatever they are called.
MEMBER_SUFFIXES = (".json", ".jsonl", ".ndjson")


def _is_member(name: str) -> bool:
    stem = name[:-3] if name.lower().endswith(".gz") else name
    return Path(stem).suffix.lower() in MEMBER_SUFFIXES


def _carries_anchors(lines: Callable[[], Iterator[str]]) -> bool:
    """Whether a shard's first record is an anchor.

    One line per shard, read and thrown away. Guessing from the file name
    does not work: in the national export every location shard is called
    ``bq_organization_locations_...``, so a name containing "organization"
    sorted the pointer shards first and resolved nothing. A shard holds one
    kind of record, so the first line settles it.
    """
    try:
        for line in lines():
            line = line.strip()
            if not line:
                continue
            return _anchor(json.loads(line)) is not None
    except (OSError, json.JSONDecodeError, zipfile.BadZipFile):
        return False
    return False


def _anchors_first(
    named: list[tuple[str, Callable[[], Iterator[str]]]],
) -> list[tuple[str, Callable[[], Iterator[str]]]]:
    if len(named) < 2:
        return named
    return sorted(named, key=lambda nr: (0 if _carries_anchors(nr[1]) else 1, nr[0]))


def _file_lines(path: Path) -> Iterator[str]:
    opener = gzip.open if path.name.lower().endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:  # type: ignore[operator]
        yield from handle


def _zip_lines(archive: zipfile.ZipFile, member: str) -> Iterator[str]:
    with archive.open(member) as raw:
        if member.lower().endswith(".gz"):
            with gzip.open(raw, "rt", encoding="utf-8", errors="replace") as text:
                yield from text
        else:
            yield from io.TextIOWrapper(raw, encoding="utf-8", errors="replace")


def _shards(path: str | Path) -> list[tuple[str, Callable[[], Iterator[str]]]]:
    """Every shard at ``path`` by name, in name order and unprobed."""
    path = Path(path)
    if path.is_dir():
        found = [p for p in path.rglob("*") if p.is_file() and _is_member(p.name)]
        if not found:
            raise FileNotFoundError(f"no register shards under {path}")
        by_name = {str(p.relative_to(path)).replace("\\", "/"): p for p in found}
        return [(name, lambda p=p: _file_lines(p)) for name, p in sorted(by_name.items())]
    if not path.is_file():
        raise FileNotFoundError(f"no register at {path}")
    if path.suffix.lower() == ".zip":
        # One handle for the archive, held by the readers: reopening a
        # 21 GB zip re-reads its central directory, which over 2,493 shards
        # is a minute of work before a single record is parsed.
        archive = zipfile.ZipFile(path)
        members = [i.filename for i in archive.infolist() if not i.is_dir() and _is_member(i.filename)]
        if not members:
            raise FileNotFoundError(f"no register shards in {path}")
        return [(m, lambda m=m: _zip_lines(archive, m)) for m in sorted(members)]
    return [(path.name, lambda: _file_lines(path))]


def sources(path: str | Path) -> list[tuple[str, Callable[[], Iterator[str]]]]:
    """The shards at ``path``, each as a name and a way to read its lines.

    One file, a directory read recursively, or a zip. Ordered with the
    anchor-bearing shards first, which costs one line read per shard; see
    :func:`_carries_anchors`.
    """
    return _anchors_first(_shards(path))


def shard_names(path: str | Path) -> list[tuple[str, bool]]:
    """Each shard's name and whether it carries anchor records.

    What a parallel pass needs: the names are small enough to send to a
    worker, and the flag says which shards have to be read first.
    """
    return [(name, _carries_anchors(reader)) for name, reader in _shards(path)]


def read_shard(path: str | Path, name: str, limit: int | None = None) -> Iterator[dict[str, Any]]:
    """The records of one shard, by the name :func:`shard_names` gave it.

    A worker in a parallel pass takes a name rather than a reader, because a
    file handle does not travel between processes.
    """
    readers = dict(_shards(path))
    if name not in readers:
        raise FileNotFoundError(f"{path} has no shard called {name}")
    yield from _parse(readers[name](), name, limit)


def read_records(path: str | Path, limit: int | None = None) -> Iterator[dict[str, Any]]:
    """Records from a register, one at a time.

    Streamed, because a register is routinely a gigabyte and the national
    export is a hundred and fifty of them, so there is no reason to hold
    one. A line that is not JSON is skipped with a warning rather than
    killing a load two thirds of the way through. ``limit`` counts records
    across every shard, not per shard.
    """
    read = 0
    for name, lines in sources(path):
        if limit is not None and read >= limit:
            break
        remaining = None if limit is None else limit - read
        for record in _parse(lines(), name, remaining):
            read += 1
            yield record


def _parse(lines: Iterator[str], name: str, limit: int | None = None) -> Iterator[dict[str, Any]]:
    """Records from one shard's lines, skipping what is not JSON.

    A line that will not parse is skipped with a warning rather than killing
    a load two thirds of the way through a hundred and fifty gigabytes.
    """
    bad = read = 0
    for number, line in enumerate(lines, start=1):
        if limit is not None and read >= limit:
            break
        line = line.strip()
        if not line:
            continue
        try:
            record = _loads(line)
        except _DECODE_ERROR:
            bad += 1
            if bad <= 3:
                logger.warning("senzing: %s line %d is not valid JSON, skipping", name, number)
            continue
        read += 1
        yield record
    if bad:
        logger.warning("senzing: %s: skipped %d unparsable line(s)", name, bad)


def _flatten(record: dict[str, Any]) -> dict[str, Any]:
    """One record's features as a single attribute dictionary.

    Nulls are dropped rather than carried: a register writes
    ``NAME_FULL: null`` where it has no name, and an attribute that is
    present and empty is worse than an absent one for anything downstream
    that counts coverage.

    An attribute a record carries twice keeps its first value under its own
    name and the rest under ``<KEY>_ALSO``, up to :data:`ALSO_LIMIT`. In the
    national export 552 companies in every half million have a second
    ``NAME_ORG``, which is an alias, and throwing those away would be
    throwing away the hardest part of resolving the register.
    """
    merged: dict[str, Any] = {}
    extra: dict[str, list[str]] = {}
    for feature in record.get("FEATURES", []):
        for key, value in feature.items():
            if value is None or key not in NODE_ATTRIBUTES:
                continue
            if key in NUMERIC_ATTRIBUTES:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
            if key not in merged:
                merged[key] = value
            elif merged[key] != value:
                also = extra.setdefault(key, [])
                if len(also) < ALSO_LIMIT and str(value) not in also:
                    also.append(str(value))
    for key, values in extra.items():
        merged[key + ALSO_SUFFIX] = "|".join(values)
    return merged


def _pointer(record: dict[str, Any]) -> tuple[Any, str] | None:
    for feature in record.get("FEATURES", []):
        if "REL_POINTER_KEY" in feature:
            return feature["REL_POINTER_KEY"], str(feature.get("REL_POINTER_ROLE", "RELATED"))
    return None


def _anchor(record: dict[str, Any]) -> Any | None:
    for feature in record.get("FEATURES", []):
        if "REL_ANCHOR_KEY" in feature:
            return feature["REL_ANCHOR_KEY"]
    return None


def _matches(attributes: dict[str, Any], states: tuple[str, ...], cities: tuple[str, ...]) -> bool:
    if states and str(attributes.get("ADDR_STATE", "")).upper() not in states:
        return False
    if cities and str(attributes.get("ADDR_CITY", "")).upper() not in cities:
        return False
    return True


def _label(record_type: str) -> str:
    """``ORGANIZATION`` as ``Organization``: a node type is a label and a
    table name, and those are written the way the rest of GraphFaker writes
    them. The register's own spelling stays on the row as ``record_type``."""
    return "".join(part.capitalize() for part in str(record_type).split("_")) or "Unknown"


def load(
    path: str | Path,
    limit: int | None = None,
    record_types: tuple[str, ...] | None = None,
    states: tuple[str, ...] | None = None,
    cities: tuple[str, ...] | None = None,
) -> tuple[list[tuple[str, str, dict[str, Any]]], list[tuple[str, str, str, str]]]:
    """A register's records and the relationships between them.

    Returns ``(nodes, edges)``: each node is ``(record_id, record_type,
    attributes)`` and each edge ``(source, target, relationship, role)``.
    The whole thing is held, which is what makes a graph or a set of tables
    possible and what makes a national export impossible; see the filters.

    This is where pointers become edges. The same walk serves
    :meth:`SenzingFetcher.fetch_graph` and :func:`read_tables`, because the
    awkward part is resolving the pointers and it should only exist once.
    """
    states = tuple(s.upper() for s in states) if states else ()
    cities = tuple(c.upper() for c in cities) if cities else ()
    filtering = bool(states or cities)

    nodes: list[tuple[str, str, dict[str, Any]]] = []
    anchors: dict[Any, str] = {}
    pending: list[tuple[str, Any, str]] = []
    skipped = out_of_scope = 0
    anchors_came_first = None

    for record in read_records(path, limit):
        record_id = str(record.get("RECORD_ID", ""))
        if not record_id:
            continue
        attributes = _flatten(record)
        kind = attributes.get("RECORD_TYPE", "UNKNOWN")
        if record_types and kind not in record_types:
            skipped += 1
            continue

        anchor = _anchor(record)
        pointer = _pointer(record)
        if anchors_came_first is None:
            anchors_came_first = anchor is not None
        if filtering:
            if anchor is not None:
                if not _matches(attributes, states, cities):
                    skipped += 1
                    continue
            elif pointer is not None:
                if pointer[0] not in anchors:
                    # Its company was not kept: either the filter dropped it,
                    # or it is not in these shards at all. Nearly always the
                    # former, which is the filter doing its job, so this is
                    # counted and not warned about: a Nevada load of the
                    # national export drops two million pointers per three
                    # company shards.
                    out_of_scope += 1
                    skipped += 1
                    continue
            else:
                skipped += 1
                continue

        attributes.setdefault("data_source", record.get("DATA_SOURCE"))
        nodes.append((record_id, kind, attributes))
        if anchor is not None:
            anchors[anchor] = record_id
        if pointer is not None:
            pending.append((record_id, pointer[0], pointer[1]))

    # Pointers are resolved after the pass, because a record can point at an
    # anchor that appears later in the file.
    edges: list[tuple[str, str, str, str]] = []
    dangling = 0
    for source, key, role in pending:
        target = anchors.get(key)
        if target is None:
            dangling += 1
            continue
        if target == source:
            continue
        edges.append((source, target, ROLE_RELATIONSHIPS.get(role, role.upper()), role))

    logger.info(
        "senzing: %d records (%d skipped), %d anchors, %d edges, %d pointers with no anchor",
        len(nodes), skipped, len(anchors), len(edges), dangling,
    )
    if out_of_scope:
        logger.info(
            "senzing: %d pointer records belong to companies the filter did not keep",
            out_of_scope,
        )
    if filtering and not anchors:
        logger.warning(
            "senzing: no company matched %s, so there was nothing to attach anything to",
            ", ".join(states + cities),
        )
    elif filtering and anchors_came_first is False:
        logger.warning(
            "senzing: the first shard read carried no companies, so pointers into companies "
            "read later were dropped; load a path whose company shards come first"
        )
    return nodes, edges


def read_tables(
    path: str | Path,
    limit: int | None = None,
    record_types: tuple[str, ...] | None = None,
    states: tuple[str, ...] | None = None,
    cities: tuple[str, ...] | None = None,
) -> GraphTables:
    """A register as node and edge tables.

    One node frame per record type (``Organization``, ``Person``) and one
    edge frame per relationship (``BRANCH_OF``, ``EXECUTIVE_AT``), which is
    what every sink in GraphFaker takes: Parquet, DuckDB, LadybugDB, a live
    Neo4j, the admin import layout. A register has no ground truth and no
    seed, so there is no ``truth/`` and nothing to reproduce; what it has is
    a shape, and the shape is the same as a generated dataset's.

    Records of one type do not all carry the same attributes, which is the
    point of a register, so a column a record lacks is null rather than
    absent.
    """
    nodes, edges = load(path, limit, record_types, states, cities)

    by_type: dict[str, list[dict[str, Any]]] = {}
    for record_id, kind, attributes in nodes:
        row = {ID: record_id, "record_type": kind, **attributes}
        row.pop("RECORD_TYPE", None)
        by_type.setdefault(_label(kind), []).append(row)

    by_relationship: dict[str, list[dict[str, Any]]] = {}
    for source, target, relationship, role in edges:
        by_relationship.setdefault(relationship, []).append(
            {SOURCE: source, TARGET: target, "role": role}
        )

    return GraphTables(
        nodes={
            name: pl.from_dicts(rows, infer_schema_length=None)
            for name, rows in sorted(by_type.items())
        },
        edges={
            name: pl.from_dicts(rows, infer_schema_length=None)
            for name, rows in sorted(by_relationship.items())
        },
    )


def write_dataset(
    tables: GraphTables, out: str | Path, source: str | Path, label: str | None = None
) -> Path:
    """Write ``nodes/``, ``edges/`` and ``manifest.json`` for a register.

    No ``schema.yaml``: a schema in GraphFaker says how to generate
    something, with shares of an edge budget and samplers per attribute, and
    a register was not generated. The digest in the manifest is of the shape
    that was loaded, so two loads of the same cut of the same register match
    and a different cut does not.
    """
    root = Path(out)
    tables.write_parquet(root)
    shape = json.dumps(
        {
            "nodes": {k: v.height for k, v in sorted(tables.nodes.items())},
            "edges": {k: v.height for k, v in sorted(tables.edges.items())},
            "columns": {k: sorted(v.columns) for k, v in sorted(tables.nodes.items())},
        },
        sort_keys=True,
    )
    Manifest(
        schema_name="register",
        schema_digest=hashlib.sha256(shape.encode()).hexdigest()[:16],
        seed=None,
        shard_size=0,
        graphfaker_version=version("graphfaker"),
        created_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        node_counts={k: v.height for k, v in tables.nodes.items()},
        edge_counts={k: v.height for k, v in tables.edges.items()},
        extra={
            "register": {
                "source": str(source),
                "label": label or Path(str(source)).name,
                "format": "senzing",
                "loaded": "no seed and no ground truth: this was read, not generated",
            }
        },
    ).write(root / "manifest.json")
    return root


class SenzingFetcher:
    """A register as a graph: organisations, people and what connects them."""

    @staticmethod
    def fetch_graph(
        path: str | Path,
        limit: int | None = None,
        record_types: tuple[str, ...] | None = None,
        states: tuple[str, ...] | None = None,
        cities: tuple[str, ...] | None = None,
    ) -> nx.DiGraph:
        """Read ``path`` into a directed graph.

        Nodes are records, keyed by ``RECORD_ID`` and typed by
        ``RECORD_TYPE`` (``ORGANIZATION`` or ``PERSON``), carrying whatever
        attributes the record had. Edges run from the pointing record to the
        thing it points at, named for the role: a branch becomes
        ``BRANCH_OF``, an officer ``EXECUTIVE_AT``.

        ``record_types`` keeps only the kinds asked for, and ``limit`` stops
        after that many records, which is how to look at a gigabyte without
        reading a gigabyte.

        ``states`` and ``cities`` cut a national export down to one place.
        They are matched against the anchor records, the companies, and a
        pointer record is kept when the company it points at was kept: an
        officer who lives in another state still belongs to the company. The
        filter needs the company shards read before the pointer shards,
        which is what the ordering in :func:`sources` is for. A pointer whose
        company was not kept is dropped and counted, which is the ordinary
        case under a filter rather than a problem; a load whose first shard
        carries no companies at all says so.
        """
        graph = nx.DiGraph()
        nodes, edges = load(path, limit, record_types, states, cities)
        for record_id, kind, attributes in nodes:
            graph.add_node(record_id, **{**attributes, "type": kind})
        for source, target, relationship, role in edges:
            graph.add_edge(source, target, relationship=relationship, role=role)
        return graph


def report(path: str | Path, limit: int | None = None) -> dict[str, Any]:
    """What a register contains, and where it departs from the format.

    A conformance pass rather than a load: it streams the records, counts
    what it sees, and keeps nothing. The point is to find out before a load
    whether the attributes, the record types and the pointer roles are ones
    this knows about, because a key nobody has seen before is silently
    dropped on the way into a graph and a role nobody has seen becomes an
    edge named after itself.

    ``limit`` is spread over the shards rather than spent on the first one,
    so a sample of a sharded export sees every kind of record in it. The
    whole national archive reads at about 28,000 records a second, which is
    three and a half hours; a few hundred thousand spread across the shards
    answers the format question in seconds.
    """
    counts: collections.Counter[str] = collections.Counter()
    datasets: collections.Counter[str] = collections.Counter()
    types: collections.Counter[str] = collections.Counter()
    roles: collections.Counter[str] = collections.Counter()
    attributes: collections.Counter[str] = collections.Counter()
    by_type: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    repeated: collections.Counter[str] = collections.Counter()
    value_types: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    shard_kinds: collections.Counter[str] = collections.Counter()

    shards = sources(path)
    per_shard = None if limit is None else max(1, -(-limit // len(shards)))
    read = 0
    bad = 0

    for name, lines in shards:
        if limit is not None and read >= limit:
            break
        here = 0
        kinds: set[str] = set()
        for line in lines():
            if per_shard is not None and here >= per_shard:
                break
            if limit is not None and read >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                record = _loads(line)
            except _DECODE_ERROR:
                bad += 1
                continue
            here += 1
            read += 1
            counts["records"] += 1
            if not record.get("RECORD_ID"):
                counts["no_record_id"] += 1
            if not record.get("DATA_SOURCE"):
                counts["no_data_source"] += 1
            datasets[str(record.get("bq_dataset") or record.get("DATA_SOURCE") or "?")] += 1

            seen: collections.Counter[str] = collections.Counter()
            kind = "?"
            for feature in record.get("FEATURES", []):
                if "RECORD_TYPE" in feature:
                    kind = str(feature["RECORD_TYPE"])
            has_anchor = has_pointer = False
            for feature in record.get("FEATURES", []):
                if not feature:
                    counts["empty_features"] += 1
                for key, value in feature.items():
                    attributes[key] += 1
                    by_type[kind][key] += 1
                    seen[key] += 1
                    value_types[key][type(value).__name__] += 1
                    if value is None:
                        counts["null_values"] += 1
                    if key == "RECORD_TYPE":
                        types[str(value)] += 1
                    elif key == "REL_POINTER_ROLE":
                        roles[str(value)] += 1
                    elif key == "REL_ANCHOR_KEY":
                        has_anchor = True
                    elif key == "REL_POINTER_KEY":
                        has_pointer = True
            for key, n in seen.items():
                if n > 1:
                    repeated[key] += 1
            counts["anchors"] += has_anchor
            counts["pointers"] += has_pointer
            counts["unlinked"] += not (has_anchor or has_pointer)
            kinds.add("anchor" if has_anchor else "pointer" if has_pointer else "unlinked")
        if here:
            shard_kinds["+".join(sorted(kinds))] += 1
        del name

    counts["bad_lines"] = bad
    known = set(NODE_ATTRIBUTES) | {
        "REL_ANCHOR_DOMAIN", "REL_ANCHOR_KEY",
        "REL_POINTER_DOMAIN", "REL_POINTER_KEY", "REL_POINTER_ROLE",
    }
    problems = []
    unknown = {k: n for k, n in attributes.items() if k not in known}
    if unknown:
        problems.append(
            "attributes this loader drops: "
            + ", ".join(f"{k} ({n:,})" for k, n in sorted(unknown.items(), key=lambda kv: -kv[1]))
        )
    conflicted = {
        k: dict(v) for k, v in value_types.items() if len({t for t in v if t != "NoneType"}) > 1
    }
    for key, seen_types in sorted(conflicted.items()):
        fix = ", coerced to float on load" if key in NUMERIC_ATTRIBUTES else ""
        problems.append(f"{key} arrives as more than one type: {seen_types}{fix}")
    for key, n in repeated.most_common():
        problems.append(
            f"{key} appears more than once on {n:,} records; the first is loaded under "
            f"{key} and the rest under {key}{ALSO_SUFFIX}"
        )
    strange = [r for r in roles if r not in ROLE_RELATIONSHIPS]
    if strange:
        problems.append("pointer roles with no mapping, named after themselves: " + ", ".join(sorted(strange)))
    if counts["bad_lines"]:
        problems.append(f"{counts['bad_lines']:,} lines were not JSON")
    if counts["unlinked"]:
        problems.append(f"{counts['unlinked']:,} records carry neither an anchor nor a pointer")

    return {
        "path": str(path),
        "shards": len(shards),
        "shards_read": sum(shard_kinds.values()),
        "shard_kinds": dict(shard_kinds),
        "counts": dict(counts),
        "datasets": dict(datasets),
        "record_types": dict(types),
        "pointer_roles": dict(roles),
        "attributes": dict(attributes.most_common()),
        "attributes_by_record_type": {
            k: dict(v.most_common()) for k, v in sorted(by_type.items())
        },
        "repeated_attributes": dict(repeated.most_common()),
        "value_types": {k: dict(v) for k, v in sorted(value_types.items())},
        "problems": problems,
    }


def format_report(data: dict[str, Any]) -> str:
    """The report as a page of text."""
    counts = data["counts"]
    lines = [
        f"register: {data['path']}",
        f"shards: {data['shards_read']} read of {data['shards']}"
        + (
            "  (" + ", ".join(f"{k} {v}" for k, v in sorted(data["shard_kinds"].items())) + ")"
            if data.get("shard_kinds")
            else ""
        ),
        f"records: {counts.get('records', 0):,}"
        f"  anchors: {counts.get('anchors', 0):,}"
        f"  pointers: {counts.get('pointers', 0):,}",
        "",
        "record types: " + ", ".join(f"{k} {v:,}" for k, v in sorted(data["record_types"].items())),
        "datasets:     " + ", ".join(f"{k} {v:,}" for k, v in sorted(data["datasets"].items())),
        "",
        "pointer roles",
    ]
    for role, n in sorted(data["pointer_roles"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {role:<16} {n:>12,}  -> {ROLE_RELATIONSHIPS.get(role, role.upper())}")
    known = set(NODE_ATTRIBUTES)
    for kind, found in data.get("attributes_by_record_type", {}).items():
        total = data["record_types"].get(kind, counts.get("records", 1))
        lines += ["", f"attributes on {kind} records ({total:,})"]
        for key, n in found.items():
            if key.startswith("REL_"):
                continue
            mark = " " if key in known else "*"
            lines.append(
                f" {mark}{key:<24} {n:>12,}  {100.0 * n / max(total, 1):>5.1f}%"
            )
    lines += ["", "* not loaded onto a node", ""]
    if data["problems"]:
        lines.append("what to know before loading this")
        lines += [f"  - {p}" for p in data["problems"]]
    else:
        lines.append("nothing to flag: every attribute, type and role is one this loader knows.")
    return "\n".join(lines)
