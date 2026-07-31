"""Graph-native entity resolution for GraphFaker.

Duplicate entities are the most commonly reported defect in LLM-extracted
knowledge graphs: the same real-world entity is emitted as several nodes, and
every edge attached to a false node is a false edge. Tabular record-linkage
tools compare *rows*, so they cannot use the strongest signal available in a
graph — two candidate nodes that share most of their neighbours are very likely
the same entity, however differently their names are spelled.

This module scores candidate pairs on attribute similarity *and* neighbourhood
overlap, clusters the survivors, and merges each cluster while rewiring its
edges onto a single canonical node.

Typical use::

    from graphfaker import GraphFaker

    gf = GraphFaker(seed=42)
    G = gf.generate_graph(source="faker", total_nodes=200, total_edges=800)

    result = gf.resolve(on=["name", "email"], threshold=0.85)
    print(result.report())

    G_clean = result.apply()

`evaluate_clusters` is provided separately for the case where you already hold
labelled clusters and want to score a prediction against them.

Design notes:
  - Deterministic. Identical input yields identical output; every iteration
    order is sorted.
  - No new dependencies. String similarity uses `difflib` from the standard
    library.
  - Structural similarity can only *raise* a pair's score, never lower it, so
    isolated nodes are never penalised for having few neighbours.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Hashable, Iterable, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import (
    Any,
)

import networkx as nx

from graphfaker.logger import logger

__all__ = [
    "ResolutionResult",
    "attribute_similarity",
    "evaluate_clusters",
    "merge_clusters",
    "neighbor_overlap",
    "normalize",
    "resolve_entities",
]

_NON_ALNUM = re.compile(r"[^0-9a-z]+")

#: Attribute keys written onto merged nodes.
MERGED_FROM = "_merged_from"
MERGED_VARIANTS = "_merged_variants"
MERGE_COUNT = "_merge_count"

#: Guard against accidentally running an O(n^2) comparison on a large graph.
MAX_UNBLOCKED_NODES = 2_000


# --------------------------------------------------------------------------- #
# similarity primitives
# --------------------------------------------------------------------------- #


def normalize(value: Any) -> str:
    """Lowercase, strip punctuation, and collapse whitespace.

    Returns an empty string for None so missing attributes compare as absent
    rather than as the literal string "none".
    """
    if value is None:
        return ""
    text = _NON_ALNUM.sub(" ", str(value).lower())
    return " ".join(text.split())


def _tokens(value: Any) -> list[str]:
    return normalize(value).split()


def _string_similarity(a: str, b: str) -> float:
    """Ratio in [0, 1]; token-set agreement is taken into account.

    Plain sequence matching alone treats "Acme Corporation" and
    "Corporation Acme" as fairly different. Taking the better of the raw ratio
    and the sorted-token ratio makes the comparison order-insensitive.
    """
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    raw = SequenceMatcher(None, a, b).ratio()
    sorted_a = " ".join(sorted(a.split()))
    sorted_b = " ".join(sorted(b.split()))
    if sorted_a == a and sorted_b == b:
        return raw
    return max(raw, SequenceMatcher(None, sorted_a, sorted_b).ratio())


def attribute_similarity(
    a_data: dict[str, Any],
    b_data: dict[str, Any],
    on: Sequence[str],
    weights: dict[str, float] | None = None,
) -> float:
    """Weighted mean string similarity of two nodes' attributes.

    Fields absent from *both* nodes are skipped rather than scored as a match,
    so comparing two nodes that each lack an `email` does not inflate their
    similarity. If no field is present on either side the result is 0.0.
    """
    weights = weights or {}
    total_weight = 0.0
    accumulated = 0.0
    for key in on:
        left = normalize(a_data.get(key))
        right = normalize(b_data.get(key))
        if not left and not right:
            continue
        weight = float(weights.get(key, 1.0))
        if weight <= 0:
            continue
        accumulated += weight * _string_similarity(left, right)
        total_weight += weight
    if total_weight == 0:
        return 0.0
    return accumulated / total_weight


def _neighbors(G: nx.Graph, node: Hashable, relationship_aware: bool) -> set[Any]:
    """Undirected neighbour set, optionally qualified by relationship type.

    Direction is deliberately ignored: an entity duplicated by an extraction
    pipeline often ends up with its edges pointing the opposite way, and we
    still want to recognise the shared context.
    """
    seen: set[Any] = set()
    if G.is_directed():
        incident = list(G.out_edges(node, data=True)) + [
            (v, u, d) for u, v, d in G.in_edges(node, data=True)
        ]
    else:
        incident = [(node, other, data) for other, data in G[node].items()]
        if G.is_multigraph():
            incident = [
                (node, other, data)
                for other, keyed in G[node].items()
                for data in keyed.values()
            ]

    for _, other, data in incident:
        if relationship_aware:
            seen.add((data.get("relationship"), other))
        else:
            seen.add(other)
    return seen


def neighbor_overlap(
    G: nx.Graph,
    a: Hashable,
    b: Hashable,
    relationship_aware: bool = False,
) -> float:
    """Jaccard overlap of two nodes' neighbourhoods, in [0, 1].

    This is the signal a tabular linkage tool cannot compute. The two nodes are
    removed from each other's neighbour sets first, so a direct edge between
    them neither helps nor hurts.
    """
    left = _neighbors(G, a, relationship_aware)
    right = _neighbors(G, b, relationship_aware)
    if relationship_aware:
        left = {item for item in left if item[1] != b}
        right = {item for item in right if item[1] != a}
    else:
        left = left - {b}
        right = right - {a}
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


# --------------------------------------------------------------------------- #
# blocking
# --------------------------------------------------------------------------- #


def _block_keys(data: dict[str, Any], fields: Sequence[str], prefix: int) -> set[str]:
    """Cheap keys used to propose candidate pairs.

    A node gets one key per token prefix of each blocking field, so
    "Acme Corporation" and "Corporation Acme Ltd" share a key even though their
    full strings differ.
    """
    keys: set[str] = set()
    for field_name in fields:
        for token in _tokens(data.get(field_name)):
            if token:
                keys.add(token[:prefix])
    return keys


def _candidate_pairs(
    G: nx.Graph,
    nodes: list[Hashable],
    on: Sequence[str],
    block_on: Sequence[str] | None,
    block_prefix: int,
    respect_type: bool,
) -> set[tuple[Hashable, Hashable]]:
    """Propose pairs worth scoring.

    With `block_on` set (the default is the first field of `on`) this is
    near-linear. With `block_on=[]` every same-type pair is compared, which is
    quadratic and therefore guarded by `MAX_UNBLOCKED_NODES`.
    """
    if block_on is None:
        block_on = on[:1]

    def type_of(node: Hashable) -> Any:
        return G.nodes[node].get("type") if respect_type else None

    pairs: set[tuple[Hashable, Hashable]] = set()

    if not block_on:
        if len(nodes) > MAX_UNBLOCKED_NODES:
            raise ValueError(
                f"Refusing to compare {len(nodes)} nodes without blocking "
                f"({len(nodes) * (len(nodes) - 1) // 2} pairs). Pass block_on=[...] "
                f"with at least one field, or raise "
                f"graphfaker.resolve.MAX_UNBLOCKED_NODES if you mean it."
            )
        for i, left in enumerate(nodes):
            for right in nodes[i + 1 :]:
                if type_of(left) == type_of(right):
                    pairs.add((left, right))
        return pairs

    buckets: dict[tuple[Any, str], list[Hashable]] = {}
    for node in nodes:
        for key in _block_keys(G.nodes[node], block_on, block_prefix):
            buckets.setdefault((type_of(node), key), []).append(node)

    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        ordered = sorted(bucket, key=repr)
        for i, left in enumerate(ordered):
            for right in ordered[i + 1 :]:
                pairs.add((left, right) if repr(left) <= repr(right) else (right, left))
    return pairs


# --------------------------------------------------------------------------- #
# result object
# --------------------------------------------------------------------------- #


@dataclass
class ResolutionResult:
    """Outcome of a resolution pass.

    Nothing has been changed on the graph yet — call `apply()` to get a merged
    copy, or read `clusters` and decide for yourself.
    """

    graph: nx.Graph
    clusters: list[list[Hashable]]
    scores: dict[tuple[Hashable, Hashable], float]
    threshold: float
    candidates_considered: int
    on: list[str] = field(default_factory=list)

    @property
    def duplicate_nodes(self) -> int:
        """How many nodes would disappear if every cluster were merged."""
        return sum(len(cluster) - 1 for cluster in self.clusters)

    def mapping(self) -> dict[Hashable, Hashable]:
        """Absorbed node id -> canonical node id."""
        result: dict[Hashable, Hashable] = {}
        for cluster in self.clusters:
            canonical = pick_canonical(self.graph, cluster)
            for node in cluster:
                if node != canonical:
                    result[node] = canonical
        return result

    def apply(self, inplace: bool = False) -> nx.Graph:
        """Merge every cluster and return the resulting graph."""
        return merge_clusters(self.graph, self.clusters, inplace=inplace)

    def report(self) -> str:
        """Human-readable summary, safe to print in a notebook."""
        lines = [
            (
                f"Resolution over {self.graph.number_of_nodes()} nodes "
                f"on {self.on or '[]'} at threshold {self.threshold}"
            ),
            f"  candidate pairs scored : {self.candidates_considered}",
            f"  pairs above threshold  : {len(self.scores)}",
            f"  clusters found         : {len(self.clusters)}",
            f"  duplicate nodes        : {self.duplicate_nodes}",
        ]
        for cluster in self.clusters[:10]:
            canonical = pick_canonical(self.graph, cluster)
            names = ", ".join(
                f"{node}({self.graph.nodes[node].get('name', '?')})" for node in cluster
            )
            lines.append(f"    -> {canonical}: {names}")
        if len(self.clusters) > 10:
            lines.append(f"    ... and {len(self.clusters) - 10} more")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# resolution
# --------------------------------------------------------------------------- #


def resolve_entities(
    G: nx.Graph,
    on: Sequence[str] = ("name",),
    threshold: float = 0.85,
    structural_weight: float = 0.5,
    node_types: Iterable[str] | None = None,
    block_on: Sequence[str] | None = None,
    block_prefix: int = 4,
    respect_type: bool = True,
    relationship_aware: bool = False,
    weights: dict[str, float] | None = None,
) -> ResolutionResult:
    """Find clusters of nodes that appear to be the same entity.

    Args:
        G: The graph to examine. Never modified.
        on: Attribute keys compared for similarity, most identifying first.
        threshold: Minimum combined score for a pair to be linked, in [0, 1].
        structural_weight: How much neighbourhood overlap may lift a pair's
            score, in [0, 1]. 0 disables the graph signal entirely, reducing
            this to ordinary attribute matching. The combination is
            ``attr + w * structural * (1 - attr)``, so structure corroborates
            attribute evidence but never contradicts it — a pair with no shared
            neighbours simply scores its attribute similarity.
        node_types: Restrict to nodes whose `type` attribute is in this set.
        block_on: Fields used to propose candidates. Defaults to the first
            entry of `on`. Pass `[]` to compare all same-type pairs.
        block_prefix: Token prefix length for blocking keys.
        respect_type: Only compare nodes sharing the same `type` attribute.
        relationship_aware: Qualify neighbours by edge `relationship`, so
            structural overlap requires the same relationship to the same
            target. Stricter, and useful when edge types are trustworthy.
        weights: Per-field weights for attribute similarity.

    Returns:
        A `ResolutionResult`. The graph is untouched until you call `apply()`.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0, 1], got {threshold}")
    if not 0.0 <= structural_weight <= 1.0:
        raise ValueError(
            f"structural_weight must be in [0, 1], got {structural_weight}"
        )
    on = list(on)
    if not on:
        raise ValueError("`on` must name at least one attribute to compare")

    wanted = set(node_types) if node_types is not None else None
    nodes = sorted(
        (
            node
            for node, data in G.nodes(data=True)
            if wanted is None or data.get("type") in wanted
        ),
        key=repr,
    )
    if len(nodes) < 2:
        return ResolutionResult(G, [], {}, threshold, 0, on)

    pairs = _candidate_pairs(G, nodes, on, block_on, block_prefix, respect_type)

    accepted: dict[tuple[Hashable, Hashable], float] = {}
    for left, right in sorted(pairs, key=repr):
        attr = attribute_similarity(G.nodes[left], G.nodes[right], on, weights)
        if attr <= 0:
            continue
        # Skip the structural computation when attributes alone already decide
        # the outcome: structure can only raise the score, so a pair that
        # already clears the threshold is accepted, and one that cannot be
        # lifted to the threshold even by perfect overlap is rejected.
        best_possible = attr + structural_weight * (1.0 - attr)
        if best_possible < threshold:
            continue
        if attr >= threshold:
            accepted[(left, right)] = attr
            continue
        structural = neighbor_overlap(G, left, right, relationship_aware)
        score = attr + structural_weight * structural * (1.0 - attr)
        if score >= threshold:
            accepted[(left, right)] = score

    link_graph = nx.Graph()
    link_graph.add_nodes_from(node for pair in accepted for node in pair)
    link_graph.add_edges_from(accepted.keys())

    clusters = [
        sorted(component, key=repr)
        for component in nx.connected_components(link_graph)
        if len(component) > 1
    ]
    clusters.sort(key=lambda cluster: (-len(cluster), repr(cluster[0])))

    logger.info(
        "resolve_entities: %d candidate pairs, %d accepted, %d clusters, "
        "%d duplicate nodes",
        len(pairs),
        len(accepted),
        len(clusters),
        sum(len(c) - 1 for c in clusters),
    )
    return ResolutionResult(G, clusters, accepted, threshold, len(pairs), on)


