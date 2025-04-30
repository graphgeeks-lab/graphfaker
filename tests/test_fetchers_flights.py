# tests/test_fetchers_flights.py
import pytest
import pandas as pd
import networkx as nx
from graphfaker.fetchers.flights import FlightGraphFetcher

@pytest.fixture
def sample_airlines_df():
    return pd.DataFrame({
        'carrier': ['AA', 'DL'],
        'airline_name': ['American Airlines', 'Delta Airlines']
    })

@pytest.fixture
def sample_airports_df():
    return pd.DataFrame({
        'faa': ['JFK', 'LAX'],
        'name': ['JFK Intl', 'LAX Intl'],
        'city': ['New York', 'Los Angeles'],
        'country': ['USA', 'USA'],
        'lat': [40.6413, 33.9416],
        'lon': [-73.7781, -118.4085]
    })

@pytest.fixture
def sample_flights_df():
    return pd.DataFrame({
        'year': [2024],
        'month': [1],
        'day': [1],
        'carrier': ['AA'],
        'flight': [100],
        'origin': ['JFK'],
        'dest': ['LAX'],
        'cancelled': [False],
        'delayed': [True]
    })

def test_build_graph_structure(sample_airlines_df, sample_airports_df, sample_flights_df):
    G = FlightGraphFetcher.build_graph(sample_airlines_df, sample_airports_df, sample_flights_df)
    # Check nodes
    expected_nodes = set(['AA', 'DL', 'JFK', 'LAX', 'New York', 'Los Angeles', 'AA100_JFK_LAX_2024-01-01'])
    assert expected_nodes.issubset(set(G.nodes)), "Missing expected nodes"
    # Check node attributes
    flight_node = 'AA100_JFK_LAX_2024-01-01'
    assert G.nodes[flight_node]['type'] == 'Flight'
    assert G.nodes[flight_node]['flight_number'] == 100
    # Check edges and relationships
    # Flight -> Airline
    assert G.has_edge(flight_node, 'AA')
    assert G.edges[flight_node, 'AA']['relationship'] == 'OPERATED_BY'
    # Flight -> Origin
    assert G.has_edge(flight_node, 'JFK')
    assert G.edges[flight_node, 'JFK']['relationship'] == 'DEPARTS_FROM'
    # Flight -> Destination
    assert G.has_edge(flight_node, 'LAX')
    assert G.edges[flight_node, 'LAX']['relationship'] == 'ARRIVES_AT'
    # Airport -> City
    assert G.has_edge('JFK', 'New York')
    assert G.edges['JFK', 'New York']['relationship'] == 'LOCATED_IN'
    assert G.has_edge('LAX', 'Los Angeles')
