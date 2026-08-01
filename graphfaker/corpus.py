"""Paired text/entity corpora for measuring entity duplication.

Purpose
-------
LLM-based graph builders frequently emit several nodes for one real-world
entity. Measuring how often requires a corpus where the correct answer is known
in advance: a set of documents plus a registry saying exactly which entities
they describe.

What this deliberately does *not* do
------------------------------------
It does not corrupt anything. No typos, no injected errors, no simulated OCR
noise. The documents are clean, well-formed English in which every entity is
unambiguous to a human reader. Entities are referred to by the surface forms a
normal writer would use — full name, surname alone, an accepted abbreviation —
because a document that says "Lovelace" once and "Ada Lovelace" twice is
ordinary prose, not a degraded signal.

This matters for the validity of any result. Synthetic *corruption* is known to
be far easier than real-world error (Lam et al., IJPDS 2024, measured roughly a
hundredfold gap), so a benchmark built on guessed error rates measures its own
noise model. Counting how many nodes a pipeline creates for an entity that a
human would never split is a different and much weaker claim — and one the
generator can actually support.

Names are checked for mutual distinctness, so a pipeline is never penalised for
conflating two entities the corpus itself made confusable.

Example:
    >>> corpus = generate_corpus(seed=42, n_entities=30, n_documents=40)
    >>> corpus.write("corpus/")           # NNN.txt files plus gold.json
    >>> # ...run a graph builder over corpus/, then:
    >>> report = duplication_report(extracted_graph, corpus)
    >>> print(report.summary())
"""

from __future__ import annotations

import json
import os
import random
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
from faker import Faker

from graphfaker.logger import logger
from graphfaker.resolve import _string_similarity, normalize

__all__ = [
    "AttributionResult",
    "Corpus",
    "Document",
    "DuplicationReport",
    "Entity",
    "Relation",
    "attribute_nodes",
    "duplication_report",
    "generate_corpus",
]

#: Two entity names closer than this are considered confusable, and the
#: generator will draw a different name rather than emit them together.
DISTINCTNESS_CEILING = 0.62

ENTITY_TYPES = ("Person", "Organization", "Place", "Product")


# --------------------------------------------------------------------------- #
# data model
# --------------------------------------------------------------------------- #


@dataclass
class Entity:
    """One real-world thing the corpus talks about."""

    id: str
    name: str
    type: str
    aliases: list[str] = field(default_factory=list)

    def surface_forms(self) -> list[str]:
        """Every string the documents may use for this entity."""
        return [self.name] + list(self.aliases)


@dataclass
class Relation:
    """A fact stated somewhere in the corpus."""

    source: str
    target: str
    type: str


@dataclass
class Document:
    """A single document, and the entities it mentions."""

    id: str
    text: str
    entity_ids: list[str] = field(default_factory=list)


