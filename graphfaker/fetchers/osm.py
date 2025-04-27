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
        address: str = None,
        bbox: tuple = None,
        network_type: str =  "drive",
        simplify: bool = True,
        retain_all: bool = False,
        dist: float = 1000
    ) -> nx.MultiDiGraph:
        """
        OSMGraphFetcher: Fetch and preprocess street networks from OpenStreetMap via OSMnx.

        Methods:
            fetch_network(
                place: str = None,
                address: str = None,
                bbox: tuple = None,
                network_type: str = "drive",
                simplify: bool = True,
                retain_all: bool = False,
                dist: float = 1000
            ) -> nx.MultiDiGraph
                Fetch a street network and project it to UTM for accurate spatial analysis.

        Parameters:
            place (str, optional): A geographic name (e.g., "London, UK") to geocode and fetch.
            address (str, optional): A specific address or point-of-interest to geocode and fetch around.
            bbox (tuple, optional): A bounding box as (north, south, east, west) coordinates.
            network_type (str): OSMnx network type: "drive", "walk", "bike", or "all".
            simplify (bool): If True, simplify the graph topology (merge intersections).
            retain_all (bool): If True, keep all connected components; else largest only.
            dist (float): Search radius in meters when fetching by address.

        Returns:
            nx.MultiDiGraph: Projected street network graph (UTM coordinates).

        Raises:
            ValueError: If none of place, address, or bbox is provided.
            ImportError: If OSMnx is not installed.

        Example:
            from graphfaker.fetchers.osm import OSMGraphFetcher
            # Fetch by place
            G1 = OSMGraphFetcher.fetch_network(place="San Francisco, CA")
            # Fetch by address within 500m
            G2 = OSMGraphFetcher.fetch_network(address="1600 Amphitheatre Parkway, Mountain View, CA", dist=500)
            # Fetch by bounding box
            bbox = (37.79, 37.77, -122.41, -122.43)
            G3 = OSMGraphFetcher.fetch_network(bbox=bbox, network_type="walk")
        """
        if address:
            G = ox.graph_from_address(address, dist=dist,network_type=network_type, simplify=simplify, retain_all=retain_all)
        elif place:
            G = ox.graph_from_place(place, network_type=network_type, simplify=simplify, retain_all=retain_all)
        elif bbox:
            north, south, east, west = bbox
            G = ox.graph_from_bbox(north, south, east, west, network_type=network_type, simplify=simplify, retain_all=retain_all)
        else:
            raise ValueError("Either 'place', 'address', or 'bbox' must be provided to fetch OSM network.")

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


