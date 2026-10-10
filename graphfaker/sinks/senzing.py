"""A generated dataset as Senzing-format records, with the answer kept aside.

Entity resolution has no public benchmark worth the name. The datasets where
resolution matters are registers, and a register has no answer key: nobody
labelled which of its rows are the same company. The datasets that do carry
labels are usually too clean to be hard. So a resolver's score is either
unmeasurable or uninteresting, and both failures are about the data.

This writes the other thing: records in the format Senzing takes, where the
same company appears several times under several spellings and at addresses
it shares with other companies, plus a separate file saying which records
belong together. Point Senzing, or anything else, at ``records.jsonl``, and
score whatever it returns against ``entities.parquet``.

    graphfaker generate supply_chain --scale 0.01 --sink senzing --out ./chain
    # ./chain/senzing/records.jsonl    the dataset, with no answer in it
    # ./chain/senzing/entities.parquet record_id -> entity_id, the answer

People are here too, and a register's way of holding them is itself the
problem: it has no person entity, only a row per person-company association,
so somebody on three boards is three rows written three ways. Most companies
have nobody at all, since 4.5% of a register's companies have anybody
attached, and the ones that do are mostly filing agents.

The hard parts are the measured ones, and they come from the generator rather
than from here: addresses shared on a register's curve
(:mod:`graphfaker.engine.addresses`), names carrying a register's legal forms
(:mod:`graphfaker.engine.names`) and boards with a floor of zero
(:mod:`graphfaker.engine.people`). What this module adds is the shape a
register has and a table of companies does not: one entity, several records.
How many is drawn from the sites-per-organisation distribution measured in
``benchmarks/realism``, where the median company has one site, the 90th
percentile three and the 99th five. Those extra records are mostly at a
different address, drawn from the dataset's own pool so the measured sharing
curve survives, because a branch being somewhere else is the reason a
register holds a record for it.

The records file never contains an entity id. That is the point of keeping it
in a second file: the dataset you hand to a resolver has to be the dataset
without the answer, and a column nobody meant to read is how an answer leaks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from graphfaker.backends.tables import SOURCE, TARGET
from graphfaker.engine.names import variants
from graphfaker.engine.people import person_variants
from graphfaker.engine.run import GraphRun
from graphfaker.logger import logger

#: ``DATA_SOURCE`` on every record, which is what Senzing groups by.
DATA_SOURCE = "GRAPHFAKER"

#: The pointer domain. A register uses one per source system; a generated
#: dataset has one source.
DOMAIN = "GF"

#: Node types written as organisations, and the role their extra records
#: take. A warehouse is a site of the business rather than a company of its
#: own, so it is not an entity here.
COMPANY_TYPES = ("Supplier", "Plant", "Customer", "Carrier", "Organization", "Company")

#: How many records one company gets, by share: the measured
#: sites-per-organisation shape, median one and a thin tail.
RECORDS_PER_ENTITY = ((1, 0.46), (2, 0.28), (3, 0.16), (4, 0.06), (5, 0.03), (8, 0.01))

#: Role on the records after the first, in the order they are handed out.
#: ``HEADQUARTERS`` and ``BRANCH`` are what a register calls the same company
#: filed again at another address.
EXTRA_ROLES = ("HEADQUARTERS", "BRANCH", "BRANCH", "BUSINESS", "BRANCH", "BUSINESS", "BRANCH")

#: Share of a company's extra records that repeat its first address rather
#: than sitting somewhere else. Assumed, not measured: a register separates a
#: headquarters from its branches by address, which is why it holds a
#: location record per site at all, so most of them differ. Leaving them all
#: identical would make the address a perfect key inside an entity, which is
#: the opposite of the trap it is in real data.
SAME_ADDRESS_RATE = 0.2

#: Node type holding people, and the relationships that attach them to a
#: company, with the register's own spelling of each role.
PERSON_TYPE = "Person"
PERSON_ROLES = {"CONTACT_AT": "Contact", "EXECUTIVE_AT": "Executive"}

#: Share of person records carrying an address. A register has one on 75.3%
#: of them, which is a home address and a reason a loaded register is
#: personal data. Here it is generated.
PERSON_ADDRESS_SHARE = 0.753

ADDRESS_COLUMNS = (
    ("address_line1", "ADDR_LINE1"),
    ("address_city", "ADDR_CITY"),
    ("address_state", "ADDR_STATE"),
    ("address_postal_code", "ADDR_POSTAL_CODE"),
    ("country", "ADDR_COUNTRY"),
)


@dataclass(frozen=True)
class SenzingExport:
    """What was written, in the numbers worth asserting on."""

    records: Path
    entities: Path
    record_count: int
    entity_count: int
    #: Entities with more than one record: the ones a resolver has to join.
    multi_record_entities: int
    #: Records whose name is spelled differently from their entity's first.
    varied_names: int
    #: Records sitting at a different address from their entity's first.
    moved_records: int
    #: Person records, and the people they belong to. A register stores one
    #: row per person-company association rather than one per person, so
    #: somebody on three boards is three records to be resolved.
    person_records: int = 0
    people: int = 0

    def summary(self) -> str:
        return (
            f"senzing: {self.record_count:,} records for {self.entity_count:,} entities "
            f"({self.multi_record_entities:,} with more than one record, "
            f"{self.varied_names:,} spelled differently, {self.moved_records:,} at another "
            f"address), of which {self.person_records:,} records for {self.people:,} people; "
            f"answer in {self.entities.name}"
        )


def _address(row: dict[str, Any]) -> dict[str, Any]:
    """The address features of a row, empty when it has none."""
    address = {
        feature: row[column]
        for column, feature in ADDRESS_COLUMNS
        if row.get(column) is not None
    }
    if address:
        address.setdefault("ADDR_TYPE", "BUSINESS")
    return address


def _features(
    row: dict[str, Any], name: str, kind: str, address: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """One record's features, in the order a register writes them."""
    features: list[dict[str, Any]] = [{"NAME_ORG": name, "NAME_TYPE": "PRIMARY"}]
    features.append({"RECORD_TYPE": "ORGANIZATION"})
    address = _address(row) if address is None else address
    if address:
        features.append(address)
    for column, feature in (("tier", "OTHER_ID_NUMBER"),):
        if row.get(column) is not None:
            features.append({"OTHER_ID_TYPE": f"GF_{column.upper()}", feature: str(row[column])})
    del kind
    return features


