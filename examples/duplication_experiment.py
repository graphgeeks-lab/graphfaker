"""Measure entity duplication across graph-building frameworks.

The experiment
--------------
Build one corpus whose entities are known in advance, hand the *same* text to
several graph builders, and count how many nodes each one creates for entities
that a human reader would never split.

Nothing is corrupted. The text is clean, well-formed English; every entity is
unambiguous; and the corpus is audited so that no surface form could be claimed
by two entities. A correct pipeline therefore scores zero.

What this measures, and what it does not
----------------------------------------
It measures node duplication on clean input. It does *not* measure answer
quality, retrieval quality, or how a pipeline behaves on messy real documents.
Those are different claims and this script does not support them.

Running it
----------
The adapters are stubs on purpose: each framework needs its own install and
usually an API key, and hard-wiring them here would rot within weeks. Fill in
the ones you have, run, and the table prints and writes to results.json.

    python examples/duplication_experiment.py --entities 60 --documents 80

Adding an adapter means writing one function: text in, NetworkX graph out, with
each node carrying a `name` attribute.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable

import networkx as nx

from graphfaker.corpus import (
    Corpus,
    DuplicationReport,
    duplication_report,
    generate_corpus,
)

# --------------------------------------------------------------------------- #
# adapters: text -> graph
# --------------------------------------------------------------------------- #
#
# Each returns a NetworkX graph whose nodes carry a `name` attribute, or None if
# the framework is not installed. Returning None is recorded as "not run" rather
# than as a zero, because a missing framework is not a result.


def build_with_reference(corpus: Corpus) -> nx.DiGraph:
    """A perfect extractor, as a control.

    Include this in every run. If the control does not score zero, the harness
    or the corpus is at fault and the other rows cannot be trusted.
    """
    G = nx.DiGraph()
    for entity in corpus.entities:
        G.add_node(entity.id, name=entity.name, type=entity.type)
    for relation in corpus.relations:
        G.add_edge(relation.source, relation.target, relationship=relation.type)
    return G


def build_with_langchain(corpus: Corpus) -> nx.DiGraph | None:
    """LangChain `LLMGraphTransformer`."""
    try:
        from langchain_core.documents import Document as LCDocument
        from langchain_experimental.graph_transformers import LLMGraphTransformer
        from langchain_openai import ChatOpenAI
    except ImportError:
        return None

    transformer = LLMGraphTransformer(llm=ChatOpenAI(temperature=0))
    documents = [LCDocument(page_content=d.text) for d in corpus.documents]
    graph_documents = transformer.convert_to_graph_documents(documents)

    G = nx.DiGraph()
    for graph_document in graph_documents:
        for node in graph_document.nodes:
            G.add_node(node.id, name=str(node.id), type=node.type)
        for edge in graph_document.relationships:
            G.add_edge(edge.source.id, edge.target.id, relationship=edge.type)
    return G


def build_with_llamaindex(corpus: Corpus) -> nx.DiGraph | None:
    """LlamaIndex `PropertyGraphIndex`."""
    try:
        from llama_index.core import Document as LIDocument
        from llama_index.core import PropertyGraphIndex
    except ImportError:
        return None

    index = PropertyGraphIndex.from_documents(
        [LIDocument(text=d.text) for d in corpus.documents]
    )
    store = index.property_graph_store
    G = nx.DiGraph()
    for node in store.get():
        name = getattr(node, "name", None) or getattr(node, "id", None)
        G.add_node(str(name), name=str(name))
    for triplet in store.get_triplets():
        source, relation, target = triplet
        G.add_edge(
            str(getattr(source, "name", source)),
            str(getattr(target, "name", target)),
            relationship=str(getattr(relation, "label", relation)),
        )
    return G


def build_with_graphrag(corpus: Corpus) -> nx.DiGraph | None:
    """Microsoft GraphRAG.

    GraphRAG runs as a CLI over an input directory and writes parquet outputs,
    so wire this to a completed run rather than calling it in-process. Point
    GRAPHRAG_OUTPUT at the artifacts directory.
    """
    output = os.environ.get("GRAPHRAG_OUTPUT")
    if not output:
        return None
    try:
        import pandas as pd
    except ImportError:
        return None

    entities = pd.read_parquet(os.path.join(output, "entities.parquet"))
    G = nx.DiGraph()
    for _, row in entities.iterrows():
        G.add_node(str(row.get("title") or row.get("id")), name=str(row.get("title")))
    relationships_path = os.path.join(output, "relationships.parquet")
    if os.path.exists(relationships_path):
        for _, row in pd.read_parquet(relationships_path).iterrows():
            G.add_edge(str(row["source"]), str(row["target"]), relationship="RELATED")
    return G


def build_with_lightrag(corpus: Corpus) -> nx.DiGraph | None:
    """LightRAG, which persists its graph as GraphML."""
    path = os.environ.get("LIGHTRAG_GRAPHML")
    if not path or not os.path.exists(path):
        return None
    loaded = nx.read_graphml(path)
    for node, data in loaded.nodes(data=True):
        data.setdefault("name", node)
    return loaded


def build_with_cognee(corpus: Corpus) -> nx.DiGraph | None:
    """Cognee. Async, so this runs its own event loop."""
    try:
        import asyncio

        import cognee
    except ImportError:
        return None

    async def run() -> nx.DiGraph:
        for document in corpus.documents:
            await cognee.add(document.text)
        await cognee.cognify()
        graph = await cognee.get_graph_data()
        G = nx.DiGraph()
        nodes, edges = graph if isinstance(graph, tuple) else (graph, [])
        for node in nodes:
            identifier = str(node[0] if isinstance(node, tuple) else node)
            payload = node[1] if isinstance(node, tuple) and len(node) > 1 else {}
            G.add_node(identifier, name=str(payload.get("name", identifier)))
        for edge in edges:
            if len(edge) >= 2:
                G.add_edge(str(edge[0]), str(edge[1]), relationship=str(edge[2]) if len(edge) > 2 else "RELATED")
        return G

    return asyncio.run(run())


ADAPTERS: dict[str, Callable[[Corpus], nx.DiGraph | None]] = {
    "reference (control)": build_with_reference,
    "langchain-LLMGraphTransformer": build_with_langchain,
    "llamaindex-PropertyGraphIndex": build_with_llamaindex,
    "microsoft-graphrag": build_with_graphrag,
    "lightrag": build_with_lightrag,
    "cognee": build_with_cognee,
}


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


def render_table(reports: list[DuplicationReport]) -> str:
    """Markdown table, ready to paste into a write-up."""
    headers = [
        ("framework", "Framework"),
        ("expected_entities", "Entities"),
        ("nodes_in_graph", "Nodes"),
        ("entities_found", "Found"),
        ("entities_missed", "Missed"),
        ("entities_duplicated", "Split"),
        ("duplication_rate", "Dup. rate"),
        ("node_inflation", "Inflation"),
        ("unmatched_nodes", "Unmatched"),
    ]
    lines = [
        "| " + " | ".join(title for _, title in headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for report in reports:
        row = report.row()
        cells = []
        for key, _ in headers:
            value = row[key]
            if key == "duplication_rate":
                value = f"{value:.1%}"
            elif key == "node_inflation":
                value = f"{value:.2f}x"
            cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--entities", type=int, default=60)
    parser.add_argument("--documents", type=int, default=80)
    parser.add_argument("--corpus-dir", default="corpus")
    parser.add_argument("--results", default="results.json")
    arguments = parser.parse_args()

    corpus = generate_corpus(
        seed=arguments.seed,
        n_entities=arguments.entities,
        n_documents=arguments.documents,
    )

    audit = corpus.audit()
    print(f"Corpus: {audit['entities']} entities, {audit['documents']} documents, "
          f"{audit['surface_forms']} surface forms")
    if not audit["clean"]:
        # Publishing a measurement from an ambiguous corpus would attribute the
        # corpus's own defects to the frameworks.
        print("\nREFUSING TO RUN: the corpus is ambiguous.")
        print(f"  ambiguous forms : {audit['ambiguous_forms']}")
        print(f"  containment     : {audit['containment_pairs'][:10]}")
        print("Try a different --seed or fewer --entities.")
        raise SystemExit(1)
    print("Audit clean: no surface form is claimable by two entities.\n")

    corpus.write(arguments.corpus_dir)
    print(f"Corpus written to {arguments.corpus_dir}/\n")

    reports: list[DuplicationReport] = []
    skipped: list[str] = []
    for name, adapter in ADAPTERS.items():
        try:
            graph = adapter(corpus)
        except Exception as error:  # noqa: BLE001 - one bad adapter must not end the run
            print(f"  {name}: FAILED ({type(error).__name__}: {error})")
            skipped.append(f"{name} (error: {type(error).__name__})")
            continue
        if graph is None:
            print(f"  {name}: not installed / not configured, skipping")
            skipped.append(f"{name} (not run)")
            continue
        report = duplication_report(graph, corpus, framework=name)
        reports.append(report)
        print(f"  {name}: {report.duplication_rate:.1%} duplication, "
              f"{report.node_inflation:.2f}x nodes")

    print("\n" + render_table(reports))

    if skipped:
        # Stated explicitly, because a table that silently omits frameworks
        # reads as though they were tested and did well.
        print(f"\nNot included in the table: {', '.join(skipped)}")

    with open(arguments.results, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "seed": arguments.seed,
                "corpus": {k: v for k, v in audit.items() if k != "containment_pairs"},
                "results": [report.row() for report in reports],
                "per_entity": {r.framework: r.per_entity for r in reports},
                "skipped": skipped,
            },
            handle,
            indent=2,
        )
    print(f"\nResults written to {arguments.results}")


if __name__ == "__main__":
    main()
