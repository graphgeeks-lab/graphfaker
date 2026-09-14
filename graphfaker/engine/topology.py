"""Edge formation.

The social model here is sequential by nature (each edge looks at the
degrees, neighbourhoods and attributes that earlier edges produced), so it
runs on a NetworkX graph and is the right tool up to about a million edges.
Vectorised models for larger graphs will live beside it and produce the same
edge tables.

This is a port of the pre-schema ``GraphFaker.generate_edges``; the mechanisms
(coverage pass, degree-preferential pools, community-local candidate pools,
triadic closure, affinity ranking, functional relationships, top-up) are
unchanged, only their parameters now come from the schema.
"""

from __future__ import annotations

import random
from collections.abc import Hashable, Sequence
from typing import Any

import networkx as nx
import polars as pl

from graphfaker.backends.tables import ID, RELATIONSHIP, GraphTables
from graphfaker.engine.sampling import RowContext, draw
from graphfaker.engine.seeding import Streams
from graphfaker.logger import logger
from graphfaker.schema.graph import EdgeType, GraphSchema, Relationship
from graphfaker.schema.topology import SocialTopology, UniformTopology


class AttachmentPool:
    """Samples nodes with probability rising in their degree.

    The standard repeated-entry trick: a node is appended each time it gains
    an edge, so a uniform draw from the list is proportional to ``degree + 1``.
    Seeding with every node once supplies the ``+1`` so unattached nodes stay
    reachable.
    """

    def __init__(self, nodes: Sequence[Any], uniform_rate: float):
        self._all = list(nodes)
        self._weighted = list(nodes)
        self._uniform_rate = uniform_rate

    def __bool__(self) -> bool:
        return bool(self._all)

    def record(self, node: Any) -> None:
        self._weighted.append(node)

    def sample(self, rand: random.Random) -> Any:
        if rand.random() < self._uniform_rate:
            return rand.choice(self._all)
        return rand.choice(self._weighted)