def _counts(rng: np.random.Generator, rows: int) -> np.ndarray:
    """Records per entity, drawn from the measured shape."""
    values = np.array([n for n, _ in RECORDS_PER_ENTITY])
    weights = np.array([w for _, w in RECORDS_PER_ENTITY], dtype=np.float64)
    weights /= weights.sum()
    return values[rng.choice(len(values), size=rows, p=weights)]


def write_senzing(
    run: GraphRun,
    directory: str | Path,
    *,
    data_source: str = DATA_SOURCE,
    seed: int | None = None,
) -> SenzingExport:
    """Write ``run``'s companies and people as Senzing records plus the answer.

    ``records.jsonl`` is one JSON object per line: a ``DATA_SOURCE``, a
    ``RECORD_ID`` and a list of ``FEATURES``, with the first record of each
    company carrying ``REL_ANCHOR_KEY`` and the rest pointing at it through
    ``REL_POINTER_KEY`` and a role. Person records point at the company they
    are attached to, with ``Contact`` or ``Executive``. That is the format
    :class:`graphfaker.fetchers.senzing.SenzingFetcher` reads, so an export
    round-trips.

    ``entities.parquet`` is ``record_id``, ``entity_id`` and ``node_type``:
    the correct clustering, which is the thing a register cannot give you.
    Nothing in ``records.jsonl`` names it.

    ``seed`` decides how many records each entity gets and which spelling
    each one uses; it defaults to the run's own seed, so an export is as
    reproducible as the dataset it came from.
    """
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    records_path = root / "records.jsonl"
    entities_path = root / "entities.parquet"

    rng = np.random.default_rng(run.manifest.seed if seed is None else seed)
    gold: list[dict[str, Any]] = []
    written = varied = multi = entities = moved = 0
    person_records = people_written = 0
    #: Company id to the anchor key a person record points at.
    anchors: dict[str, int] = {}

    # Every address in the dataset, so a branch record can sit at one of the
    # others. Drawing from this pool rather than inventing an address keeps
    # the measured sharing curve: the busiest address stays busiest.
    pool = [
        _address(row)
        for label in COMPANY_TYPES
        if (frame := run.tables.nodes.get(label)) is not None and "name" in frame.columns
        for row in frame.to_dicts()
    ]
    pool = [address for address in pool if address]

    with records_path.open("w", encoding="utf-8", newline="\n") as handle:
        for label in COMPANY_TYPES:
            frame = run.tables.nodes.get(label)
            if frame is None or "name" not in frame.columns:
                continue
            rows = frame.to_dicts()
            counts = _counts(rng, len(rows))
            for row, count in zip(rows, counts.tolist(), strict=True):
                entity_id = str(row["id"])
                entities += 1
                if count > 1:
                    multi += 1
                spellings = [str(row["name"])]
                if count > 1:
                    spellings += variants(str(row["name"]), rng, count - 1)
                while len(spellings) < count:
                    # A company can be filed twice under the same spelling,
                    # which is the easy case and part of the mix.
                    spellings.append(spellings[0])

                anchor_key = written + 1
                anchors[entity_id] = anchor_key
                home = _address(row)
                for n, spelling in enumerate(spellings):
                    record_id = f"{entity_id}-{n}" if n else entity_id
                    address = home
                    if n and pool and rng.random() > SAME_ADDRESS_RATE:
                        # A branch is somewhere else, which is the reason a
                        # register has a record for it.
                        address = pool[int(rng.integers(0, len(pool)))]
                        if address != home:
                            moved += 1
                    features = _features(row, spelling, label, address)
                    if n == 0:
                        features.append(
                            {"REL_ANCHOR_DOMAIN": DOMAIN, "REL_ANCHOR_KEY": anchor_key}
                        )
                    else:
                        features.append({
                            "REL_POINTER_DOMAIN": DOMAIN,
                            "REL_POINTER_KEY": anchor_key,
                            "REL_POINTER_ROLE": EXTRA_ROLES[(n - 1) % len(EXTRA_ROLES)],
                        })
                        if spelling != spellings[0]:
                            varied += 1
                    handle.write(json.dumps({
                        "DATA_SOURCE": data_source,
                        "RECORD_ID": record_id,
                        "FEATURES": features,
                    }) + "\n")
                    gold.append(
                        {"record_id": record_id, "entity_id": entity_id, "node_type": label}
                    )
                    written += 1

        person_records, people_written, people_multi = _write_people(
            run, handle, rng, anchors, data_source, gold
        )
        written += person_records
        multi += people_multi

    pl.DataFrame(gold).write_parquet(entities_path, use_pyarrow=True)
    export = SenzingExport(
        records=records_path,
        entities=entities_path,
        record_count=written,
        entity_count=entities + people_written,
        multi_record_entities=multi,
        varied_names=varied,
        moved_records=moved,
        person_records=person_records,
        people=people_written,
    )
    logger.info("%s", export.summary())
    return export


