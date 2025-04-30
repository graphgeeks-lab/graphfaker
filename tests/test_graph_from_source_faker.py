# tests/test_graph_from_source_faker
import pytest
import networkx as nx
from graphfaker.core import GraphFaker

def test_graph_from_source_faker():
    gf = GraphFaker()
    # source faker
    G = gf.generate_graph(source="faker", total_nodes=10, total_edges=50)
    # Node count matches requested total_nodes
    assert G.number_of_nodes() == 10
    # Each we are calculating the edges based on the nodes.
    assert G.number_of_edges() >= 10
    # is_directed() should be True
    assert G.is_directed() == True

