"""Graphfaker module with synthetic graph generation, visualization, and export capabilities."""
import networkx as nx
import random
from faker import Faker
import matplotlib.pyplot as plt

fake = Faker()

class GraphFaker:
    @staticmethod
    def generate_people(num=10):
        """Generate synthetic people nodes."""
        G = nx.Graph()
        for i in range(num):
            pid = f"person_{i}"
            G.add_node(
                pid,
                type="Person",
                name=fake.name(),
                email=fake.email(),
                age=random.randint(18, 80)
            )
        return G

    @staticmethod
    def generate_places(num=5):
        """Generate synthetic place nodes."""
        G = nx.Graph()
        for i in range(num):
            cid = f"place_{i}"
            G.add_node(
                cid,
                type="Place",
                name=fake.city(),
                country=fake.country()
            )
        return G

    @staticmethod
    def generate_organizations(num=5):
        """Generate synthetic organization nodes."""
        G = nx.Graph()
        for i in range(num):
            oid = f"org_{i}"
            G.add_node(
                oid,
                type="Organization",
                name=fake.company(),
                industry=fake.job()
            )
        return G

    @staticmethod
    def generate_events(num=3):
        """Generate synthetic event nodes."""
        G = nx.Graph()
        for i in range(num):
            eid = f"event_{i}"
            G.add_node(
                eid,
                type="Event",
                name=fake.catch_phrase(),
                date=fake.date()
            )
        return G

    @staticmethod
    def generate_products(num=3):
        """Generate synthetic product nodes."""
        G = nx.Graph()
        for i in range(num):
            pid = f"product_{i}"
            G.add_node(
                pid,
                type="Product",
                name=fake.word(),
                category=fake.word()
            )
        return G

    @staticmethod
    def connect_people_to_organizations(G, people_nodes, org_nodes):
        """Connect people to organizations with a random relationship."""
        for p in people_nodes:
            org = random.choice(org_nodes)
            G.add_edge(p, org, relationship=random.choice(["works_at", "consults_for", "owns"]))

    @staticmethod
    def connect_people_to_places(G, people_nodes, place_nodes):
        """Connect people to places with a random relationship."""
        for p in people_nodes:
            place = random.choice(place_nodes)
            G.add_edge(p, place, relationship=random.choice(["lives_in", "born_in"]))

    @staticmethod
    def connect_people_to_events(G, people_nodes, event_nodes):
        """Connect people to events with an 'attends' relationship."""
        for p in people_nodes:
            event = random.choice(event_nodes)
            G.add_edge(p, event, relationship="attends")

    @staticmethod
    def connect_people_to_products(G, people_nodes, product_nodes):
        """Connect people to products with a 'buys' relationship."""
        for p in people_nodes:
            product = random.choice(product_nodes)
            G.add_edge(p, product, relationship="buys")

    @staticmethod
    def connect_orgs_to_places(G, org_nodes, place_nodes):
        """Connect organizations to places with a 'headquartered_in' relationship."""
        for org in org_nodes:
            place = random.choice(place_nodes)
            G.add_edge(org, place, relationship="headquartered_in")

    @staticmethod
    def generate_graph(total_nodes=100, total_edges=500):
        """
        Generate a synthetic graph with a specified total number of nodes and edges.
        Default: 100 nodes and ~500 edges.
        The nodes are split into types:
          - 50% People
          - 20% Places
          - 15% Organizations
          - 10% Events
          - Remaining as Products
        """
        # Determine numbers per node type
        num_people = int(total_nodes * 0.5)
        num_places = int(total_nodes * 0.2)
        num_orgs = int(total_nodes * 0.15)
        num_events = int(total_nodes * 0.1)
        num_products = total_nodes - (num_people + num_places + num_orgs + num_events)

        # Generate subgraphs for each node type
        G_people = GraphFaker.generate_people(num_people)
        G_places = GraphFaker.generate_places(num_places)
        G_orgs = GraphFaker.generate_organizations(num_orgs)
        G_events = GraphFaker.generate_events(num_events)
        G_products = GraphFaker.generate_products(num_products)

        # Combine all nodes into one main graph
        G = nx.Graph()
        G.add_nodes_from(G_people.nodes(data=True))
        G.add_nodes_from(G_places.nodes(data=True))
        G.add_nodes_from(G_orgs.nodes(data=True))
        G.add_nodes_from(G_events.nodes(data=True))
        G.add_nodes_from(G_products.nodes(data=True))

        # Get lists of nodes for each type
        people_nodes = list(G_people.nodes())
        place_nodes = list(G_places.nodes())
        org_nodes = list(G_orgs.nodes())
        event_nodes = list(G_events.nodes())
        product_nodes = list(G_products.nodes())

        # Create predefined relationships between nodes
        GraphFaker.connect_people_to_organizations(G, people_nodes, org_nodes)
        GraphFaker.connect_people_to_places(G, people_nodes, place_nodes)
        GraphFaker.connect_people_to_events(G, people_nodes, event_nodes)
        GraphFaker.connect_people_to_products(G, people_nodes, product_nodes)
        GraphFaker.connect_orgs_to_places(G, org_nodes, place_nodes)

        # Add random edges until we reach the desired total_edges
        current_edges = G.number_of_edges()
        all_nodes = list(G.nodes())
        possible_rels = ["works_at", "consults_for", "owns", "lives_in", "born_in", "attends", "buys", "headquartered_in"]
        while current_edges < total_edges:
            u, v = random.sample(all_nodes, 2)
            if not G.has_edge(u, v):
                rel = random.choice(possible_rels)
                G.add_edge(u, v, relationship=rel)
                current_edges += 1

        return G

    @staticmethod
    def visualize_graph(G, title="GraphFaker Synthetic Graph"):
        """Visualize the graph using Matplotlib with a more spread-out layout."""
        plt.figure(figsize=(12, 10))
        # Increase 'k' and iterations for a better spread
        pos = nx.spring_layout(G, seed=42, k=1.5, iterations=100)
        node_colors = []
        for node, data in G.nodes(data=True):
            if data.get("type") == "Person":
                node_colors.append("lightblue")
            elif data.get("type") == "Place":
                node_colors.append("lightgreen")
            elif data.get("type") == "Organization":
                node_colors.append("orange")
            elif data.get("type") == "Event":
                node_colors.append("pink")
            elif data.get("type") == "Product":
                node_colors.append("yellow")
            else:
                node_colors.append("gray")

        nx.draw_networkx_nodes(G, pos, node_color=node_colors, node_size=500, alpha=0.8)
        nx.draw_networkx_edges(G, pos, alpha=0.5)
        # Create labels: display the 'name' attribute if available, else the node ID.
        labels = {node: data.get("name", node) for node, data in G.nodes(data=True)}
        nx.draw_networkx_labels(G, pos, labels=labels, font_size=8)
        plt.title(title)
        plt.axis("off")
        plt.show()

    @staticmethod
    def export_graph(G, filename="graph_output.graphml"):
        """Export the graph to GraphML format."""
        nx.write_graphml(G, filename)
        print(f"Graph exported to {filename}")


if __name__ == "__main__":
    # Interactive: if desired, these values could be passed via CLI arguments or user input.
    # For now, default values are used.
    G = GraphFaker.generate_graph(total_nodes=100, total_edges=500)
    GraphFaker.visualize_graph(G, title="graphfaker Synthetic Graph (POC)")
    GraphFaker.export_graph(G, filename="graphfaker_output.graphml")

    print("Total nodes:", G.number_of_nodes())
    print("Total edges:", G.number_of_edges())
