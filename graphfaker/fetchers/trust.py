"""
Trust graph fetcher for generating realistic directed social trust graphs.

Produces homogeneous User→User graphs with configurable topology:
- Power-law degree distribution (Barabási-Albert preferential attachment)
- Community structure (Stochastic Block Model)
- Configurable reciprocity (directed trust links)
- Distrust links (for compromised account simulation)
- Small-world shortcuts (Watts-Strogatz rewiring)
"""

import math
import random
import secrets
from typing import Optional

import networkx as nx
import numpy as np
from faker import Faker

from graphfaker.logger import logger

fake = Faker()


class TrustGraphFetcher:
    """Generates realistic directed social trust graphs.

    All methods are static — the class acts as a namespace,
    consistent with the existing fetcher pattern (OSMGraphFetcher,
    FlightGraphFetcher).
    """

    @staticmethod
    def generate_users(
        G: nx.DiGraph,
        total_users: int,
        community_labels: dict,
    ) -> None:
        """Add User nodes with Faker-generated attributes.

        Args:
            G: Target directed graph.
            total_users: Number of user nodes to create.
            community_labels: Mapping of node index -> community ID.
        """
        # Direct dict update is faster than G.add_node() for existing nodes
        node_data = G.nodes
        for i in range(total_users):
            node_id = f"user_{i}"
            node_data[node_id].update(
                type="User",
                name=fake.name(),
                public_key=secrets.token_hex(32),
                created_at=str(fake.date_time_between(start_date="-3y")),
                community=community_labels.get(i, 0),
                is_bot=False,
                is_compromised=False,
            )

    @staticmethod
    def _build_community_sizes(
        total_users: int,
        num_communities: Optional[int],
        seed: Optional[int] = None,
        min_community_size: int = 50,
    ) -> list:
        """Compute community sizes for the SBM using a log-normal distribution.

        Draws sizes from a log-normal distribution to produce a realistic
        mix of a few large communities and many smaller ones, then rounds
        and clamps to guarantee each community has at least
        ``min_community_size`` members.

        Args:
            total_users: Total number of users.
            num_communities: Number of communities. If None, defaults to ~500 users per community.
            seed: Random seed for reproducibility.
            min_community_size: Floor for every community (default 50).

        Returns:
            List of community sizes summing to total_users.
        """
        if num_communities is None:
            num_communities = max(2, round(total_users / 500))
        num_communities = min(num_communities, total_users)

        # For very small graphs where log-normal doesn't make sense,
        # fall back to equal partitioning
        if num_communities <= 1 or total_users < num_communities * min_community_size:
            base_size = total_users // num_communities
            sizes = [base_size] * num_communities
            sizes[-1] += total_users - sum(sizes)
            return sizes

        rng = np.random.default_rng(seed)
        raw = rng.lognormal(mean=0.0, sigma=1.0, size=num_communities)
        raw = raw / raw.sum()  # normalize to fractions

        # Scale to total_users and round
        sizes = [max(min_community_size, int(round(f * total_users))) for f in raw]

        # Fix the total: redistribute difference to the largest community
        diff = total_users - sum(sizes)
        largest_idx = sizes.index(max(sizes))
        sizes[largest_idx] += diff

        # Safety: if adjustment pushed largest below floor, redistribute
        if sizes[largest_idx] < min_community_size:
            sizes[largest_idx] = min_community_size
            diff = total_users - sum(sizes)
            # spread across all communities proportionally
            for i in range(abs(diff)):
                sizes[i % num_communities] += 1 if diff > 0 else -1

        return sizes

    @staticmethod
    def _build_probability_matrix(
        sizes: list,
        avg_trust_links: int,
        community_mixing: float,
        total_users: int,
    ) -> list:
        """Build the SBM connection probability matrix.

        Partitions the target average degree into within-community and
        between-community contributions using community_mixing as the
        fraction of total edges that cross community boundaries.

        With variable community sizes, each diagonal block gets its own
        within-community probability. Off-diagonal blocks use a single
        uniform between-community probability to keep the matrix symmetric
        (required by NetworkX's undirected SBM).

        Args:
            sizes: List of community sizes (one per community).
            avg_trust_links: Target average degree.
            community_mixing: Fraction of total edges that are between communities (0.0 to 1.0).
            total_users: Total number of users.

        Returns:
            len(sizes) x len(sizes) symmetric probability matrix.
        """
        num_communities = len(sizes)
        within_per_node = (1.0 - community_mixing) * avg_trust_links
        between_per_node = community_mixing * avg_trust_links

        # Between-community: use mean community size for a single symmetric value
        mean_size = total_users / num_communities
        p_between = min(1.0, between_per_node / max(1, total_users - mean_size))

        p_matrix = []
        for i in range(num_communities):
            row = []
            p_within_i = min(1.0, within_per_node / max(1, sizes[i] - 1))
            for j in range(num_communities):
                row.append(p_within_i if i == j else p_between)
            p_matrix.append(row)
        return p_matrix

    @staticmethod
    def _apply_preferential_attachment(
        G: nx.Graph,
        community_labels: dict,
        pa_fraction: float,
        rng: random.Random,
    ) -> None:
        """Rewire within-community edges using preferential attachment.

        For each within-community edge, with probability pa_fraction,
        replace the target with a degree-proportional random node in the
        same community. Creates hub nodes (power-law-ish degree distribution).

        Only within-community edges are rewired to preserve community structure.

        Args:
            G: Undirected graph to modify in-place.
            community_labels: Mapping of node index -> community ID.
            pa_fraction: Probability of rewiring each within-community edge.
            rng: Random instance for reproducibility.
        """
        comm_nodes: dict = {}
        for node, comm in community_labels.items():
            comm_nodes.setdefault(comm, []).append(node)

        # Cache: degree array per community, indexed by position in comm_nodes[comm]
        comm_degrees: dict = {}
        comm_node_idx: dict = {}
        for comm, members in comm_nodes.items():
            idx_map = {node: i for i, node in enumerate(members)}
            comm_node_idx[comm] = idx_map
            comm_degrees[comm] = np.array(
                [G.degree(w) + 1 for w in members], dtype=np.float64
            )

        within_edges = [
            (u, v) for u, v in G.edges()
            if community_labels.get(u) == community_labels.get(v)
        ]

        for u, v in within_edges:
            if rng.random() >= pa_fraction:
                continue

            comm = community_labels[u]
            members = comm_nodes[comm]
            degrees = comm_degrees[comm]

            # Weighted sample using cached degree array + searchsorted
            cumsum = degrees.cumsum()
            total = cumsum[-1]
            r = rng.random() * total
            chosen_idx = int(np.searchsorted(cumsum, r))
            chosen_idx = min(chosen_idx, len(members) - 1)
            new_target = members[chosen_idx]

            if new_target != u and not G.has_edge(u, new_target):
                G.remove_edge(u, v)
                G.add_edge(u, new_target)

                # Incrementally update degree cache
                idx_map = comm_node_idx[comm]
                if v in idx_map:
                    degrees[idx_map[v]] = max(1, degrees[idx_map[v]] - 1)
                if new_target in idx_map:
                    degrees[idx_map[new_target]] += 1

    @staticmethod
    def _apply_reciprocity(
        G_undirected: nx.Graph,
        reciprocity: float,
        rng: random.Random,
    ) -> nx.DiGraph:
        """Convert an undirected graph to directed with configurable reciprocity.

        For each undirected edge:
        - With probability `reciprocity`: create both A→B and B→A (mutual trust)
        - With probability `1 - reciprocity`: create only one direction (random)

        All edges get relationship="TRUSTS".

        Args:
            G_undirected: Source undirected graph.
            reciprocity: Fraction of edges that become mutual.
            rng: Random instance for reproducibility.

        Returns:
            New directed graph with TRUSTS edges.
        """
        G_dir = nx.DiGraph()
        G_dir.add_nodes_from(G_undirected.nodes(data=True))

        for u, v in G_undirected.edges():
            if rng.random() < reciprocity:
                G_dir.add_edge(u, v, relationship="TRUSTS")
                G_dir.add_edge(v, u, relationship="TRUSTS")
            else:
                if rng.random() < 0.5:
                    G_dir.add_edge(u, v, relationship="TRUSTS")
                else:
                    G_dir.add_edge(v, u, relationship="TRUSTS")

        return G_dir

    @staticmethod
    def _add_organic_distrust(
        G: nx.DiGraph,
        avg_distrust_links: float,
        rng: random.Random,
        np_rng: np.random.Generator,
    ) -> None:
        """Add Poisson-distributed DISTRUSTS edges per user.

        Each user draws k ~ Poisson(avg_distrust_links) distrust targets,
        producing a realistic distribution where most users distrust at
        least one entity (~86.5% at lambda=2.0) but some naturally have none.

        Args:
            G: Directed graph to modify in-place.
            avg_distrust_links: Lambda for Poisson draw per user.
            rng: Random instance for reproducibility.
            np_rng: NumPy random generator for Poisson draws.
        """
        if avg_distrust_links <= 0:
            return

        nodes = list(G.nodes())
        n = len(nodes)
        node_to_idx = {node: i for i, node in enumerate(nodes)}
        draws = np_rng.poisson(lam=avg_distrust_links, size=n)

        # Pre-build successor index sets for all nodes: O(E) total
        successor_idxs: list = [set() for _ in range(n)]
        for u, v in G.edges():
            ui = node_to_idx.get(u)
            vi = node_to_idx.get(v)
            if ui is not None and vi is not None:
                successor_idxs[ui].add(vi)

        for idx in range(n):
            k = int(draws[idx])
            if k == 0:
                continue

            excluded = successor_idxs[idx]
            excluded.add(idx)  # no self-loops

            n_candidates = n - len(excluded)
            if n_candidates <= 0:
                continue
            k = min(k, n_candidates)

            # Rejection sampling: with ~15 excluded out of 10k+,
            # collision rate is <0.2%, so this is nearly O(k)
            selected: set = set()
            while len(selected) < k:
                r = np_rng.integers(0, n)
                if r not in excluded and r not in selected:
                    selected.add(r)

            src = nodes[idx]
            for si in selected:
                G.add_edge(src, nodes[si], relationship="DISTRUSTS")

    @staticmethod
    def _add_bot_clusters(
        G: nx.DiGraph,
        num_compromised: int,
        bots_per_cluster: list,
        rng: random.Random,
        np_rng: np.random.Generator,
    ) -> dict:
        """Add bot cluster substructures around compromised accounts.

        Selects high-degree existing users as compromised accounts, then
        creates dense bot clusters connected to them — matching the
        attack pattern where compromised accounts bridge to bot-generated
        account clusters.

        Args:
            G: Directed graph to modify in-place.
            num_compromised: Number of existing users to mark as compromised.
            bots_per_cluster: List of bot counts, one per compromised account.
            rng: Random instance for reproducibility.
            np_rng: NumPy random generator.

        Returns:
            Dict mapping compromised node IDs to list of their bot node IDs.
        """
        if num_compromised <= 0 or not bots_per_cluster:
            return {}

        # Select compromised accounts: above-median out-degree users
        user_nodes = [n for n, d in G.nodes(data=True) if not d.get("is_bot", False)]
        out_degrees = {n: G.out_degree(n) for n in user_nodes}
        median_deg = sorted(out_degrees.values())[len(out_degrees) // 2]
        high_degree = [n for n, d in out_degrees.items() if d >= median_deg]
        num_compromised = min(num_compromised, len(high_degree))
        compromised = rng.sample(high_degree, num_compromised)

        for node in compromised:
            G.nodes[node]["is_compromised"] = True

        # Build community map for concentrated distrust later
        communities_map: dict = {}
        for node, data in G.nodes(data=True):
            c = data.get("community", -1)
            if c >= 0:
                communities_map.setdefault(c, []).append(node)

        cluster_map = {}
        if seed_val := np_rng.integers(0, 2**31):
            Faker.seed(int(seed_val))

        for ci, comp_node in enumerate(compromised):
            n_bots = bots_per_cluster[ci] if ci < len(bots_per_cluster) else bots_per_cluster[-1]
            bot_ids = []
            for bi in range(n_bots):
                bot_id = f"bot_{ci}_{bi}"
                G.add_node(
                    bot_id,
                    type="User",
                    is_bot=True,
                    is_compromised=False,
                    name=fake.name(),
                    public_key=secrets.token_hex(32),
                    created_at=str(fake.date_time_between(start_date="-1y")),
                    community=-1,
                )
                bot_ids.append(bot_id)

            # Wire compromised <-> bots
            for bot_id in bot_ids:
                G.add_edge(comp_node, bot_id, relationship="TRUSTS")
                G.add_edge(bot_id, comp_node, relationship="TRUSTS")

            # Dense intra-cluster bot trust (~50% pairwise)
            for i, b1 in enumerate(bot_ids):
                for b2 in bot_ids[i + 1:]:
                    if rng.random() < 0.5:
                        G.add_edge(b1, b2, relationship="TRUSTS")
                    if rng.random() < 0.5:
                        G.add_edge(b2, b1, relationship="TRUSTS")

            # Concentrated distrust: nearby users flag the compromised account
            comp_comm = G.nodes[comp_node].get("community", -1)
            if comp_comm >= 0 and comp_comm in communities_map:
                comm_members = [
                    n for n in communities_map[comp_comm]
                    if n != comp_node and not G.nodes[n].get("is_compromised", False)
                ]
                num_distrusters = max(1, len(comm_members) // 10)
                num_distrusters = min(num_distrusters, len(comm_members))
                distrusters = rng.sample(comm_members, num_distrusters)
                for d_node in distrusters:
                    if not G.has_edge(d_node, comp_node) or \
                       G.edges[d_node, comp_node].get("relationship") != "DISTRUSTS":
                        G.add_edge(d_node, comp_node, relationship="DISTRUSTS")

            cluster_map[comp_node] = bot_ids

        return cluster_map

    @staticmethod
    def _rewire_small_world(
        G: nx.DiGraph,
        rewire_prob: float,
        rng: random.Random,
    ) -> None:
        """Rewire edges for small-world shortcuts.

        For a fraction of directed edges, replace the target with a random
        node to create long-range connections. This reduces average path
        length while preserving clustering structure.

        Args:
            G: Directed graph to modify in-place.
            rewire_prob: Probability of rewiring each edge.
            rng: Random instance for reproducibility.
        """
        nodes = list(G.nodes())
        edges_to_rewire = [
            (u, v) for u, v, d in G.edges(data=True)
            if d.get("relationship") == "TRUSTS" and rng.random() < rewire_prob
        ]

        for u, v in edges_to_rewire:
            new_target = rng.choice(nodes)
            if new_target != u and not G.has_edge(u, new_target):
                G.remove_edge(u, v)
                G.add_edge(u, new_target, relationship="TRUSTS")

    @staticmethod
    def _fast_sbm(
        sizes: list,
        p_matrix: list,
        seed: Optional[int] = None,
    ) -> nx.Graph:
        """Generate an undirected SBM graph using vectorized numpy sampling.

        For each block (i, j) in the probability matrix:
        1. Draw edge count from Binomial(n_possible_pairs, p)
        2. Sample that many unique random pairs via np.random.choice
        3. Assemble into scipy COO sparse matrix -> NetworkX Graph

        Complexity: O(E + B^2) where E = edges, B = number of blocks.
        """
        from scipy.sparse import coo_matrix

        rng = np.random.default_rng(seed)
        n = sum(sizes)
        num_blocks = len(sizes)
        offsets = [0]
        for s in sizes:
            offsets.append(offsets[-1] + s)

        all_rows: list = []
        all_cols: list = []

        for i in range(num_blocks):
            for j in range(i, num_blocks):  # upper triangle + diagonal
                ni, nj = sizes[i], sizes[j]
                p = p_matrix[i][j]
                if p <= 0:
                    continue

                if i == j:
                    # Within-block: sample from upper triangle (no self-loops)
                    n_possible = ni * (ni - 1) // 2
                    if n_possible == 0:
                        continue
                    n_edges = int(rng.binomial(n_possible, p))
                    if n_edges == 0:
                        continue

                    # Sample flat indices into upper triangle
                    flat = rng.choice(n_possible, size=n_edges, replace=False)

                    # Convert flat upper-triangle index to (row, col)
                    row = (
                        ni - 2
                        - np.floor(
                            np.sqrt(-8.0 * flat + 4.0 * ni * (ni - 1) - 7.0)
                            / 2.0
                            - 0.5
                        ).astype(np.intp)
                    )
                    col = (
                        flat
                        + row
                        + 1
                        - ni * (ni - 1) // 2
                        + ((ni - row) * (ni - row - 1)) // 2
                    )

                    row = row + offsets[i]
                    col = col + offsets[i]
                else:
                    # Between-block: sample from full ni x nj grid
                    n_possible = ni * nj
                    n_edges = int(rng.binomial(n_possible, p))
                    if n_edges == 0:
                        continue

                    flat = rng.choice(n_possible, size=n_edges, replace=False)
                    row = flat // nj + offsets[i]
                    col = flat % nj + offsets[j]

                all_rows.append(row)
                all_cols.append(col)

        if all_rows:
            rows = np.concatenate(all_rows)
            cols = np.concatenate(all_cols)
        else:
            rows = np.array([], dtype=np.intp)
            cols = np.array([], dtype=np.intp)

        # Build symmetric adjacency (undirected)
        data = np.ones(len(rows), dtype=np.int8)
        adj = coo_matrix((data, (rows, cols)), shape=(n, n))
        adj = adj + adj.T  # symmetrize

        G = nx.from_scipy_sparse_array(adj, create_using=nx.Graph())
        return G

    @staticmethod
    def build_graph(
        total_users: int = 10000,
        avg_trust_links: int = 15,
        reciprocity: float = 0.7,
        num_communities: Optional[int] = None,
        community_mixing: float = 0.15,
        avg_distrust_links: float = 2.0,
        bot_fraction: float = 0.10,
        rewire_prob: float = 0.05,
        seed: Optional[int] = None,
    ) -> nx.DiGraph:
        """Generate a realistic directed social trust graph.

        Pipeline:
        1. Build community structure via Stochastic Block Model
        2. Preferential attachment rewiring for hub emergence
        3. Convert to directed edges with configurable reciprocity
        4. Small-world rewiring for realistic path lengths
        5. Relabel nodes to user_N and attach Faker-generated attributes
        6. Add organic Poisson-distributed distrust links
        7. Add bot clusters around compromised accounts

        Args:
            total_users: Number of user nodes.
            avg_trust_links: Target average outgoing trust edges per user.
            reciprocity: Fraction of edges that are mutual (0.0 to 1.0).
            num_communities: Number of community clusters. None = auto (~500 users per community).
            community_mixing: Fraction of edges crossing community boundaries (0.0 to 1.0).
            avg_distrust_links: Average DISTRUSTS edges per user (Poisson lambda).
            bot_fraction: Fraction of nodes that are bots (0.0 to 1.0). Default 0.10 (10%).
            rewire_prob: Probability of rewiring each edge for small-world shortcuts.
            seed: Random seed for reproducibility.

        Returns:
            nx.DiGraph with User nodes and TRUSTS/DISTRUSTS edges.
        """
        rng = random.Random(seed)
        np_rng = np.random.default_rng(seed)

        logger.info(
            f"Generating trust graph: {total_users} users, "
            f"~{avg_trust_links} avg links, "
            f"reciprocity={reciprocity}, "
            f"communities={'auto' if num_communities is None else num_communities}"
        )

        # Step 1: Community structure via SBM
        sizes = TrustGraphFetcher._build_community_sizes(
            total_users, num_communities, seed=seed
        )
        actual_num_communities = len(sizes)

        p_matrix = TrustGraphFetcher._build_probability_matrix(
            sizes, avg_trust_links, community_mixing, total_users
        )

        G_undirected = TrustGraphFetcher._fast_sbm(sizes, p_matrix, seed=seed)

        logger.info(
            f"SBM generated: {G_undirected.number_of_nodes()} nodes, "
            f"{G_undirected.number_of_edges()} undirected edges, "
            f"{actual_num_communities} communities"
        )

        # Build community label mapping from SBM partition
        community_labels = {}
        node_idx = 0
        for comm_id, size in enumerate(sizes):
            for _ in range(size):
                community_labels[node_idx] = comm_id
                node_idx += 1

        # Step 1b: Preferential attachment for hub emergence
        TrustGraphFetcher._apply_preferential_attachment(
            G_undirected, community_labels, pa_fraction=0.4, rng=rng
        )

        # Step 2: Convert to directed with reciprocity
        G = TrustGraphFetcher._apply_reciprocity(G_undirected, reciprocity, rng)
        del G_undirected  # Free undirected graph early

        logger.info(
            f"Directed graph: {G.number_of_edges()} edges "
            f"(reciprocity={reciprocity})"
        )

        # Step 3: Small-world rewiring (TRUSTS only)
        if rewire_prob > 0:
            TrustGraphFetcher._rewire_small_world(G, rewire_prob, rng)

        # Step 4: Relabel integer nodes to user_N and add attributes
        mapping = {i: f"user_{i}" for i in range(total_users)}
        G = nx.relabel_nodes(G, mapping, copy=False)

        if seed is not None:
            Faker.seed(seed)
        TrustGraphFetcher.generate_users(G, total_users, community_labels)

        # Step 5: Add organic distrust
        if avg_distrust_links > 0:
            TrustGraphFetcher._add_organic_distrust(G, avg_distrust_links, rng, np_rng)
            distrust_count = sum(
                1 for _, _, d in G.edges(data=True)
                if d.get("relationship") == "DISTRUSTS"
            )
            logger.info(f"Added {distrust_count} organic distrust links")

        # Step 6: Add bot clusters (derive structure from bot_fraction)
        total_bots = round(total_users * bot_fraction)
        if total_bots > 0:
            # Target ~15 bots per cluster, scale compromised accounts accordingly
            bpc = min(15, total_bots)
            num_compromised = max(1, total_bots // bpc)
            bpc = total_bots // num_compromised
            remainder = total_bots - (num_compromised * bpc)
            # Distribute bots across clusters, spreading remainder
            bots_per_cluster = [bpc] * num_compromised
            for i in range(remainder):
                bots_per_cluster[i] += 1

            cluster_map = TrustGraphFetcher._add_bot_clusters(
                G, num_compromised, bots_per_cluster, rng, np_rng
            )
            actual_bots = sum(len(v) for v in cluster_map.values())
            logger.info(
                f"Added {len(cluster_map)} bot clusters "
                f"({actual_bots} bot nodes total, bot_fraction={bot_fraction})"
            )

        logger.info(
            f"Trust graph complete: {G.number_of_nodes()} nodes, "
            f"{G.number_of_edges()} edges"
        )

        return G
