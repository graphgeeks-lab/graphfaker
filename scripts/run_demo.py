# run_demo.py

import matplotlib
matplotlib.use("TkAgg")          # pick a GUI backend on desktop
from graphfaker.core import GraphFaker

# Create the graph with defaults (can be overridden by user input)
if __name__ == "__main__":
    gf = GraphFaker()
    G = gf.generate_graph(total_nodes=20, total_edges=60)
    print("-> graphfaker generation <-")
    print("Total nodes:", G.number_of_nodes())
    print("Total edges:", G.number_of_edges())
    gf.visualize_graph(title="Graph Faker PoC ")
    # sg.export_graph(filename="social_knowledge_graph.graphml")


from graphfaker.core import GraphFaker

gf = GraphFaker()

# Random synthetic
G_rand = gf.generate_graph(mode="random", total_nodes=50, total_edges=200)
gf.visualize_graph(G_rand)

# OSM network
G_osm = gf.generate_graph(
    mode="osm",
    place="Chinatown, San Francisco, California",
    network_type="drive"
)
gf.visualize_graph(G_osm)