@dataclass
class Corpus:
    """Documents plus the answer key describing them."""

    entities: list[Entity]
    relations: list[Relation]
    documents: list[Document]
    seed: int | None = None

    @property
    def expected_entity_count(self) -> int:
        """How many distinct entities a correct extraction would produce."""
        return len(self.entities)

    def alias_index(self) -> dict[str, str]:
        """Normalised surface form -> entity id.

        Forms shared by more than one entity are dropped: an ambiguous string
        cannot be used to judge a pipeline.
        """
        owners: dict[str, set[str]] = {}
        for entity in self.entities:
            for form in entity.surface_forms():
                owners.setdefault(normalize(form), set()).add(entity.id)
        return {
            form: next(iter(ids)) for form, ids in owners.items() if len(ids) == 1
        }

    def text(self) -> str:
        """The whole corpus as one string, for pipelines that want a blob."""
        return "\n\n".join(document.text for document in self.documents)

    def audit(self) -> dict[str, Any]:
        """Report any surface form more than one entity could claim.

        Run this before publishing a measurement. If `ambiguous_forms` is
        non-empty, some part of the observed "duplication" is the corpus's
        fault rather than the pipeline's, and the result needs a caveat or a
        different seed.
        """
        owners: dict[str, set[str]] = {}
        for entity in self.entities:
            for form in entity.surface_forms():
                owners.setdefault(normalize(form), set()).add(entity.id)
        ambiguous = {
            form: sorted(ids) for form, ids in owners.items() if len(ids) > 1
        }

        # A form contained inside another entity's form is also ambiguous in
        # running prose, even though the exact strings differ.
        containment: list[tuple[str, str]] = []
        forms = sorted(
            ((normalize(f), e.id) for e in self.entities for f in e.surface_forms()),
            key=lambda pair: pair[0],
        )
        for form, owner in forms:
            for other_form, other_owner in forms:
                if owner == other_owner or not form or not other_form:
                    continue
                if form != other_form and form in other_form:
                    containment.append((form, other_form))

        return {
            "entities": len(self.entities),
            "documents": len(self.documents),
            "surface_forms": len(owners),
            "ambiguous_forms": ambiguous,
            "containment_pairs": sorted(set(containment)),
            "clean": not ambiguous and not containment,
        }

    def gold(self) -> dict[str, Any]:
        """The answer key as a plain dict."""
        return {
            "seed": self.seed,
            "expected_entity_count": self.expected_entity_count,
            "entities": [
                {
                    "id": entity.id,
                    "name": entity.name,
                    "type": entity.type,
                    "aliases": entity.aliases,
                }
                for entity in self.entities
            ],
            "relations": [
                {"source": r.source, "target": r.target, "type": r.type}
                for r in self.relations
            ],
            "documents": [
                {"id": document.id, "entity_ids": document.entity_ids}
                for document in self.documents
            ],
        }

    def write(self, directory: str, encoding: str = "utf-8") -> str:
        """Write one .txt per document plus gold.json, and return the directory."""
        target = os.path.abspath(directory)
        os.makedirs(target, exist_ok=True)
        for document in self.documents:
            path = os.path.join(target, f"{document.id}.txt")
            with open(path, "w", encoding=encoding) as handle:
                handle.write(document.text)
        with open(os.path.join(target, "gold.json"), "w", encoding=encoding) as handle:
            json.dump(self.gold(), handle, indent=2, ensure_ascii=False)
        logger.info(
            "corpus: wrote %d documents and gold.json to %s",
            len(self.documents),
            target,
        )
        return target


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #

_PERSON_TEMPLATES = [
    "{person} joined {org} in {year} as a {role}.",
    "{person} has worked at {org} since {year}.",
    "Before {year}, {person} led the {role} team at {org}.",
    "{person} was promoted to {role} at {org} last spring.",
]

_PLACE_TEMPLATES = [
    "{org} operates its main site in {place}.",
    "{org} opened a second office in {place} in {year}.",
    "The {place} branch of {org} employs several dozen people.",
]

_PRODUCT_TEMPLATES = [
    "{org} manufactures the {product}.",
    "The {product} is {org}'s best-selling line.",
    "{org} discontinued the {product} in {year}.",
]

_RESIDENCE_TEMPLATES = [
    "{person} lives in {place}.",
    "{person} relocated to {place} in {year}.",
    "{person} grew up near {place}.",
]

_REVIEW_TEMPLATES = [
    "{person} reviewed the {product} favourably.",
    "According to {person}, the {product} is reliable.",
    "{person} has used the {product} for years.",
]

_ROLES = [
    "logistics",
    "research",
    "quality assurance",
    "field operations",
    "procurement",
    "customer support",
]


