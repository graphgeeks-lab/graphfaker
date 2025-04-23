# graphfaker/fetchers/osm.py
"""
OSM Fetcher module: wraps OSMnx functionality to retrieve and preprocess street networks.
"""

import networkx as nx
import osmnx as ox

class OSMGraphFetcher:
    @staticmethod
    def fetch_network(
        place: str = None,
        bbox: tuple = None,
        network_type: str =  "drive",
        simplify: bool = True,
        retain_all: bool = False
    ) -> nx.MultiDiGraph:
        """
        Fetch a street network from OpenStreetMap.

        :param place: A place name (e.g., "London, UK") to geocode and retrieve.
        :param bbox: A tuple of (north, south, east, west) coordinates.
        :param network_type: Type of street network ("drive", "walk", "bike", etc.).
        :param simplify: Whether to simplify the graph topology.
        :param retain_all: If True, retain all connected components.
        :return: A NetworkX graph of the street network.
        """
        if place:
            G = ox.graph_from_place(place, network_type=network_type, simplify=simplify, retain_all=retain_all)
        elif bbox:
            north, south, east, west = bbox
            G = ox.graph_from_bbox(north, south, east, west, network_type=network_type, simplify=simplify, retain_all=retain_all)
        else:
            raise ValueError("Either 'place' or 'bbox' must be provided to fetch OSM network.")

        # Project to UTM for accurate distance-based metrics
        G_proj = ox.project_graph(G)
        return G_proj

    @staticmethod
    def basic_stats(G: nx.Graph) -> dict:
        """
        Compute basic statistics of the OSM network
        """
        stats = {
            'nodes': G.number_of_nodes(),
            'edges': G.number_of_edges(),
            'avg_degree': sum(dict(G.degree()).values()) / float(G.number_of_nodes())
        }
        return stats


