# tests/test_corpus.py
"""Paired text/entity corpora and the duplication measurement."""

import json

import networkx as nx
import pytest

from graphfaker.corpus import (
    Corpus,
    Entity,
    attribute_nodes,
    duplication_report,
    generate_corpus,
)


def _perfect_extraction(corpus: Corpus) -> nx.DiGraph:
    """What a pipeline that never splits an entity would produce."""
    G = nx.DiGraph()
    for entity in corpus.entities:
        G.add_node(entity.id, name=entity.name, type=entity.type)
    return G


def _splits_every_alias(corpus: Corpus) -> nx.DiGraph:
    """A pipeline that emits a separate node per surface form."""
    G = nx.DiGraph()
    for entity in corpus.entities:
        G.add_node(entity.id, name=entity.name, type=entity.type)
        for index, alias in enumerate(entity.aliases):
            G.add_node(f"{entity.id}_a{index}", name=alias, type=entity.type)
    return G


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #


def test_corpus_is_reproducible():
    a = generate_corpus(seed=42, n_entities=16, n_documents=10)
    b = generate_corpus(seed=42, n_entities=16, n_documents=10)
    assert [e.name for e in a.entities] == [e.name for e in b.entities]
    assert [d.text for d in a.documents] == [d.text for d in b.documents]


def test_different_seeds_differ():
    a = generate_corpus(seed=1, n_entities=16, n_documents=10)
    b = generate_corpus(seed=2, n_entities=16, n_documents=10)
    assert [d.text for d in a.documents] != [d.text for d in b.documents]


def test_every_entity_is_mentioned_somewhere():
    corpus = generate_corpus(seed=42, n_entities=24, n_documents=15)
    mentioned = {eid for doc in corpus.documents for eid in doc.entity_ids}
    assert mentioned == {entity.id for entity in corpus.entities}


def test_entity_names_appear_verbatim_in_the_text():
    corpus = generate_corpus(seed=42, n_entities=16, n_documents=20)
    blob = corpus.text()
    for entity in corpus.entities:
        assert any(
            form in blob for form in entity.surface_forms()
        ), f"{entity.id} never appears"


@pytest.mark.parametrize("seed", [1, 2, 7, 42, 99])
def test_generated_corpora_are_unambiguous(seed):
    """No surface form may be claimable by two entities.

    This is the property the whole measurement rests on: if the corpus is
    ambiguous, observed "duplication" is partly the corpus's fault.
    """
    audit = generate_corpus(seed=seed, n_entities=24, n_documents=15).audit()
    assert audit["ambiguous_forms"] == {}
    assert audit["containment_pairs"] == []
    assert audit["clean"] is True


def test_audit_detects_a_deliberately_ambiguous_corpus():
    """The audit must actually be capable of failing."""
    corpus = Corpus(
        entities=[
            Entity(id="e1", name="Acme Corporation", type="Organization", aliases=["Acme"]),
            Entity(id="e2", name="Acme", type="Organization"),
        ],
        relations=[],
        documents=[],
    )
    audit = corpus.audit()
    assert audit["clean"] is False
    assert audit["ambiguous_forms"]


@pytest.mark.parametrize("seed", [1, 2, 7, 42, 99])
def test_aliases_carry_no_dangling_punctuation(seed):
    """The first word of "Barnes, Cole and Ramirez" is "Barnes," with a comma.

    Aliases appear verbatim in the generated prose, so trailing punctuation
    shows up as visible garbage in the corpus.
    """
    corpus = generate_corpus(seed=seed, n_entities=24, n_documents=10)
    for entity in corpus.entities:
        for alias in entity.aliases:
            assert alias == alias.strip(" ,.;:"), (entity.id, alias)
            assert ",," not in alias


def test_aliases_never_duplicate_the_canonical_name():
    corpus = generate_corpus(seed=42, n_entities=24, n_documents=10)
    for entity in corpus.entities:
        assert len(entity.aliases) == len(set(entity.aliases)), entity.id
        assert entity.name not in entity.aliases


def test_alias_index_maps_every_unambiguous_form():
    from graphfaker.resolve import normalize

    corpus = generate_corpus(seed=42, n_entities=16, n_documents=10)
    index = corpus.alias_index()
    for entity in corpus.entities:
        # Every surface form must resolve back to its own entity.
        for form in entity.surface_forms():
            assert index[normalize(form)] == entity.id