def _write_people(
    run: GraphRun,
    handle: Any,
    rng: np.random.Generator,
    anchors: dict[str, int],
    data_source: str,
    gold: list[dict[str, Any]],
) -> tuple[int, int, int]:
    """One record per person-company link, which is how a register holds them.

    A register has no person entity. It has a row saying "this person is a
    contact at that company", so somebody attached to three companies is
    three rows, written differently in each, and joining them up is the
    resolution problem. The name on the records after the first is a
    convention away from the first: the surname leading, an initial for the
    first name, a middle initial where there is a middle name.

    Returns the records written, the people they belong to, and how many of
    those people have more than one record.
    """
    people = run.tables.nodes.get(PERSON_TYPE)
    if people is None:
        return 0, 0, 0

    links: list[tuple[str, str, str]] = []
    for relationship, role in PERSON_ROLES.items():
        frame = run.tables.edges.get(relationship)
        if frame is None:
            continue
        links += [
            (str(source), str(target), role)
            for source, target in zip(frame[SOURCE], frame[TARGET], strict=True)
        ]
    if not links:
        return 0, 0, 0

    rows = {str(row["id"]): row for row in people.to_dicts()}
    by_person: dict[str, list[tuple[str, str]]] = {}
    for person, company, role in links:
        by_person.setdefault(person, []).append((company, role))

    written = multi = 0
    for person_id, attachments in by_person.items():
        row = rows.get(person_id)
        if row is None:
            continue
        if len(attachments) > 1:
            multi += 1
        name = str(row.get("name") or "")
        spellings = [name]
        if len(attachments) > 1:
            spellings += person_variants(row, rng, len(attachments) - 1)
        while len(spellings) < len(attachments):
            spellings.append(name)

        for n, (company, role) in enumerate(attachments):
            anchor = anchors.get(company)
            if anchor is None:
                continue
            features: list[dict[str, Any]] = [{"NAME_FULL": spellings[n]}]
            if row.get("first") and row.get("last"):
                parts = {"NAME_FIRST": row["first"], "NAME_LAST": row["last"]}
                if row.get("middle"):
                    parts["NAME_MIDDLE"] = row["middle"]
                features.append(parts)
            features.append({"RECORD_TYPE": "PERSON"})
            address = _address(row)
            if address and rng.random() < PERSON_ADDRESS_SHARE:
                features.append({**address, "ADDR_TYPE": "HOME"})
            features.append({
                "REL_POINTER_DOMAIN": DOMAIN,
                "REL_POINTER_KEY": anchor,
                "REL_POINTER_ROLE": role,
            })
            record_id = f"{person_id}-{n}" if n else person_id
            handle.write(json.dumps({
                "DATA_SOURCE": data_source,
                "RECORD_ID": record_id,
                "FEATURES": features,
            }) + "\n")
            gold.append(
                {"record_id": record_id, "entity_id": person_id, "node_type": PERSON_TYPE}
            )
            written += 1
    return written, len(by_person), multi


def read_gold(directory: str | Path) -> list[list[str]]:
    """The answer key as clusters, the shape ``evaluate_clusters`` wants.

    Single-record entities are left out: a resolver is not credited for
    failing to merge something that only appears once, and
    ``evaluate_clusters`` treats anything it is not told about as a
    singleton.
    """
    frame = pl.read_parquet(Path(directory) / "entities.parquet")
    grouped = frame.group_by("entity_id").agg(pl.col("record_id"))
    return [
        sorted(records)
        for records in grouped["record_id"].to_list()
        if len(records) > 1
    ]
