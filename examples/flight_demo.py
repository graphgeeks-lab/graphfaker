"""
This error is typically related to the Matplotlib backend that's being used by PyCharm's console.
One workaround is to explicitly switch the backend before plotting. For example, you can try switching to
TkAgg. Add this at the top of your script (before any plotting commands):
"""

# import os
# import io
# import zipfile
# import warnings
# from datetime import datetime, timedelta
# from typing import Tuple, Optional
# from io import StringIO
#
# import requests
# import pandas as pd
# from tqdm.auto import tqdm
# import networkx as nx
# import time
#
# # suppress only the single warning from unverified HTTPS
# import urllib3
# from urllib3.exceptions import InsecureRequestWarning
#
# urllib3.disable_warnings(InsecureRequestWarning)


import matplotlib

matplotlib.use("TkAgg")
from graphfaker import GraphFaker
import networkx as nx
from graphfaker.logger import logger

# import lxml.etree as lxmletree

import matplotlib.pyplot as plt

# Generate graph and visualize
gf = GraphFaker()
#
# gd = gf.generate_graph(source='faker', total_nodes=100, total_edges=500)
#
# gf.visualize_graph(gd)

G_flight = gf.generate_graph(source="flights", year=2024, month=1)

# for n, data in G_flight.nodes(data=True):
#     coord = data.get('coordinates')
#     if isinstance(coord, tuple):
#         data['coordinates'] = f"{coord[0]},{coord[1]}"

# for n, data in G_small.nodes(data=True):
#     coord = data.get('coordinates')
#     if isinstance(coord, tuple):
#         data['coordinates'] = f"{coord[0]},{coord[1]}"

# for n, data in G_flight.nodes(data=True):
#     data.pop('coordinates', None)