# --------------------------------------------------------------------------- #
# merging
# --------------------------------------------------------------------------- #


def pick_canonical(G: nx.Graph, cluster: Sequence[Hashable]) -> Hashable:
    """Choose which node in a cluster survives the merge.

    The best-connected node wins, since it carries the most context. Ties break
    on the number of populated attributes and then on the node id, which keeps
    the choice deterministic.
    """
    return max(
        cluster,
        key=lambda node: (
            G.degree(node),
            len([v for v in G.nodes[node].values() if v not in (None, "")]),
            repr(node),
        ),
    )


def _merge_node_attributes(
    target: dict[str, Any], source: dict[str, Any], source_id: Hashable
) -> None:
    """Fold `source` attributes into `target`, recording disagreements.

    The canonical node's own values win. Anything it is missing gets filled in;
    anything it disagrees with is preserved under `_merged_variants` so the
    merge is never silently lossy.
    """
    variants: dict[str, list[Any]] = target.setdefault(MERGED_VARIANTS, {})
    for key, value in source.items():
        if key in (MERGED_FROM, MERGED_VARIANTS, MERGE_COUNT):
            continue
        current = target.get(key)
        if key not in target or current in (None, ""):
            target[key] = value
        elif current != value and value not in (None, ""):
            seen = variants.setdefault(key, [])
            if value not in seen:
                seen.append(value)
    target.setdefault(MERGED_FROM, []).append(source_id)