class SocialEdgeBuilder:
    """Forms edges on ``G`` in place according to a :class:`SocialTopology`
    (or :class:`UniformTopology`) and the schema's edge families."""

    def __init__(self, schema: GraphSchema, streams: Streams):
        self.schema = schema
        self.model = schema.topology
        self.realistic = isinstance(self.model, SocialTopology)
        self.streams = streams
        self.rand = streams.rand
        self.ctx = RowContext(streams=streams)

    # ---------------------------------------------------------------- scoring

    def _affinity(self, G: nx.DiGraph, source: Hashable, target: Hashable) -> float:
        """How plausible an edge is; only ever used to rank a small sample, so
        the scale is arbitrary. Shared group and similar attributes win."""
        model = self.model
        assert isinstance(model, SocialTopology)
        a, b = G.nodes[source], G.nodes[target]
        score = 0.0
        if a.get(model.group) == b.get(model.group):
            score += model.group_affinity
        if model.prominence:
            score += model.prominence_affinity * float(b.get(model.prominence) or 0.0)
        for attr, affinity in model.numeric_affinity.items():
            x, y = a.get(attr), b.get(attr)
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                score += affinity.weight * max(0.0, 1.0 - abs(x - y) / affinity.scale)
        for attr, weight in model.categorical_affinity.items():
            if a.get(attr) is not None and a.get(attr) == b.get(attr):
                score += weight
        return score

    def _choose_affine(
        self,
        G: nx.DiGraph,
        pool: AttachmentPool,
        source: Hashable,
        local_pool: AttachmentPool | None = None,
    ) -> Any:
        """Draw several candidates by degree preference, keep the most
        plausible. Most candidates come from the source's own group; relying
        on the score alone leaves modularity near zero because a global sample
        rarely contains a same-group candidate to prefer."""
        model = self.model
        assert isinstance(model, SocialTopology)
        best, best_score = None, float("-inf")
        for _ in range(model.affinity_sample_size):
            source_pool = pool
            if local_pool and self.rand.random() < model.same_group_rate:
                source_pool = local_pool
            candidate = source_pool.sample(self.rand)
            if candidate == source:
                continue
            score = self._affinity(G, source, candidate)
            if score > best_score:
                best, best_score = candidate, score
        return best

    # ------------------------------------------------------------------ edges

    def _edge_attributes(self, rel: Relationship) -> dict[str, Any]:
        return {name: draw(sampler, self.ctx, 0) for name, sampler in rel.attributes.items()}

    def _add(self, G: nx.DiGraph, source: Any, target: Any, rel: Relationship) -> None:
        attrs = self._edge_attributes(rel)
        G.add_edge(source, target, relationship=rel.name, **attrs)
        if rel.bidirectional:
            G.add_edge(target, source, relationship=rel.name, **attrs)

    def build(self, G: nx.DiGraph) -> None:
        schema, model = self.schema, self.model
        total_edges = schema.total_edges
        if total_edges <= 0:
            # An explicit zero is a request, and top-up would still attach
            # every isolated node once.
            return
        group = model.group if isinstance(model, SocialTopology) else None
        uniform_rate = model.uniform_attachment_rate if isinstance(model, SocialTopology) else 0.0

        nodes_by_type: dict[str, list[Any]] = {node.name: [] for node in schema.nodes}
        for node, data in G.nodes(data=True):
            if data.get("type") in nodes_by_type:
                nodes_by_type[data["type"]].append(node)

        pools = {
            name: AttachmentPool(nodes, uniform_rate) for name, nodes in nodes_by_type.items() if nodes
        }
        local_pools: dict[tuple[str, Any], AttachmentPool] = {}
        if group:
            for name, nodes in nodes_by_type.items():
                grouped: dict[Any, list[Any]] = {}
                for node in nodes:
                    grouped.setdefault(G.nodes[node].get(group), []).append(node)
                for key, members in grouped.items():
                    local_pools[(name, key)] = AttachmentPool(members, uniform_rate)

        assigned: dict[tuple[Any, str], Any] = {}
        # Insertion-ordered dicts stand in for sets: iterating a set of strings
        # depends on PYTHONHASHSEED, which would make runs differ across
        # processes for the same seed.
        neighbours: dict[Any, dict[Any, None]] = {node: {} for node in G.nodes()}

        for edge in schema.edges:
            num_edges = int(total_edges * edge.share)
            src_nodes, tgt_nodes = nodes_by_type.get(edge.source, []), nodes_by_type.get(edge.target, [])
            if not src_nodes or not tgt_nodes:
                continue

            # Pure preferential attachment leaves a large share of nodes never
            # selected; the first pass over each family walks every source once.
            coverage = list(src_nodes)
            self.rand.shuffle(coverage)

            # Budget counts edges actually added: a bidirectional relationship
            # adds two, a skipped functional one adds none.
            added = index = attempts = 0
            while added < num_edges and attempts < num_edges * 8 + 32:
                attempts += 1
                index += 1
                before = G.number_of_edges()
                rel = self.rand.choice(edge.relationships)

                if not self.realistic:
                    source = self.rand.choice(src_nodes)
                    target = self.rand.choice(tgt_nodes)
                else:
                    source = (
                        coverage[index - 1]
                        if index <= len(coverage)
                        else pools[edge.source].sample(self.rand)
                    )
                    local = local_pools.get((edge.target, G.nodes[source].get(group)))

                    if rel.functional:
                        key = (source, rel.name)
                        if key in assigned:
                            continue
                        if rel.target_from:
                            target = G.nodes[source].get(rel.target_from) or None
                        else:
                            target = self._choose_affine(G, pools[edge.target], source, local)
                        if target is None:
                            continue
                        assigned[key] = target
                    elif (
                        edge.closure
                        and neighbours[source]
                        and self.rand.random() < model.triadic_closure_rate  # type: ignore[union-attr]
                    ):
                        # Befriend a friend of a friend: the sole source of clustering.
                        friend = self.rand.choice(tuple(neighbours[source]))
                        candidates = [
                            c
                            for c in neighbours.get(friend, ())
                            if c != source
                            and c not in neighbours[source]
                            and G.nodes[c].get("type") == edge.target
                        ]
                        target = (
                            self.rand.choice(candidates)
                            if candidates
                            else self._choose_affine(G, pools[edge.target], source, local)
                        )
                    else:
                        if rel.target_from:
                            target = G.nodes[source].get(rel.target_from) or None
                        else:
                            target = self._choose_affine(G, pools[edge.target], source, local)

                if target is None or source == target:
                    continue

                self._add(G, source, target, rel)
                added += G.number_of_edges() - before

                if self.realistic:
                    pools[edge.source].record(source)
                    pools[edge.target].record(target)
                    neighbours[source][target] = None
                    neighbours[target][source] = None

        if self.realistic:
            self._top_up(G, nodes_by_type, pools, local_pools, neighbours)

    def _top_up(
        self,
        G: nx.DiGraph,
        nodes_by_type: dict[str, list[Any]],
        pools: dict[str, AttachmentPool],
        local_pools: dict[tuple[str, Any], AttachmentPool],
        neighbours: dict[Any, dict[Any, None]],
    ) -> None:
        """Attach leftover nodes and make up the edge shortfall.

        Target-only types (places, events, products) can be missed by the
        coverage pass, and functional relationships that were skipped leave
        the budget ~15% short. Only repeatable relationships are used, so
        topping up cannot hand anyone a second birthplace.
        """
        schema, model = self.schema, self.model
        assert isinstance(model, SocialTopology)
        total_edges = schema.total_edges

        repeatable: list[tuple[EdgeType, list[Relationship]]] = []
        for edge in schema.edges:
            usable = [r for r in edge.relationships if not r.functional and not r.target_from]
            if usable and nodes_by_type.get(edge.source) and nodes_by_type.get(edge.target):
                repeatable.append((edge, usable))
        if not repeatable:
            return

        reachable_as_target: dict[str, list[tuple[str, list[Relationship]]]] = {}
        for edge, usable in repeatable:
            reachable_as_target.setdefault(edge.target, []).append((edge.source, usable))

        def connect(source: Any, target: Any, rel: Relationship) -> bool:
            if source is None or target is None or source == target or G.has_edge(source, target):
                return False
            self._add(G, source, target, rel)
            pools[G.nodes[source]["type"]].record(source)
            pools[G.nodes[target]["type"]].record(target)
            neighbours[source][target] = None
            neighbours[target][source] = None
            return True

        for node in list(G.nodes()):
            if G.degree(node) > 0:
                continue
            options = reachable_as_target.get(G.nodes[node].get("type"))
            if not options:
                continue
            src_type, usable = self.rand.choice(options)
            pool = local_pools.get((src_type, G.nodes[node].get(model.group))) or pools[src_type]
            connect(pool.sample(self.rand), node, self.rand.choice(usable))

        weights = [edge.share for edge, _ in repeatable]
        attempts, limit = 0, max(200, 12 * total_edges)
        while G.number_of_edges() < total_edges and attempts < limit:
            attempts += 1
            edge, usable = self.rand.choices(repeatable, weights)[0]
            source = pools[edge.source].sample(self.rand)
            local = local_pools.get((edge.target, G.nodes[source].get(model.group)))
            target = self._choose_affine(G, pools[edge.target], source, local)
            rel = self.rand.choice(usable)
            if not connect(source, target, rel):
                # Affinity concentrates candidates, so late in the fill the same
                # plausible pairs keep coming back already connected. One
                # uniform retry stops the budget from stalling short.
                connect(source, self.rand.choice(nodes_by_type[edge.target]), rel)

        logger.debug(
            "edges: %d after top-up (%d requested, %d attempts)",
            G.number_of_edges(),
            total_edges,
            attempts,
        )


