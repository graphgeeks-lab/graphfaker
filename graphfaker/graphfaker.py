"""Graphfaker module with visualization and export capabilities."""
import networkx as nx
import random
from faker import Faker
import matplotlib.pyplot as plt

fake = Faker()

class Graphfaker:
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
        """Connect people to organizations."""
        for p in people_nodes:
            org = random.choice(org_nodes)
            G.add_edge(p, org, relationship=random.choice(["works_at", "consults_for", "owns"]))

    @staticmethod
    def connect_people_to_places(G, people_nodes, place_nodes):
        """Connect people to places."""
        for p in people_nodes:
            place = random.choice(place_nodes)
            G.add_edge(p, place, relationship=random.choice(["lives_in", "born_in"]))

    @staticmethod
    def connect_people_to_events(G, people_nodes, event_nodes):
        """Connect people to events."""
        for p in people_nodes:
            event = random.choice(event_nodes)
            G.add_edge(p, event, relationship="attends")

    @staticmethod
    def connect_people_to_products(G, people_nodes, product_nodes):
        """Connect people to products."""
        for p in people_nodes:
            product = random.choice(product_nodes)
            G.add_edge(p, product, relationship="buys")

    @staticmethod
    def connect_orgs_to_places(G, org_nodes, place_nodes):
        """Connect organizations to places."""
        for org in org_nodes:
            place = random.choice(place_nodes)
            G.add_edge(org, place, relationship="headquartered_in")

    @staticmethod
    def visualize_graph(G, title="Graphfaker Synthetic Graph"):
        """Visualize the graph using Matplotlib."""
        plt.figure(figsize=(12, 10))
        pos = nx.spring_layout(G, seed=42, k=1.5, iterations=100)
        node_colors = []
        # Color nodes based on their type
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
        # Create labels: if a node has a 'name' attribute, use that; otherwise, use the node id.
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
    # Generate synthetic subgraphs
    G_people = Graphfaker.generate_people(10)
    G_places = Graphfaker.generate_places(5)
    G_orgs = Graphfaker.generate_organizations(5)
    G_events = Graphfaker.generate_events(3)
    G_products = Graphfaker.generate_products(3)

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

    # Create relationships between nodes
    Graphfaker.connect_people_to_organizations(G, people_nodes, org_nodes)
    Graphfaker.connect_people_to_places(G, people_nodes, place_nodes)
    Graphfaker.connect_people_to_events(G, people_nodes, event_nodes)
    Graphfaker.connect_people_to_products(G, people_nodes, product_nodes)
    Graphfaker.connect_orgs_to_places(G, org_nodes, place_nodes)

    # Visualize the graph
    Graphfaker.visualize_graph(G, title="Graphfaker Synthetic Graph (POC)")

    # Optionally, export the graph
    Graphfaker.export_graph(G, filename="graphfaker_output.graphml")

    # Print summary information
    print("Total nodes:", G.number_of_nodes())
    print("Total edges:", G.number_of_edges())