def test_write_produces_documents_and_gold(tmp_path):
    corpus = generate_corpus(seed=42, n_entities=16, n_documents=8)
    target = corpus.write(str(tmp_path / "corpus"))

    for document in corpus.documents:
        assert (tmp_path / "corpus" / f"{document.id}.txt").exists()

    gold = json.loads((tmp_path / "corpus" / "gold.json").read_text(encoding="utf-8"))
    assert gold["seed"] == 42
    assert gold["expected_entity_count"] == corpus.expected_entity_count
    assert len(gold["entities"]) == len(corpus.entities)
    assert target.endswith("corpus")


# --------------------------------------------------------------------------- #
# attribution
# --------------------------------------------------------------------------- #


def test_attribution_matches_canonical_names():
    corpus = generate_corpus(seed=42, n_entities=16, n_documents=10)
    result = attribute_nodes(_perfect_extraction(corpus), corpus)
    assert result.unmatched == []
    assert len(result.matched) == len(corpus.entities)


def test_attribution_matches_aliases():
    corpus = generate_corpus(seed=42, n_entities=16, n_documents=10)
    result = attribute_nodes(_splits_every_alias(corpus), corpus)
    assert result.unmatched == []


def test_attribution_handles_decorated_labels():
    """Extractors often emit 'Ada Lovelace (engineer)'."""
    corpus = generate_corpus(seed=42, n_entities=16, n_documents=10)
    entity = corpus.entities[0]
    G = nx.DiGraph()
    G.add_node("n1", name=f"{entity.name} (mentioned in report)")
    result = attribute_nodes(G, corpus)
    assert result.matched.get(entity.id) == ["n1"]


def test_attribution_reports_rather_than_guesses_unknown_nodes():
    corpus = generate_corpus(seed=42, n_entities=16, n_documents=10)
    G = nx.DiGraph()
    G.add_node("junk", name="Completely Unrelated Thing 12345")
    result = attribute_nodes(G, corpus)
    assert result.unmatched == ["junk"]
    assert result.matched == {}


def test_attribution_falls_back_to_the_node_id():
    corpus = generate_corpus(seed=42, n_entities=16, n_documents=10)
    entity = corpus.entities[0]
    G = nx.DiGraph()
    G.add_node(entity.name)  # id is the name, no 'name' attribute
    result = attribute_nodes(G, corpus)
    assert result.matched.get(entity.id) == [entity.name]


# --------------------------------------------------------------------------- #
# the measurement
# --------------------------------------------------------------------------- #


def test_perfect_pipeline_scores_zero_duplication():
    corpus = generate_corpus(seed=42, n_entities=24, n_documents=15)
    report = duplication_report(_perfect_extraction(corpus), corpus, framework="perfect")
    assert report.duplication_rate == 0.0
    assert report.missed == []
    assert report.unmatched_nodes == 0
    assert report.node_inflation == pytest.approx(1.0)


def test_alias_splitting_pipeline_is_penalised():
    corpus = generate_corpus(seed=42, n_entities=24, n_documents=15)
    report = duplication_report(
        _splits_every_alias(corpus), corpus, framework="splitter"
    )
    assert report.duplication_rate > 0.5
    assert report.node_inflation > 1.5
    assert report.worst[0][1] > 1


def test_missing_entities_are_counted():
    corpus = generate_corpus(seed=42, n_entities=24, n_documents=15)
    G = _perfect_extraction(corpus)
    G.remove_node(corpus.entities[0].id)
    report = duplication_report(G, corpus)
    assert corpus.entities[0].id in report.missed
    assert report.found == corpus.expected_entity_count - 1


def test_report_row_is_serialisable():
    corpus = generate_corpus(seed=42, n_entities=16, n_documents=10)
    row = duplication_report(_perfect_extraction(corpus), corpus, framework="x").row()
    assert json.loads(json.dumps(row))["framework"] == "x"


def test_report_summary_is_printable():
    corpus = generate_corpus(seed=42, n_entities=16, n_documents=10)
    text = duplication_report(_splits_every_alias(corpus), corpus, "s").summary()
    assert "duplication" in text.lower() or "split" in text.lower()
    assert "node inflation" in text


def test_empty_graph_reports_everything_missed():
    corpus = generate_corpus(seed=42, n_entities=16, n_documents=10)
    report = duplication_report(nx.DiGraph(), corpus)
    assert report.found == 0
    assert report.duplication_rate == 0.0  # nothing found, so nothing split
    assert len(report.missed) == corpus.expected_entity_count