#
# import random
# #import networkx as nx
#
# # target number of edges
# E = 1000
#
# # list of all directed edges (u,v)
# all_edges = list(G_flight.edges())
# if len(all_edges) > E:
#     sampled_edges = random.sample(all_edges, E)
# else:
#     sampled_edges = all_edges
#
# # induced subgraph on those edges
# G_small = G_flight.edge_subgraph(sampled_edges).copy()
#
# print("Small graph:", G_small.number_of_nodes(), "nodes;",
#       G_small.number_of_edges(), "edges")
#
# for n, data in G_small.nodes(data=True):
#     coord = data.get('coordinates')
#     if isinstance(coord, tuple):
#         data['coordinates'] = f"{coord[0]},{coord[1]}"
#
# nx.write_gexf(G_small, "smallflightGraph.gexf")
#
#
# for n, data in G_small.nodes(data=True):
#     coord = data.get('coordinates')
#     if isinstance(coord, tuple):
#         data['coordinates'] = f"{coord[0]},{coord[1]}"
#
# for n, data in G_connected_subgraph.nodes(data=True):
#     coord = data.get('coordinates')
#     if isinstance(coord, tuple):
#         data['coordinates'] = f"{coord[0]},{coord[1]}"
#
# nx.write_gexf(G_small, "smallflightGraph.gexf")
#
# print("Done exporting")
#
# def find_fully_connected_flights_in_graph(G):
#     connected_flights = []
#
#     for node, data in G.nodes(data=True):
#         if data.get('type') != 'Flight':
#             continue  # Skip non-flights
#
#         # Check direct connections
#         successors = list(G.successors(node))
#         relationships = {G.edges[node, succ]['relationship']: succ for succ in successors if 'relationship' in G.edges[node, succ]}
#
#         has_airline = 'OPERATED_BY' in relationships
#         has_origin = 'DEPARTS_FROM' in relationships
#         has_dest = 'ARRIVES_AT' in relationships
#
#         if not (has_airline and has_origin and has_dest):
#             continue  # Missing key relationships
#
#         # Check if origin and dest airports are themselves connected to a City
#         origin_airport = relationships['DEPARTS_FROM']
#         dest_airport = relationships['ARRIVES_AT']
#
#         origin_city_connected = False
#         dest_city_connected = False
#
#         # Check outgoing edges from airports
#         for succ in G.successors(origin_airport):
#             if G.edges[origin_airport, succ].get('relationship') == 'LOCATED_IN' and G.nodes[succ].get('type') == 'City':
#                 origin_city_connected = True
#
#         for succ in G.successors(dest_airport):
#             if G.edges[dest_airport, succ].get('relationship') == 'LOCATED_IN' and G.nodes[succ].get('type') == 'City':
#                 dest_city_connected = True
#
#         if origin_city_connected and dest_city_connected:
#             connected_flights.append(node)
#
#     print(f"Found {len(connected_flights)} fully connected flights.")
#     return connected_flights
#
#
# # Find fully connected flights in G_flight
# connected_flights = find_fully_connected_flights_in_graph(G_flight)
#
# def build_connected_flights_subgraph(G, connected_flights):
#     nodes_to_keep = set()
#
#     for flight in connected_flights:
#         if not G.has_node(flight):
#             continue
#
#         nodes_to_keep.add(flight)
#
#         # Follow outgoing edges from the flight
#         for succ in G.successors(flight):
#             nodes_to_keep.add(succ)
#
#             # If the successor is an Airport, find the City it's located in
#             if G.nodes[succ].get('type') == 'Airport':
#                 for airport_succ in G.successors(succ):
#                     if G.edges[succ, airport_succ].get('relationship') == 'LOCATED_IN':
#                         nodes_to_keep.add(airport_succ)
#
#     # Create subgraph
#     subG = G.subgraph(nodes_to_keep).copy()
#
#     return subG
#
#
# #
# # import matplotlib.pyplot as plt
# # import networkx as nx
# #
# # # Color map by node 'type'
# # def get_node_color(nodetype):
# #     if nodetype == 'Flight':
# #         return 'skyblue'
# #     elif nodetype == 'Airport':
# #         return 'lightgreen'
# #     elif nodetype == 'Airline':
# #         return 'orange'
# #     elif nodetype == 'City':
# #         return 'pink'
# #     else:
# #         return 'gray'
# #
# # # Get layout
# # pos = nx.spring_layout(G_small, seed=42)  # Nice looking layout
# #
# # # Build color list
# # node_colors = [get_node_color(G_small.nodes[n].get('type', '')) for n in G_small.nodes]
# #
# # # Draw nodes
# # plt.figure(figsize=(14,10))
# # nx.draw_networkx_nodes(G_small, pos, node_color=node_colors, node_size=300)
# #
# # # Draw edges
# # nx.draw_networkx_edges(G_small, pos, arrows=True)
# #
# # # Draw node labels (optional: flight numbers, city names)
# # nx.draw_networkx_labels(G_small, pos, font_size=8)
# #
# # # Draw edge labels (relationship types)
# # edge_labels = nx.get_edge_attributes(G_small, 'relationship')
# # nx.draw_networkx_edge_labels(G_small, pos, edge_labels=edge_labels, font_size=6, label_pos=0.5)
# #
# # plt.title("Sample Flight Graph Visualization")
# # plt.axis('off')
# # plt.show()
#
#
#
# # gf.visualize_graph(G_flight)
#
#
# #
# # import random
# #
# # # suppose G_flight is your big graph and you want at most 1000 nodes
# # N = 100
# # all_nodes = list(G_flight.nodes())
# # if len(all_nodes) > N:
# #     sampled = random.sample(all_nodes, N)
# #     G_small = G_flight.subgraph(sampled).copy()
# # else:
# #     G_small = G_flight.copy()
# #
# # print("Original:", G_flight.number_of_nodes(), "nodes,", G_flight.number_of_edges(), "edges")
# # print("Small   :", G_small.number_of_nodes(), "nodes,", G_small.number_of_edges(), "edges")
# #
# # gf.visualize_graph(G_small)
# # # # #%matplotlib inline
#
#
#
#
#
# # from graphfaker.core import GraphFaker
# #
# # gf = GraphFaker()
# # G = gf.generate_graph(total_nodes=50, total_edges=200)
# # gf.visualize_graph(G, title="GraphFaker in Jupyter")
# # gf.export_graph(G, path="notebook.graphml")
#
# import random, networkx as nx
#
# # target number of edges
# E = 1000
#
# # list of all directed edges (u,v)
# all_edges = list(G_flight.edges())
# if len(all_edges) > E:
#     sampled_edges = random.sample(all_edges, E)
# else:
#     sampled_edges = all_edges
# #
# # # induced subgraph on those edges
# # G_small = G_flight.edge_subgraph(sampled_edges).copy()
# #
# # print("Small graph:", G_small.number_of_nodes(), "nodes;",
# #       G_small.number_of_edges(), "edges")
#
#

