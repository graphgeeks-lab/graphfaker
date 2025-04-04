from graphfaker import graphfaker
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
Graphfaker = graphfaker.Graphfaker()

G_people = Graphfaker.generate_people(10)
G_places = Graphfaker.generate_places(5)
G_orgs = Graphfaker.generate_organizations(5)
G_events = Graphfaker.generate_events(3)
G_products = Graphfaker.generate_products(3)

G = nx.Graph()
G.add_nodes_from(G_people.nodes(data=True))
G.add_nodes_from(G_places.nodes(data=True))
G.add_nodes_from(G_orgs.nodes(data=True))
G.add_nodes_from(G_events.nodes(data=True))
G.add_nodes_from(G_products.nodes(data=True))

people_nodes = list(G_people.nodes())
place_nodes = list(G_places.nodes())
org_nodes = list(G_orgs.nodes())
event_nodes = list(G_events.nodes())
product_nodes = list(G_products.nodes())

Graphfaker.connect_people_to_organizations(G, people_nodes, org_nodes)
Graphfaker.connect_people_to_places(G, people_nodes, place_nodes)
Graphfaker.connect_people_to_events(G, people_nodes, event_nodes)
Graphfaker.connect_people_to_products(G, people_nodes, product_nodes)
Graphfaker.connect_orgs_to_places(G, org_nodes, place_nodes)

Graphfaker.visualize_graph(G, title="Graphfaker Synthetic Graph (Jupyter)")
