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
