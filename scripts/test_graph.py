from graphfaker.fetchers.flights import FlightGraphFetcher
import networkx as nx

# Initialize fetcher
fetcher = FlightGraphFetcher()

# Fetch datasets
airlines_df = fetcher.fetch_airlines()
airports_df = fetcher.fetch_airports()
flights_df = fetcher.fetch_flights(year=2024, month=1)

# Build full graph
G = fetcher.build_graph(airlines_df, airports_df, flights_df)

# Build subgraph with 5000 connected flights
G_sub = fetcher.build_flight_subgraph(G, limit=1500)

# Save the subgraph
nx.write_gexf(G_sub, "connected_5000_flights.gexf")

print("✅ Exported 'connected_5000_flights.gexf'")


