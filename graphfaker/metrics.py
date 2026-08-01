"""Structural measurements for generated and extracted graphs.

Claiming a generator is "realistic" means nothing without numbers attached, so
this module provides the numbers. It exists mainly to distinguish a graph with
real-world structure from one produced by uniform random attachment, which is
what GraphFaker's synthetic generator produced before version 0.5.

The discriminating statistics:

``degree_fano``
    Variance of degree divided by mean degree. Uniform random attachment gives a
    Poisson degree distribution, whose variance equals its mean, so this sits
    near 1.0. Real social, citation, and web graphs are heavy-tailed and land far
    above it. This is the single clearest test of whether hubs exist.

``degree_gini``
    Inequality of the degree distribution, 0 (every node identical) to 1 (one
    node holds everything). Uniform attachment produces a low value.

``average_clustering``
    How often two neighbours of a node are themselves connected. Uniform
    attachment leaves this near ``mean_degree / n`` — effectively zero on a
    sparse graph — while real graphs cluster strongly, because people who share a
    friend tend to meet.

``degree_assortativity``
    Whether high-degree nodes attach to other high-degree nodes. Social graphs
    are usually mildly positive; technological ones negative.

``community_modularity``
    How well the graph divides into groups, computed against the node
    ``community`` attribute when present and otherwise against greedy
    modularity communities. Uniform attachment has no group structure to find.

Example:
    >>> from graphfaker import GraphFaker
    >>> from graphfaker.metrics import graph_stats, compare_topology
    >>> realistic = GraphFaker(seed=1).generate_graph(total_nodes=500)
    >>> uniform = GraphFaker(seed=1).generate_graph(total_nodes=500,
    ...                                             topology="uniform")
    >>> print(compare_topology({"realistic": realistic, "uniform": uniform}))
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import networkx as nx

from graphfaker.logger import logger

__all__ = [
    "as_simple_undirected",
    "compare_topology",
    "degree_gini",
    "graph_stats",
]


def as_simple_undirected(G: nx.Graph) -> nx.Graph:
    """Collapse to a simple undirected graph.

    Clustering, modularity, and assortativity are defined on undirected simple
    graphs. Reciprocal pairs (a FRIENDS_WITH written both ways) would otherwise
    be double counted, and self-loops distort clustering.
    """
    H = nx.Graph()
    H.add_nodes_from(G.nodes(data=True))
    H.add_edges_from((u, v) for u, v in G.edges() if u != v)
    return H


def degree_gini(degrees: list[int]) -> float:
    """Gini coefficient of a degree sequence, in [0, 1]."""
    if not degrees:
        return 0.0
    ordered = sorted(degrees)
    total = sum(ordered)
    if total == 0:
        return 0.0
    n = len(ordered)
    weighted = sum((index + 1) * value for index, value in enumerate(ordered))
    return (2 * weighted) / (n * total) - (n + 1) / n


def _percentile(ordered: list[int], fraction: float) -> float:
    if not ordered:
        return 0.0
    position = max(0, min(len(ordered) - 1, round(fraction * (len(ordered) - 1))))
    return float(ordered[position])


def graph_stats(G: nx.Graph, community_attr: str = "community") -> dict[str, Any]:
    """Structural summary of a graph.

    Args:
        G: Any NetworkX graph. Directed and multigraphs are collapsed first.
        community_attr: Node attribute holding a ground-truth grouping. When
            absent, modularity is computed against greedy communities instead,
            which measures whether *any* group structure exists rather than
            whether a known one was reproduced.

    Returns:
        A dict of scalars, safe to serialise and put in a table.
    """
    H = as_simple_undirected(G)
    n = H.number_of_nodes()
    m = H.number_of_edges()
    if n == 0:
        return {"nodes": 0, "edges": 0}

    degrees = [degree for _, degree in H.degree()]
    ordered = sorted(degrees)
    mean_degree = sum(degrees) / n
    variance = sum((degree - mean_degree) ** 2 for degree in degrees) / n
    median = _percentile(ordered, 0.5)

    stats: dict[str, Any] = {
        "nodes": n,
        "edges": m,
        "density": nx.density(H),
        "mean_degree": mean_degree,
        "max_degree": max(degrees),
        "median_degree": median,
        # Poisson (uniform attachment) sits near 1.0; heavy tails far above.
        "degree_fano": variance / mean_degree if mean_degree else 0.0,
        "degree_gini": degree_gini(degrees),
        "degree_p99_over_median": (
            _percentile(ordered, 0.99) / median if median else float("inf")
        ),
        "isolated_nodes": sum(1 for degree in degrees if degree == 0),
        "average_clustering": nx.average_clustering(H) if m else 0.0,
        # A sparse uniform-attachment graph clusters at roughly mean_degree / n,
        # which is the baseline the measured value should be compared against.
        "clustering_baseline": mean_degree / n if n else 0.0,
    }

    try:
        stats["degree_assortativity"] = nx.degree_assortativity_coefficient(H)
    except (ZeroDivisionError, ValueError, nx.NetworkXError):
        # Undefined when every node shares a degree.
        stats["degree_assortativity"] = float("nan")

    groups = {
        node: data.get(community_attr)
        for node, data in H.nodes(data=True)
        if data.get(community_attr) is not None
    }
    if len(groups) == n and len(set(groups.values())) > 1:
        partition: dict[Any, set[Any]] = {}
        for node, group in groups.items():
            partition.setdefault(group, set()).add(node)
        stats["community_modularity"] = nx.community.modularity(
            H, list(partition.values())
        )
        stats["community_source"] = community_attr
        try:
            stats["community_assortativity"] = nx.attribute_assortativity_coefficient(
                H, community_attr
            )
        except (ZeroDivisionError, ValueError, nx.NetworkXError):
            stats["community_assortativity"] = float("nan")
    elif m:
        detected = list(nx.community.greedy_modularity_communities(H))
        stats["community_modularity"] = nx.community.modularity(H, detected)
        stats["community_source"] = "greedy (detected)"
        stats["detected_communities"] = len(detected)

    return stats


def numeric_assortativity(G: nx.Graph, attribute: str) -> float:
    """Correlation of a numeric attribute across edges.

    Positive means like attaches to like — age homophily, for instance. Returns
    NaN when the attribute is missing or constant.
    """
    H = as_simple_undirected(G)
    present = [
        node for node, data in H.nodes(data=True) if isinstance(data.get(attribute), (int, float))
    ]
    if len(present) < 2:
        return float("nan")
    try:
        return nx.numeric_assortativity_coefficient(H.subgraph(present), attribute)
    except (ZeroDivisionError, ValueError, nx.NetworkXError):
        return float("nan")


_TABLE_ROWS = [
    ("nodes", "{:.0f}"),
    ("edges", "{:.0f}"),
    ("mean_degree", "{:.2f}"),
    ("max_degree", "{:.0f}"),
    ("degree_fano", "{:.2f}"),
    ("degree_gini", "{:.3f}"),
    ("degree_p99_over_median", "{:.2f}"),
    ("average_clustering", "{:.4f}"),
    ("clustering_baseline", "{:.4f}"),
    ("degree_assortativity", "{:+.3f}"),
    ("community_modularity", "{:.3f}"),
    ("isolated_nodes", "{:.0f}"),
]


def compare_topology(
    graphs: Mapping[str, nx.Graph], community_attr: str = "community"
) -> str:
    """Render a side-by-side table of `graph_stats` for several graphs.

    Useful for showing what a change to the generator actually did, rather than
    asserting that it helped.
    """
    computed = {
        label: graph_stats(graph, community_attr) for label, graph in graphs.items()
    }
    labels = list(computed)
    width = max([len("degree_p99_over_median")] + [len(label) for label in labels]) + 2

    lines = ["metric".ljust(width) + "".join(label.rjust(14) for label in labels)]
    lines.append("-" * (width + 14 * len(labels)))
    for key, fmt in _TABLE_ROWS:
        if not any(key in stats for stats in computed.values()):
            continue
        row = key.ljust(width)
        for label in labels:
            value = computed[label].get(key)
            row += (fmt.format(value) if isinstance(value, (int, float)) else "-").rjust(14)
        lines.append(row)

    logger.debug("compare_topology over %d graphs", len(graphs))
    return "\n".join(lines)