def _distinct_enough(candidate: str, chosen: Iterable[str]) -> bool:
    """Reject a name too similar to one already in use.

    Without this the corpus can defeat itself. Naming four people
    "Person 0" through "Person 3" produces strings that are genuinely ~93%
    similar, so any name-based matcher will merge them — and the resulting
    "duplication" would be the corpus's fault, not the pipeline's.
    """
    normalized = normalize(candidate)
    if not normalized:
        return False
    for existing in chosen:
        if _string_similarity(normalized, normalize(existing)) > DISTINCTNESS_CEILING:
            return False
    return True


def _draw_distinct(
    make: Any, chosen: list[str], attempts: int = 200
) -> str | None:
    """Draw from `make` until the result is distinct from everything chosen."""
    for _ in range(attempts):
        candidate = make()
        if _distinct_enough(candidate, chosen):
            return candidate
    return None


def _other_aliases(entities: Sequence[Entity], exclude_id: str) -> list[str]:
    """Aliases already granted to entities other than this one."""
    return [
        alias
        for entity in entities
        if entity.id != exclude_id
        for alias in entity.aliases
    ]


def _alias_is_safe(alias: str, others: Sequence[str]) -> bool:
    """Reject an alias that any other entity could also plausibly claim.

    Similarity alone is not enough here. Faker derives place names from
    surnames, so a corpus can end up with a person aliased "Henderson" beside an
    organization named "Henderson, Ramirez and Lewis" — two entities with a
    genuine claim on the same string. A document using it would be ambiguous to
    a human too, which means it cannot be used to judge a pipeline.

    An alias is dropped when, against any other entity's surface form, it is
    too similar, is a substring, or its tokens are a subset.
    """
    normalized = normalize(alias)
    if not normalized:
        return False
    tokens = set(normalized.split())
    for other in others:
        other_normalized = normalize(other)
        if not other_normalized:
            continue
        if _string_similarity(normalized, other_normalized) > DISTINCTNESS_CEILING:
            return False
        if normalized in other_normalized or other_normalized in normalized:
            return False
        if tokens and tokens <= set(other_normalized.split()):
            return False
    return True


def _person_aliases(name: str) -> list[str]:
    """Surface forms a writer would naturally use for a person."""
    parts = name.split()
    if len(parts) < 2:
        return []
    first, last = parts[0], parts[-1]
    return [last, f"{first[0]}. {last}", first]


def _org_aliases(name: str) -> list[str]:
    """Drop legal suffixes and abbreviate, as ordinary prose does."""
    aliases: list[str] = []
    trimmed = name
    for suffix in (" Inc", " Inc.", " LLC", " Ltd", " Ltd.", " and Sons", " Group"):
        if trimmed.endswith(suffix):
            trimmed = trimmed[: -len(suffix)].rstrip(",")
            break
    if trimmed != name:
        aliases.append(trimmed)
    # Strip trailing punctuation: the first word of "Barnes, Cole and Ramirez"
    # is "Barnes," and an alias with a dangling comma appears verbatim in the
    # generated prose.
    head = trimmed.split()[0].strip(",.;:")
    if len(head) > 3 and head.lower() not in {"the"}:
        aliases.append(head)
    words = [word for word in trimmed.split() if word[:1].isupper()]
    if len(words) >= 2:
        aliases.append("".join(word[0] for word in words).upper())
    return aliases


