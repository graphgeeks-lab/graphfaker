# tests/test_graph_from_source_osm.py

import pytest
import networkx as nx
import osmnx as ox
from graphfaker.fetchers.osm import OSMGraphFetcher

of = OSMGraphFetcher()
def test_graph_from_source_osm_address():
    #Fetch address data
    G = of.fetch_network(address="1600 Amphitheatre Parkway, Mountain View, CA", dist=1000)

    assert G.is_directed() == True, "Value was False, should be True"

    assert G.is_multigraph() == True, "Value was False, should be True"

    assert G.graph['created_with'] == 'OSMnx 2.0.2', "Value wasn't 'OSMnx 2.0.2', osmnx much have been updated"

    assert G.number_of_nodes() <= 100, "Number of nodes should be less than or equal to 100, except it have changed"

    assert G.number_of_edges() >= 195, "Number of edges should be equal or greater than 94, except something changed"

def test_graph_from_source_osm_place():
    G = of.fetch_network(place="Chinatown, San Francisco, California", network_type="drive")

    assert G.is_directed() == True, "Value was False, should be True"

    assert G.is_multigraph() == True, "Value was False, should be True"

    assert G.graph['created_with'] == 'OSMnx 2.0.2', "Value wasn't 'OSMnx 2.0.2', osmnx much have been updated"

    assert G.number_of_nodes() == 50, "Number of nodes should be 50, except it have changed"

    assert G.number_of_edges() >= 94, "Number of edges should be equal or greater than 94, except something changed"

