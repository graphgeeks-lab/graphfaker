from graphfaker import core
import networkx as nx

"""
This error is typically related to the Matplotlib backend that's being used by PyCharm's console.
One workaround is to explicitly switch the backend before plotting. For example, you can try switching to
TkAgg. Add this at the top of your script (before any plotting commands):
"""

import matplotlib
matplotlib.use("TkAgg")

import matplotlib.pyplot as plt

# Generate graph and visualize
gf = core.GraphFaker()

gd = gf.generate_graph(total_nodes=100, total_edges=500)

gf.visualize_graph(gd, title="GraphFaker Project")
