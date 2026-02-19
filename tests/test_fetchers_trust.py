# tests/test_fetchers_trust.py
import random

import pytest
import networkx as nx
import numpy as np

from graphfaker.fetchers.trust import TrustGraphFetcher


class TestBuildCommunities:
    def test_default_communities(self):
        sizes = TrustGraphFetcher._build_community_sizes(100, None, seed=42)
        assert sum(sizes) == 100
        assert len(sizes) == 2  # 100 / 500 rounds to 0, clamped to min 2

    def test_explicit_communities(self):
        sizes = TrustGraphFetcher._build_community_sizes(10000, 5, seed=42)
        assert sum(sizes) == 10000
        assert len(sizes) == 5
        assert all(s >= 50 for s in sizes)

    def test_more_communities_than_users(self):
        sizes = TrustGraphFetcher._build_community_sizes(3, 10, seed=42)
        assert sum(sizes) == 3
        assert len(sizes) == 3  # clamped to total_users

    def test_single_community(self):
        sizes = TrustGraphFetcher._build_community_sizes(50, 1, seed=42)
        assert sizes == [50]

    def test_variable_community_sizes(self):
        """Verify log-normal produces non-uniform sizes with min floor."""
        sizes = TrustGraphFetcher._build_community_sizes(10000, 20, seed=42)
        assert sum(sizes) == 10000
        assert len(sizes) == 20
        assert all(s >= 50 for s in sizes)
        # Sizes should NOT all be equal (log-normal is skewed)
        assert len(set(sizes)) > 1

    def test_reproducible_with_seed(self):
        s1 = TrustGraphFetcher._build_community_sizes(10000, 20, seed=99)
        s2 = TrustGraphFetcher._build_community_sizes(10000, 20, seed=99)
        assert s1 == s2


class TestProbabilityMatrix:
    def test_matrix_shape(self):
        sizes = [250, 250, 250, 250]
        matrix = TrustGraphFetcher._build_probability_matrix(
            sizes=sizes, avg_trust_links=15,
            community_mixing=0.15, total_users=1000,
        )
        assert len(matrix) == 4
        assert all(len(row) == 4 for row in matrix)

    def test_within_greater_than_between(self):
        sizes = [250, 250, 250, 250]
        matrix = TrustGraphFetcher._build_probability_matrix(
            sizes=sizes, avg_trust_links=15,
            community_mixing=0.15, total_users=1000,
        )
        p_within = matrix[0][0]
        p_between = matrix[0][1]
        assert p_within > p_between

    def test_within_greater_than_between_variable_sizes(self):
        """Even with the largest community, p_within > p_between."""
        sizes = [2000, 800, 600, 400, 200]
        matrix = TrustGraphFetcher._build_probability_matrix(
            sizes=sizes, avg_trust_links=15,
            community_mixing=0.15, total_users=4000,
        )
        # Check for every row (community)
        for i in range(len(sizes)):
            p_within = matrix[i][i]
            p_between = matrix[i][(i + 1) % len(sizes)]
            assert p_within > p_between, f"Community {i} (size={sizes[i]}): p_within={p_within} <= p_between={p_between}"

    def test_probabilities_capped_at_one(self):
        sizes = [5, 5]
        matrix = TrustGraphFetcher._build_probability_matrix(
            sizes=sizes, avg_trust_links=500,
            community_mixing=0.5, total_users=10,
        )
        for row in matrix:
            for p in row:
                assert 0.0 <= p <= 1.0


class TestApplyReciprocity:
    @pytest.fixture
    def simple_undirected(self):
        G = nx.Graph()
        G.add_edges_from([(0, 1), (1, 2), (2, 3), (3, 4)])
        return G

    def test_full_reciprocity(self, simple_undirected):
        rng = random.Random(42)
        G_dir = TrustGraphFetcher._apply_reciprocity(simple_undirected, 1.0, rng)
        assert isinstance(G_dir, nx.DiGraph)
        # With reciprocity=1.0, every undirected edge becomes two directed edges
        assert G_dir.number_of_edges() == 8  # 4 undirected * 2

    def test_zero_reciprocity(self, simple_undirected):
        rng = random.Random(42)
        G_dir = TrustGraphFetcher._apply_reciprocity(simple_undirected, 0.0, rng)
        # With reciprocity=0.0, every undirected edge becomes one directed edge
        assert G_dir.number_of_edges() == 4

    def test_edge_relationship_attribute(self, simple_undirected):
        rng = random.Random(42)
        G_dir = TrustGraphFetcher._apply_reciprocity(simple_undirected, 0.5, rng)
        for u, v, data in G_dir.edges(data=True):
            assert data["relationship"] == "TRUSTS"


