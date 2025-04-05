"""Graphfaker module with visualization and export capabilities."""
"""Graphfaker module for generating synthetic knowledge graphs with realistic relationships."""
import networkx as nx
import random
from faker import Faker
from collections import defaultdict
import matplotlib.pyplot as plt

fake = Faker()


class GraphFaker:
    @staticmethod
    def generate_nodes(node_type, num):
        """Generate nodes of specified type with appropriate attributes."""
        nodes = []
        for i in range(num):
            node_id = f"{node_type.lower()}_{i}"
            data = {'type': node_type}

            if node_type == 'Person':
                data.update({
                    'name': fake.name(),
                    'age': random.randint(18, 80),
                    'email': fake.email(),
                    'occupation': fake.job(),
                    'education': random.choice(['High School', 'Bachelor', 'Master', 'PhD']),
                    'skills': [fake.job() for _ in range(random.randint(1, 3))],
                    'subtype': random.choice(['Student', 'Professional', 'Retiree', 'Unemployed'])
                })
            elif node_type == 'Place':
                data.update({
                    'name': fake.city(),
                    'subtype': random.choice(['City', 'Park', 'Restaurant', 'Airport', 'University']),
                    'population': random.randint(1000, 1000000),
                    'coordinates': (fake.latitude(), fake.longitude())
                })
            elif node_type == 'Organization':
                data.update({
                    'name': fake.company(),
                    'subtype': random.choice(['TechCompany', 'Hospital', 'NGO', 'University', 'RetailChain']),
                    'industry': fake.bs(),
                    'employees': random.randint(10, 10000),
                    'founded': fake.year()
                })
            elif node_type == 'Event':
                data.update({
                    'name': fake.catch_phrase(),
                    'subtype': random.choice(['Concert', 'Conference', 'Protest', 'SportsGame']),
                    'start_date': fake.date_this_decade(),
                    'duration': random.randint(1, 7)
                })
            elif node_type == 'Product':
                data.update({
                    'name': fake.word(),
                    'subtype': random.choice(['Electronics', 'Apparel', 'Book', 'Vehicle']),
                    'price': round(random.uniform(10, 1000), 2),
                    'launched': fake.date_this_decade()
                })
            nodes.append((node_id, data))
        return nodes

    @staticmethod
    def generate_graph(total_nodes=100, total_edges=500):
        """Generate a directed graph with realistic relationships and properties."""
        G = nx.DiGraph()

        # Node distribution
        node_counts = {
            'Person': int(total_nodes * 0.5),
            'Place': int(total_nodes * 0.2),
            'Organization': int(total_nodes * 0.15),
            'Event': int(total_nodes * 0.1),
            'Product': total_nodes - sum([int(total_nodes * p) for p in [0.5, 0.2, 0.15, 0.1]])
        }

        # Generate and add nodes
        for node_type, count in node_counts.items():
            if count > 0:
                nodes = GraphFaker.generate_nodes(node_type, count)
                G.add_nodes_from(nodes)

        # Build node type index
        type_index = defaultdict(list)
        for node, data in G.nodes(data=True):
            type_index[data['type']].append(node)

        # Relationship configuration
        edge_categories = [
            {
                'source': 'Person',
                'target': 'Person',
                'probability': 0.4,
                'relationships': [
                    {'type': 'FRIENDS_WITH', 'weight': 0.7},
                    {'type': 'COLLEAGUES', 'weight': 0.2},
                    {'type': 'MENTORS', 'weight': 0.1}
                ]
            },
            {
                'source': 'Person',
                'target': 'Place',
                'probability': 0.2,
                'relationships': [
                    {'type': 'LIVES_IN', 'weight': 0.5},
                    {'type': 'VISITED', 'weight': 0.3},
                    {'type': 'BORN_IN', 'weight': 0.2}
                ]
            },
            {
                'source': 'Person',
                'target': 'Organization',
                'probability': 0.15,
                'relationships': [
                    {'type': 'WORKS_AT', 'weight': 0.8},
                    {'type': 'STUDIED_AT', 'weight': 0.15},
                    {'type': 'OWNS', 'weight': 0.05}
                ]
            },
            {
                'source': 'Organization',
                'target': 'Place',
                'probability': 0.1,
                'relationships': [
                    {'type': 'HEADQUARTERED_IN', 'weight': 0.6},
                    {'type': 'HAS_BRANCH', 'weight': 0.4}
                ]
            },
            {
                'source': 'Person',
                'target': 'Event',
                'probability': 0.08,
                'relationships': [
                    {'type': 'ATTENDED', 'weight': 0.9},
                    {'type': 'ORGANIZED', 'weight': 0.1}
                ]
            },
            {
                'source': 'Organization',
                'target': 'Product',
                'probability': 0.05,
                'relationships': [
                    {'type': 'MANUFACTURES', 'weight': 0.7},
                    {'type': 'SELLS', 'weight': 0.3}
                ]
            },
            {
                'source': 'Person',
                'target': 'Product',
                'probability': 0.02,
                'relationships': [
                    {'type': 'PURCHASED', 'weight': 0.8},
                    {'type': 'REVIEWED', 'weight': 0.2}
                ]
            }
        ]

        # Relationship properties
        property_generators = {
            'VISITED': lambda: {
                'first_visit': fake.date_this_year(),
                'last_visit': fake.date_this_year(),
                'visits': random.randint(1, 10)
            },
            'WORKS_AT': lambda: {
                'position': fake.job(),
                'start_date': fake.date_this_decade()
            },
            'PURCHASED': lambda: {
                'amount': random.randint(1, 5),
                'date': fake.date_this_year()
            },
            'REVIEWED': lambda: {
                'rating': random.randint(1, 5),
                'review': fake.sentence()
            }
        }

        # Generate edges
        for _ in range(total_edges):
            # Select relationship category
            category = random.choices(
                edge_categories,
                weights=[c['probability'] for c in edge_categories],
                k=1
            )[0]

            # Select specific relationship type
            rel_type = random.choices(
                category['relationships'],
                weights=[r['weight'] for r in category['relationships']],
                k=1
            )[0]['type']

            # Get valid nodes
            source_nodes = type_index.get(category['source'], [])
            target_nodes = type_index.get(category['target'], [])

            if source_nodes and target_nodes:
                source = random.choice(source_nodes)
                target = random.choice(target_nodes)

                # Generate edge properties
                properties = property_generators.get(rel_type, lambda: {})()

                # Add edge with metadata
                G.add_edge(source, target, relationship=rel_type, **properties)

        return G

    @staticmethod
    def visualize_graph(G, title="Social Knowledge Graph"):
        """Visualize the graph with directional indicators."""
        plt.figure(figsize=(15, 12))
        pos = nx.spring_layout(G, k=0.5, iterations=50, seed=42)

        # Node coloring
        type_colors = {
            'Person': 'skyblue',
            'Place': 'lightgreen',
            'Organization': 'salmon',
            'Event': 'violet',
            'Product': 'gold'
        }

        node_colors = [type_colors.get(data['type'], 'gray')
                       for _, data in G.nodes(data=True)]

        # Draw elements
        nx.draw_networkx_nodes(G, pos, node_color=node_colors, node_size=800, alpha=0.9)
        nx.draw_networkx_edges(G, pos, edgelist=G.edges(), width=1.5,
                               arrowstyle='-|>', arrowsize=20, edge_color='gray')

        # Label formatting
        labels = {node: data.get('name', node.split('_')[0])
                  for node, data in G.nodes(data=True)}
        nx.draw_networkx_labels(G, pos, labels, font_size=8, font_family='sans-serif')

        plt.title(title, fontsize=14)
        plt.axis('off')
        plt.tight_layout()
        plt.show()

    @staticmethod
    def export_graph(G, filename="knowledge_graph.graphml"):
        """Export graph with attributes to GraphML format."""
        nx.write_graphml(G, filename)
        print(f"Graph exported to {filename} ({(G.size())} edges)")


if __name__ == "__main__":
    # Example usage
    social_graph = GraphFaker.generate_graph(total_nodes=200, total_edges=1000)
    GraphFaker.visualize_graph(social_graph)
   #GraphFaker.export_graph(social_graph)

    print("Node types distribution:")
    for node_type in ['Person', 'Place', 'Organization', 'Event', 'Product']:
        count = len([n for n, data in social_graph.nodes(data=True)
                     if data.get('type') == node_type])
        print(f"{node_type}: {count} nodes")