# Suppose you have a flight node id like "AA100_JFK_LAX_2024-01-05"


def find_fully_connected_flights_in_graph(G):
    connected_flights = []

    for node, data in G.nodes(data=True):
        if data.get("type") != "Flight":
            continue  # Only check flights

        successors = list(G.successors(node))
        relationships = {
            G.edges[node, succ]["relationship"]: succ
            for succ in successors
            if "relationship" in G.edges[node, succ]
        }

        has_airline = "OPERATED_BY" in relationships
        has_origin = "DEPARTS_FROM" in relationships
        has_dest = "ARRIVES_AT" in relationships

        if not (has_airline and has_origin and has_dest):
            continue  # Skip flights not properly connected

        # Check Airports -> Cities
        origin_airport = relationships["DEPARTS_FROM"]
        dest_airport = relationships["ARRIVES_AT"]

        origin_city_connected = any(
            G.edges[origin_airport, succ].get("relationship") == "LOCATED_IN"
            for succ in G.successors(origin_airport)
        )

        dest_city_connected = any(
            G.edges[dest_airport, succ].get("relationship") == "LOCATED_IN"
            for succ in G.successors(dest_airport)
        )

        if origin_city_connected and dest_city_connected:
            connected_flights.append(node)
    logger.info(f"Found {len(connected_flights)} fully connected flights.")

    return connected_flights


def build_connected_flights_subgraph(G, connected_flights, limit=1000):
    nodes_to_keep = set()

    # Pick only the first N flights
    selected_flights = connected_flights[:limit]

    for flight in selected_flights:
        if not G.has_node(flight):
            continue

        nodes_to_keep.add(flight)

        # Follow outgoing edges from the flight
        for succ in G.successors(flight):
            relationship = G.edges[flight, succ].get("relationship")

            if relationship in ("OPERATED_BY", "DEPARTS_FROM", "ARRIVES_AT"):
                nodes_to_keep.add(succ)

                # If it's an Airport, find the City
                if G.nodes[succ].get("type") == "Airport":
                    for city_succ in G.successors(succ):
                        if G.edges[succ, city_succ].get("relationship") == "LOCATED_IN":
                            nodes_to_keep.add(city_succ)

    # Create subgraph
    subG = G.subgraph(nodes_to_keep).copy()
    return subG


import networkx as nx

# 1. Find fully connected flights
connected_flights = find_fully_connected_flights_in_graph(G_flight)

# 2. Build subgraph with 1000 flights
G_sub = build_connected_flights_subgraph(G_flight, connected_flights, limit=1500)

## to export because of tuple
for n, data in G_sub.nodes(data=True):
    coord = data.get("coordinates")
    if isinstance(coord, tuple):
        data["coordinates"] = f"{coord[0]},{coord[1]}"

# 3. Export to GEXF
nx.write_gexf(G_sub, "fully_connected_1500_flights.gexf")
logger.info(
    f"✅ Exported {G_sub.number_of_nodes()} nodes and {G_sub.number_of_edges()} edges to fully_connected_1500_flights.gexf"
)
