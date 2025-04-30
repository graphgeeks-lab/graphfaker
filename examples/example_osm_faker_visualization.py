# run_demo.py

import matplotlib
matplotlib.use("TkAgg")          # pick a GUI backend on desktop
from graphfaker.core import GraphFaker
import networkx as nx
from matplotlib import pyplot as plt


# visualization function for faker source
def visualize_faker_graph(self, title, k=1.5, iterations=100):
    """Visualize the graph using Matplotlib with a more spread-out layout."""
    plt.figure(figsize=(14, 12))
    pos = nx.spring_layout(self.G, seed=42, k=k, iterations=iterations)
    # Color nodes based on their type
    color_map = {
        "Person": "lightblue",
        "Place": "lightgreen",
        "Organization": "orange",
        "Event": "pink",
        "Product": "yellow"
    }
    node_colors = [color_map.get(data.get("type"), "gray") for _, data in self.G.nodes(data=True)]
    nx.draw_networkx_nodes(self.G, pos, node_color=node_colors, node_size=500, alpha=0.9)
    nx.draw_networkx_edges(self.G, pos, alpha=0.4)
    labels = {node: data.get("name", node) for node, data in self.G.nodes(data=True)}
    nx.draw_networkx_labels(self.G, pos, labels=labels, font_size=8)
    plt.title(title=title)
    plt.axis("off")
    plt.show()



# Visualize osm data.. since it's a multigraph
def visualize_osm(self, G: nx.Graph = None,
                  show_edge_names: bool = False,
                  show_node_ids: bool = False,
                  node_size: int = 20,
                  edge_linewidth: float = 1.0):
    """
    Visualize an OSM-derived graph using OSMnx plotting, with optional labels.
    :param G: The graph to visualize (default: last generated graph).
    :param show_edge_names: If True, overlay edge 'name' attributes as labels.
    :param show_node_ids: If True, overlay node IDs as labels.
    :param node_size: Size of nodes in the plot.
    :param edge_linewidth: Width of edges in the plot.
    """
    if G is None:
        G = self.G
    try:
        import osmnx as ox
    except ImportError:
        raise ImportError("osmnx is required for visualize_osm. Install via `pip install osmnx`.")
    # Plot base OSM network
    fig, ax = ox.plot_graph(G, node_size=node_size, edge_linewidth=edge_linewidth,
                            show=False, close=False)
    # Prepare positions for labeling
    pos = {node: (data.get('x'), data.get('y')) for node, data in G.nodes(data=True)}
    # Edge labels
    if show_edge_names:
        edge_labels = {}
        for u, v, data in G.edges(data=True):
            name = data.get('name')
            if name:
                # OSMnx 'name' can be list or string
                label = name if isinstance(name, str) else ",".join(name)
                edge_labels[(u, v)] = label
        nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, font_size=6, ax=ax)
    # Node labels
    if show_node_ids or any('name' in d for _, d in G.nodes(data=True)):
        labels = {}
        for node, data in G.nodes(data=True):
            if show_node_ids:
                labels[node] = str(node)
            elif 'name' in data:
                labels[node] = data['name']
        nx.draw_networkx_labels(G, pos, labels=labels, font_size=6, ax=ax)
    ax.set_title("OSM Network Visualization")
    plt.show()



# Create the graph with defaults (can be overridden by user input)
if __name__ == "__main__":
    gf = GraphFaker()
    G = gf.generate_graph(total_nodes=20, total_edges=60)
    print("-> graphfaker generation <-")
    print("Total nodes:", G.number_of_nodes())
    print("Total edges:", G.number_of_edges())
    gf.visualize_faker_graph(title="Graph Faker PoC ")
    # sg.export_graph(filename="social_knowledge_graph.graphml")


gf = GraphFaker()

# Faker social graph
G_rand = gf.generate_graph(source="faker", total_nodes=50, total_edges=200)
gf.visualize_faker_graph(G_rand)

# OSM network
G_osm = gf.generate_graph(
    source="osm",
    place="Chinatown, San Francisco, California",
    network_type="drive"
)
gf.visualize_faker_graph(G_osm)