# ------------------------------------------------------------------ derived


def apply_derived(G: nx.DiGraph, schema: GraphSchema) -> None:
    """Fill ``DegreeDerived`` attributes from the structure that was built."""
    for node_type in schema.nodes:
        if not node_type.derived:
            continue
        for node, data in G.nodes(data=True):
            if data.get("type") != node_type.name:
                continue
            for attr, derived in node_type.derived.items():
                edges = G.in_edges(node, data=True) if derived.direction == "in" else G.out_edges(node, data=True)
                count = sum(1 for _, _, e in edges if e.get(RELATIONSHIP) == derived.relationship)
                scope = {**data, "count": count, "max": max, "min": min, "int": int, "round": round}
                data[attr] = eval(derived.expr, {"__builtins__": {}}, scope)


# ----------------------------------------------------------------- driver


def build_edges(tables: GraphTables, schema: GraphSchema, streams: Streams) -> GraphTables:
    """Run the schema's topology over node tables and return tables with edges
    and derived attributes filled in."""
    if not isinstance(schema.topology, (SocialTopology, UniformTopology)):
        raise TypeError(f"unsupported topology {schema.topology.kind!r}")
    G = tables.to_networkx()
    SocialEdgeBuilder(schema, streams).build(G)
    apply_derived(G, schema)
    built = GraphTables.from_networkx(G)

    nodes: dict[str, pl.DataFrame] = {}
    for node_type in schema.nodes:
        frame = tables.nodes[node_type.name]
        if node_type.derived and node_type.name in built.nodes:
            derived_cols = [ID, *node_type.derived]
            frame = frame.join(built.nodes[node_type.name].select(derived_cols), on=ID, how="left")
        nodes[node_type.name] = frame
    return GraphTables(nodes=nodes, edges=built.edges)