def _add_or_reinforce_edge(
    H: nx.Graph, u: Hashable, v: Hashable, data: dict[str, Any]
) -> None:
    """Attach a rewired edge, collapsing exact duplicates into a count.

    Multigraphs keep parallel edges as-is. For simple graphs, an edge that
    already exists with the same relationship gets its `_merge_count`
    incremented rather than being dropped without trace.
    """
    if H.is_multigraph():
        H.add_edge(u, v, **data)
        return
    if H.has_edge(u, v):
        existing = H.edges[u, v]
        if existing.get("relationship") == data.get("relationship"):
            existing[MERGE_COUNT] = existing.get(MERGE_COUNT, 1) + 1
            return
    H.add_edge(u, v, **data)


def merge_clusters(
    G: nx.Graph,
    clusters: Iterable[Sequence[Hashable]],
    inplace: bool = False,
    canonical: Callable[[nx.Graph, Sequence[Hashable]], Hashable] | None = None,
) -> nx.Graph:
    """Collapse each cluster into one node, rewiring its edges.

    This is the part that has no tabular equivalent. Every edge touching an
    absorbed node is re-pointed at the canonical node; self-loops created by
    the merge are dropped; and edges that become duplicates are counted rather
    than discarded.

    Args:
        G: Source graph.
        clusters: Groups of node ids to merge. Groups of one are ignored.
        inplace: Mutate `G` instead of working on a copy.
        canonical: Optional `(graph, cluster) -> node` override for choosing
            the surviving node.

    Returns:
        The merged graph — `G` itself when `inplace` is true, otherwise a copy.
    """
    H = G if inplace else G.copy()
    chooser = canonical or pick_canonical

    mapping: dict[Hashable, Hashable] = {}
    absorbed_by: dict[Hashable, list[Hashable]] = {}
    for cluster in clusters:
        members = sorted({node for node in cluster if node in H}, key=repr)
        if len(members) < 2:
            continue
        survivor = chooser(H, members)
        for node in members:
            if node != survivor:
                mapping[node] = survivor
                absorbed_by.setdefault(survivor, []).append(node)

    if not mapping:
        return H

    # Rewire first, using the complete mapping so that a cluster member
    # pointing at another cluster's member resolves correctly in one pass.
    for u, v, data in list(H.edges(data=True)):
        new_u = mapping.get(u, u)
        new_v = mapping.get(v, v)
        if (new_u, new_v) == (u, v):
            continue
        if new_u == new_v:
            continue  # the merge turned this into a self-loop
        _add_or_reinforce_edge(H, new_u, new_v, dict(data))

    # Then fold attributes and drop the absorbed nodes, which removes their
    # now-redundant edges along with them.
    for survivor, absorbed in absorbed_by.items():
        target = H.nodes[survivor]
        for node in sorted(absorbed, key=repr):
            _merge_node_attributes(target, dict(H.nodes[node]), node)
        H.remove_nodes_from(absorbed)
        if not target.get(MERGED_VARIANTS):
            target.pop(MERGED_VARIANTS, None)

    logger.info(
        "merge_clusters: %d nodes merged into %d canonical nodes (%d -> %d)",
        len(mapping),
        len(absorbed_by),
        G.number_of_nodes(),
        H.number_of_nodes(),
    )
    return H


