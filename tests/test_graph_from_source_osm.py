"""Road networks from OpenStreetMap through OSMnx.

Both tests download from Nominatim and Overpass, which are public services
that rate-limit and time out (Overpass regularly refuses the GitHub runners),
so they carry the ``network`` marker and CI deselects them. Run them by hand
with ``pytest -m network``.
"""

import pytest

from graphfaker.fetchers.osm import OSMGraphFetcher

pytestmark = pytest.mark.network

of = OSMGraphFetcher()


def test_graph_from_source_osm_address():
    G = of.fetch_network(address="1600 Amphitheatre Parkway, Mountain View, CA", dist=1000)

    assert G.is_directed()
    assert G.is_multigraph()
    assert G.graph["created_with"].startswith("OSMnx")
    assert G.number_of_nodes() <= 100, "the 1 km walk network around the address has stayed under 100 intersections"
    assert G.number_of_edges() >= 195


def test_graph_from_source_osm_place():
    G = of.fetch_network(place="Chinatown, San Francisco, California", network_type="drive")

    assert G.is_directed()
    assert G.is_multigraph()
    assert G.graph["created_with"].startswith("OSMnx")
    assert 40 <= G.number_of_nodes() <= 60, "Chinatown's drive network has about 50 intersections; the map changes a little over time"
    assert G.number_of_edges() >= 94