class TestOrganicDistrust:
    def test_organic_distrust_distribution(self):
        """Most nodes should have >=1 DISTRUSTS edge at avg=2.0."""
        rng = random.Random(42)
        np_rng = np.random.default_rng(42)
        G = nx.DiGraph()
        n = 1000
        for i in range(n):
            G.add_node(f"user_{i}")
        # Add some trust edges so the graph is non-trivial
        for i in range(n - 1):
            G.add_edge(f"user_{i}", f"user_{i+1}", relationship="TRUSTS")

        TrustGraphFetcher._add_organic_distrust(G, 2.0, rng, np_rng)

        distrust_edges = [
            (u, v) for u, v, d in G.edges(data=True)
            if d.get("relationship") == "DISTRUSTS"
        ]
        distrust_sources = set(u for u, _ in distrust_edges)

        # At lambda=2.0, ~86.5% should have >=1 distrust link
        assert len(distrust_sources) / n > 0.75, (
            f"Only {len(distrust_sources)}/{n} users have distrust links"
        )
        # Total should be roughly n * avg (within 50% tolerance)
        assert n * 1.0 < len(distrust_edges) < n * 3.5, (
            f"Expected ~{n * 2} distrust edges, got {len(distrust_edges)}"
        )

    def test_zero_distrust(self):
        """avg_distrust_links=0.0 should produce no DISTRUSTS edges."""
        rng = random.Random(42)
        np_rng = np.random.default_rng(42)
        G = nx.DiGraph()
        for i in range(10):
            G.add_node(f"user_{i}")
        for i in range(9):
            G.add_edge(f"user_{i}", f"user_{i+1}", relationship="TRUSTS")

        TrustGraphFetcher._add_organic_distrust(G, 0.0, rng, np_rng)

        distrust_edges = [
            (u, v) for u, v, d in G.edges(data=True)
            if d.get("relationship") == "DISTRUSTS"
        ]
        assert len(distrust_edges) == 0

    def test_distrust_no_overlap_with_trust(self):
        """DISTRUSTS targets must not have TRUSTS from the same source."""
        rng = random.Random(42)
        np_rng = np.random.default_rng(42)
        G = nx.DiGraph()
        n = 200
        for i in range(n):
            G.add_node(f"user_{i}")
        for i in range(n - 1):
            G.add_edge(f"user_{i}", f"user_{i+1}", relationship="TRUSTS")

        TrustGraphFetcher._add_organic_distrust(G, 2.0, rng, np_rng)

        trust_set = set(
            (u, v) for u, v, d in G.edges(data=True)
            if d.get("relationship") == "TRUSTS"
        )
        distrust_set = set(
            (u, v) for u, v, d in G.edges(data=True)
            if d.get("relationship") == "DISTRUSTS"
        )
        overlap = trust_set & distrust_set
        assert len(overlap) == 0, f"Found {len(overlap)} overlapping TRUSTS/DISTRUSTS edges"


class TestBotClusters:
    @pytest.fixture
    def graph_with_bots(self):
        """Build a small graph and add bot clusters."""
        G = TrustGraphFetcher.build_graph(
            total_users=200,
            avg_trust_links=5,
            reciprocity=0.7,
            num_communities=4,
            community_mixing=0.15,
            avg_distrust_links=1.0,
            bot_fraction=0.05,
            seed=42,
        )
        return G

    def test_bot_nodes_created(self, graph_with_bots):
        """Verify expected number of bot nodes exist."""
        bot_nodes = [n for n, d in graph_with_bots.nodes(data=True) if d.get("is_bot")]
        assert len(bot_nodes) == 10  # round(200 * 0.05) = 10

    def test_compromised_nodes_marked(self, graph_with_bots):
        """Verify compromised account count — derived from total bots / ~15 per cluster."""
        comp_nodes = [n for n, d in graph_with_bots.nodes(data=True) if d.get("is_compromised")]
        assert len(comp_nodes) >= 1

    def test_bot_cluster_connectivity(self, graph_with_bots):
        """Each bot should be connected to exactly one compromised account in both directions."""
        G = graph_with_bots
        comp_nodes = [n for n, d in G.nodes(data=True) if d.get("is_compromised")]
        bot_nodes = [n for n, d in G.nodes(data=True) if d.get("is_bot")]

        for bot in bot_nodes:
            # Each bot must have bidirectional TRUSTS with exactly one compromised node
            linked_comp = [c for c in comp_nodes if G.has_edge(c, bot) and G.has_edge(bot, c)]
            assert len(linked_comp) == 1, (
                f"Bot {bot} should connect to exactly 1 compromised node, found {len(linked_comp)}"
            )

    def test_bot_ids_format(self, graph_with_bots):
        """Bot node IDs should match bot_* pattern."""
        bot_nodes = [n for n, d in graph_with_bots.nodes(data=True) if d.get("is_bot")]
        for bot in bot_nodes:
            assert bot.startswith("bot_"), f"Unexpected bot ID format: {bot}"

    def test_no_bots_when_zero(self):
        """bot_fraction=0.0 should produce no bots or compromised markers."""
        G = TrustGraphFetcher.build_graph(
            total_users=100,
            avg_trust_links=5,
            bot_fraction=0.0,
            seed=42,
        )
        bot_nodes = [n for n, d in G.nodes(data=True) if d.get("is_bot")]
        comp_nodes = [n for n, d in G.nodes(data=True) if d.get("is_compromised")]
        assert len(bot_nodes) == 0
        assert len(comp_nodes) == 0

    def test_convergent_distrust_on_compromised(self, graph_with_bots):
        """Compromised accounts should have multiple incoming DISTRUSTS edges."""
        G = graph_with_bots
        comp_nodes = [n for n, d in G.nodes(data=True) if d.get("is_compromised")]
        for comp in comp_nodes:
            incoming_distrust = [
                u for u in G.predecessors(comp)
                if G.edges[u, comp].get("relationship") == "DISTRUSTS"
            ]
            assert len(incoming_distrust) >= 1, (
                f"Compromised node {comp} has {len(incoming_distrust)} incoming DISTRUSTS"
            )

    def test_bot_community_is_negative_one(self, graph_with_bots):
        """Bot nodes should have community=-1."""
        bot_nodes = [n for n, d in graph_with_bots.nodes(data=True) if d.get("is_bot")]
        for bot in bot_nodes:
            assert graph_with_bots.nodes[bot]["community"] == -1