# --------------------------------------------------------------------------- #
# scoring against known clusters
# --------------------------------------------------------------------------- #


def _as_cluster_map(
    clustering: Any, universe: set[Hashable] | None = None
) -> dict[Hashable, Any]:
    """Accept either a list of clusters or a node -> cluster-id mapping."""
    if isinstance(clustering, dict):
        return dict(clustering)
    result: dict[Hashable, Any] = {}
    for index, cluster in enumerate(clustering):
        for node in cluster:
            result[node] = index
    return result


def _pairs_within(cluster_map: dict[Hashable, Any]) -> set[tuple[Any, Any]]:
    groups: dict[Any, list[Hashable]] = {}
    for node, cluster_id in cluster_map.items():
        groups.setdefault(cluster_id, []).append(node)
    pairs: set[tuple[Any, Any]] = set()
    for members in groups.values():
        ordered = sorted(members, key=repr)
        for i, left in enumerate(ordered):
            for right in ordered[i + 1 :]:
                pairs.add((left, right))
    return pairs


def evaluate_clusters(predicted: Any, gold: Any) -> dict[str, float]:
    """Score a predicted clustering against a known-correct one.

    Both arguments may be a list of clusters or a `{node: cluster_id}` mapping.
    Any node named on one side but not the other is treated as a singleton on
    the missing side, so you can pass only the non-trivial clusters.

    Returns pairwise and B-cubed precision/recall/F1. Pairwise is the stricter
    and more familiar measure; B-cubed is less punishing about one large cluster
    being split and is the usual choice for entity resolution.

    This function makes no assumptions about where the gold labels came from and
    does not manufacture them — supply your own.
    """
    predicted_map = _as_cluster_map(predicted)
    gold_map = _as_cluster_map(gold)

    universe = set(predicted_map) | set(gold_map)
    for index, node in enumerate(sorted(universe, key=repr)):
        predicted_map.setdefault(node, f"__pred_singleton_{index}")
        gold_map.setdefault(node, f"__gold_singleton_{index}")

    predicted_pairs = _pairs_within(predicted_map)
    gold_pairs = _pairs_within(gold_map)
    shared = predicted_pairs & gold_pairs

    def ratio(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 1.0

    pairwise_precision = ratio(len(shared), len(predicted_pairs))
    pairwise_recall = ratio(len(shared), len(gold_pairs))

    predicted_groups: dict[Any, set[Hashable]] = {}
    gold_groups: dict[Any, set[Hashable]] = {}
    for node, cluster_id in predicted_map.items():
        predicted_groups.setdefault(cluster_id, set()).add(node)
    for node, cluster_id in gold_map.items():
        gold_groups.setdefault(cluster_id, set()).add(node)

    b3_precision_total = 0.0
    b3_recall_total = 0.0
    for node in universe:
        in_predicted = predicted_groups[predicted_map[node]]
        in_gold = gold_groups[gold_map[node]]
        overlap = len(in_predicted & in_gold)
        b3_precision_total += overlap / len(in_predicted)
        b3_recall_total += overlap / len(in_gold)
    count = len(universe) or 1

    def f1(precision: float, recall: float) -> float:
        return (
            2 * precision * recall / (precision + recall)
            if (precision + recall)
            else 0.0
        )

    b3_precision = b3_precision_total / count
    b3_recall = b3_recall_total / count

    return {
        "pairwise_precision": pairwise_precision,
        "pairwise_recall": pairwise_recall,
        "pairwise_f1": f1(pairwise_precision, pairwise_recall),
        "b_cubed_precision": b3_precision,
        "b_cubed_recall": b3_recall,
        "b_cubed_f1": f1(b3_precision, b3_recall),
        "predicted_pairs": len(predicted_pairs),
        "gold_pairs": len(gold_pairs),
        "correct_pairs": len(shared),
    }