def generate_corpus(
    seed: int | None = None,
    n_entities: int = 30,
    n_documents: int = 40,
    sentences_per_document: int = 4,
    alias_probability: float = 0.45,
) -> Corpus:
    """Build a corpus of documents whose entities are known in advance.

    Args:
        seed: Makes the corpus reproducible. Strongly recommended — a
            measurement made against an unreproducible corpus cannot be checked
            by anyone else.
        n_entities: Approximate number of distinct entities. Fewer may be
            produced if the distinctness check cannot find enough clearly
            different names.
        n_documents: How many documents to write.
        sentences_per_document: Sentences per document.
        alias_probability: Chance that a mention after the first uses an alias
            rather than the canonical name. This is normal prose variation, not
            corruption.

    Returns:
        A `Corpus`. Every entity is mentioned at least once.
    """
    rand = random.Random(seed)
    faker = Faker()
    if seed is not None:
        faker.seed_instance(seed)

    per_type = max(1, n_entities // len(ENTITY_TYPES))
    entities: list[Entity] = []
    used_names: list[str] = []

    makers = {
        "Person": faker.name,
        "Organization": faker.company,
        "Place": faker.city,
        "Product": lambda: f"{faker.word().capitalize()} {rand.randint(100, 900)}",
    }
    aliasers = {
        "Person": _person_aliases,
        "Organization": _org_aliases,
        "Place": lambda name: [],
        "Product": lambda name: [f"the {name.split()[0]}"],
    }

    # First pass: draw every canonical name, each distinct from all the others.
    for entity_type in ENTITY_TYPES:
        for index in range(per_type):
            name = _draw_distinct(makers[entity_type], used_names)
            if name is None:
                logger.warning(
                    "corpus: could only place %d distinct %s names",
                    index,
                    entity_type,
                )
                break
            used_names.append(name)
            entities.append(
                Entity(id=f"{entity_type.lower()}_{index}", name=name, type=entity_type)
            )

    # Second pass: assign aliases only now that every name is known. Doing this
    # inside the first loop would vet each alias against a partial world and let
    # a later entity collide with an alias already handed out.
    dropped = 0
    for entity in entities:
        others = [other.name for other in entities if other.id != entity.id]
        # Trimming a legal suffix and taking the first word can both yield the
        # same string ("Blake and Sons" -> "Blake" twice), so track what has
        # already been granted, including the canonical name itself.
        granted = {normalize(entity.name)}
        for alias in aliasers[entity.type](entity.name):
            key = normalize(alias)
            if key in granted:
                continue
            if _alias_is_safe(alias, others + _other_aliases(entities, entity.id)):
                entity.aliases.append(alias)
                granted.add(key)
            else:
                dropped += 1
    if dropped:
        logger.info(
            "corpus: dropped %d ambiguous alias(es) that another entity could claim",
            dropped,
        )

    by_type: dict[str, list[Entity]] = {t: [] for t in ENTITY_TYPES}
    for entity in entities:
        by_type[entity.type].append(entity)

    def mention(entity: Entity, first: bool) -> str:
        if first or not entity.aliases or rand.random() > alias_probability:
            return entity.name
        return rand.choice(entity.aliases)

    relations: list[Relation] = []
    documents: list[Document] = []
    mentioned_once: set[str] = set()

    def build_sentence(seen: set[str]) -> tuple[str, list[str]]:
        """Emit one factual sentence, returning it and the entities used."""
        options: list[str] = []
        if by_type["Person"] and by_type["Organization"]:
            options += ["employment"]
        if by_type["Organization"] and by_type["Place"]:
            options += ["location"]
        if by_type["Organization"] and by_type["Product"]:
            options += ["product"]
        if by_type["Person"] and by_type["Place"]:
            options += ["residence"]
        if by_type["Person"] and by_type["Product"]:
            options += ["review"]
        if not options:
            return "", []

        kind = rand.choice(options)
        year = rand.randint(2015, 2025)

        if kind == "employment":
            person = rand.choice(by_type["Person"])
            org = rand.choice(by_type["Organization"])
            relations.append(Relation(person.id, org.id, "WORKS_AT"))
            text = rand.choice(_PERSON_TEMPLATES).format(
                person=mention(person, person.id not in seen),
                org=mention(org, org.id not in seen),
                year=year,
                role=rand.choice(_ROLES),
            )
            return text, [person.id, org.id]

        if kind == "location":
            org = rand.choice(by_type["Organization"])
            place = rand.choice(by_type["Place"])
            relations.append(Relation(org.id, place.id, "LOCATED_IN"))
            text = rand.choice(_PLACE_TEMPLATES).format(
                org=mention(org, org.id not in seen),
                place=mention(place, place.id not in seen),
                year=year,
            )
            return text, [org.id, place.id]

        if kind == "product":
            org = rand.choice(by_type["Organization"])
            product = rand.choice(by_type["Product"])
            relations.append(Relation(org.id, product.id, "MANUFACTURES"))
            text = rand.choice(_PRODUCT_TEMPLATES).format(
                org=mention(org, org.id not in seen),
                product=mention(product, product.id not in seen),
                year=year,
            )
            return text, [org.id, product.id]

        if kind == "residence":
            person = rand.choice(by_type["Person"])
            place = rand.choice(by_type["Place"])
            relations.append(Relation(person.id, place.id, "LIVES_IN"))
            text = rand.choice(_RESIDENCE_TEMPLATES).format(
                person=mention(person, person.id not in seen),
                place=mention(place, place.id not in seen),
                year=year,
            )
            return text, [person.id, place.id]

        person = rand.choice(by_type["Person"])
        product = rand.choice(by_type["Product"])
        relations.append(Relation(person.id, product.id, "REVIEWED"))
        text = rand.choice(_REVIEW_TEMPLATES).format(
            person=mention(person, person.id not in seen),
            product=mention(product, product.id not in seen),
        )
        return text, [person.id, product.id]

    for index in range(n_documents):
        seen: set[str] = set()
        sentences: list[str] = []
        used: list[str] = []
        for _ in range(sentences_per_document):
            sentence, ids = build_sentence(seen)
            if not sentence:
                continue
            sentences.append(sentence)
            for entity_id in ids:
                seen.add(entity_id)
                mentioned_once.add(entity_id)
                if entity_id not in used:
                    used.append(entity_id)
        documents.append(
            Document(id=f"doc_{index:03d}", text=" ".join(sentences), entity_ids=used)
        )

    # Any entity the random walk never reached gets its own document, so the
    # answer key never contains an entity the text does not mention.
    unmentioned = [e for e in entities if e.id not in mentioned_once]
    for offset, entity in enumerate(unmentioned):
        documents.append(
            Document(
                id=f"doc_{n_documents + offset:03d}",
                text=f"{entity.name} is a {entity.type.lower()} of record.",
                entity_ids=[entity.id],
            )
        )

    logger.info(
        "generate_corpus: %d entities, %d relations, %d documents (seed=%s)",
        len(entities),
        len(relations),
        len(documents),
        seed,
    )
    return Corpus(
        entities=entities, relations=relations, documents=documents, seed=seed
    )


# --------------------------------------------------------------------------- #
# measurement
# --------------------------------------------------------------------------- #


@dataclass
class AttributionResult:
    """Which extracted nodes correspond to which known entities."""

    matched: dict[str, list[Hashable]]
    unmatched: list[Hashable]

    @property
    def matched_node_count(self) -> int:
        return sum(len(nodes) for nodes in self.matched.values())


def attribute_nodes(
    G: nx.Graph,
    corpus: Corpus,
    name_attr: str = "name",
    fallback_to_id: bool = True,
) -> AttributionResult:
    """Assign each node in an extracted graph to a known entity, if it matches.

    Matching is exact on a normalised surface form first, then falls back to
    checking whether a canonical name appears inside a longer node label — LLM
    extractors often emit "Ada Lovelace (engineer)".

    Nodes that match nothing are reported in `unmatched` rather than being
    forced onto the nearest entity. Attribution is itself a matching problem,
    and quietly guessing would contaminate the very number being measured.
    """
    index = corpus.alias_index()
    canonical = sorted(
        ((normalize(e.name), e.id) for e in corpus.entities),
        key=lambda pair: -len(pair[0]),
    )

    matched: dict[str, list[Hashable]] = {}
    unmatched: list[Hashable] = []

    for node, data in G.nodes(data=True):
        label = data.get(name_attr)
        if label in (None, "") and fallback_to_id:
            label = node
        key = normalize(label)
        entity_id = index.get(key)
        if entity_id is None:
            # Longest canonical name contained in the label wins, so
            # "Northwind Logistics" is preferred over "Northwind".
            for name, candidate in canonical:
                if name and name in key:
                    entity_id = candidate
                    break
        if entity_id is None:
            unmatched.append(node)
        else:
            matched.setdefault(entity_id, []).append(node)

    return AttributionResult(matched=matched, unmatched=unmatched)


@dataclass
class DuplicationReport:
    """How badly a pipeline split the entities it was given."""

    framework: str
    expected_entities: int
    nodes_in_graph: int
    per_entity: dict[str, int]
    missed: list[str]
    unmatched_nodes: int

    @property
    def found(self) -> int:
        return sum(1 for count in self.per_entity.values() if count >= 1)

    @property
    def duplicated(self) -> int:
        return sum(1 for count in self.per_entity.values() if count > 1)

    @property
    def duplication_rate(self) -> float:
        """Share of the entities that were found which got split."""
        return self.duplicated / self.found if self.found else 0.0

    @property
    def node_inflation(self) -> float:
        """Nodes produced per entity that actually exists."""
        return (
            self.nodes_in_graph / self.expected_entities
            if self.expected_entities
            else 0.0
        )

    @property
    def worst(self) -> list[tuple[str, int]]:
        return sorted(
            ((k, v) for k, v in self.per_entity.items() if v > 1),
            key=lambda pair: (-pair[1], pair[0]),
        )

    def row(self) -> dict[str, Any]:
        """One row for the comparison table."""
        return {
            "framework": self.framework,
            "expected_entities": self.expected_entities,
            "nodes_in_graph": self.nodes_in_graph,
            "entities_found": self.found,
            "entities_missed": len(self.missed),
            "entities_duplicated": self.duplicated,
            "duplication_rate": round(self.duplication_rate, 4),
            "node_inflation": round(self.node_inflation, 3),
            "unmatched_nodes": self.unmatched_nodes,
        }

    def summary(self) -> str:
        lines = [
            f"{self.framework}",
            f"  entities in corpus     : {self.expected_entities}",
            f"  nodes in graph         : {self.nodes_in_graph}",
            f"  entities found         : {self.found}",
            f"  entities missed        : {len(self.missed)}",
            (
                f"  entities split in two+ : {self.duplicated}"
                f" ({self.duplication_rate:.1%} of those found)"
            ),
            f"  node inflation         : {self.node_inflation:.2f}x",
            f"  nodes matching nothing : {self.unmatched_nodes}",
        ]
        for entity_id, count in self.worst[:10]:
            lines.append(f"    {entity_id}: {count} nodes")
        return "\n".join(lines)


def duplication_report(
    G: nx.Graph,
    corpus: Corpus,
    framework: str = "unknown",
    name_attr: str = "name",
) -> DuplicationReport:
    """Count how many nodes a pipeline created per known entity.

    The headline number is `duplication_rate`: of the entities the pipeline
    found at all, what share did it split across more than one node. Because the
    input text is clean and every entity is unambiguous to a human reader, a
    correct pipeline scores zero.

    `unmatched_nodes` is reported alongside and should be read as a caveat on
    the rest: a pipeline that invents unrelated nodes, or labels them in a way
    the corpus cannot recognise, will show a high count there, and its other
    numbers deserve less trust.
    """
    attribution = attribute_nodes(G, corpus, name_attr=name_attr)
    per_entity = {
        entity.id: len(attribution.matched.get(entity.id, []))
        for entity in corpus.entities
    }
    missed = [entity_id for entity_id, count in per_entity.items() if count == 0]
    return DuplicationReport(
        framework=framework,
        expected_entities=corpus.expected_entity_count,
        nodes_in_graph=G.number_of_nodes(),
        per_entity=per_entity,
        missed=missed,
        unmatched_nodes=len(attribution.unmatched),
    )