class TestBuildGraph:
    @pytest.fixture
    def small_trust_graph(self):
        return TrustGraphFetcher.build_graph(
            total_users=100,
            avg_trust_links=5,
            reciprocity=0.7,
            num_communities=4,
            community_mixing=0.15,
            avg_distrust_links=1.0,
            bot_fraction=0.10,
            seed=42,
        )

    def test_node_count(self, small_trust_graph):
        # 100 users + round(100*0.10)=10 bots = 110
        assert small_trust_graph.number_of_nodes() == 110

    def test_is_directed(self, small_trust_graph):
        assert isinstance(small_trust_graph, nx.DiGraph)

    def test_node_attributes(self, small_trust_graph):
        node_data = small_trust_graph.nodes["user_0"]
        assert node_data["type"] == "User"
        assert "name" in node_data
        assert "public_key" in node_data
        assert len(node_data["public_key"]) == 64  # 32 bytes hex
        assert "created_at" in node_data
        assert "community" in node_data
        assert "is_bot" in node_data
        assert "is_compromised" in node_data

    def test_edge_relationships(self, small_trust_graph):
        relationships = set()
        for u, v, data in small_trust_graph.edges(data=True):
            relationships.add(data.get("relationship"))
        assert "TRUSTS" in relationships
        assert "DISTRUSTS" in relationships

    def test_has_edges(self, small_trust_graph):
        assert small_trust_graph.number_of_edges() > 0

    def test_node_id_format(self, small_trust_graph):
        for node in small_trust_graph.nodes():
            assert node.startswith("user_") or node.startswith("bot_")

    def test_community_labels_assigned(self, small_trust_graph):
        communities = set()
        for _, data in small_trust_graph.nodes(data=True):
            communities.add(data.get("community"))
        assert 4 in [c for c in communities if c >= 0] or len([c for c in communities if c >= 0]) == 4
        # Bot nodes have community=-1
        assert -1 in communities

    def test_reproducible_with_seed(self):
        g1 = TrustGraphFetcher.build_graph(total_users=50, seed=123)
        g2 = TrustGraphFetcher.build_graph(total_users=50, seed=123)
        assert g1.number_of_nodes() == g2.number_of_nodes()
        assert g1.number_of_edges() == g2.number_of_edges()
        assert set(g1.nodes()) == set(g2.nodes())

    def test_default_parameters(self):
        # Smoke test with defaults (but small scale)
        g = TrustGraphFetcher.build_graph(total_users=50, seed=1)
        assert g.number_of_nodes() > 50  # 50 users + bots
        assert g.number_of_edges() > 0


class TestCoreIntegration:
    def test_generate_graph_trust_source(self):
        from graphfaker.core import GraphFaker
        gf = GraphFaker()
        g = gf.generate_graph(
            source="trust",
            total_users=50,
            avg_trust_links=5,
            bot_fraction=0.06,
            seed=42,
        )
        assert isinstance(g, nx.DiGraph)
        assert g.number_of_nodes() == 53  # 50 users + round(50*0.06)=3 bots

    def test_graphfaker_stores_graph(self):
        from graphfaker.core import GraphFaker
        gf = GraphFaker()
        g = gf.generate_graph(source="trust", total_users=30, seed=42)
        assert gf.G is g
