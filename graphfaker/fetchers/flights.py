# graphfaker/fetchers/flights.py
"""
Flight network fetcher module for GraphFaker.

Ontology / Schema:

Node Types:
  - Airport:
      id: IATA or ICAO code (string)
      name: Airport name (string)
      city: City served (string)
      country: Country (string)
      coordinates: (lat, lon) tuple

  - Airline:
      id: IATA or ICAO code (string)
      name: Airline name (string)
      country: Headquarters country (string)

  - Flight:
      flight_number: Combined airline code + number (string)
      cancelled: Flight cancelled flag (bool)
      delayed: Flight delayed flag (bool)

Relationships:
  - (Flight) -[OPERATED_BY]-> (Airline)
  - (Flight) -[DEPARTS_FROM]-> (Airport)
  - (Flight) -[ARRIVES_AT]-> (Airport)

Fetching Strategy:
  - load static OpenFlights CSV datasets (airports.dat, routes.dat)

Example Usage:
    from graphfaker.fetchers.flights import FlightGraphFetcher
    G = FlightGraphFetcher.fetch_from_openflights(
        airports_csv="data/airports.dat",
        routes_csv="data/routes.dat"
    )
"""
import networkx as nx
import csv
import random

class FlightGraphFetcher:
    @staticmethod
    def fetch_from_openflights(airports_csv: str, routes_csv: str) -> nx.DiGraph:
        """
        Build a flight network from OpenFlights data files:
        - airports_csv: path to airports.dat (CSV)
        - routes_csv: path to routes.dat (CSV)

        Returns a directed graph with Airport, Airline, and Flight nodes,
        including cancellation and delay flags on Flight nodes.
        """
        G = nx.DiGraph()

        # Load Airports as nodes
        with open(airports_csv, newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                airport_id = row[4] or row[5]  # prefer IATA, fallback ICAO
                name       = row[1]
                city       = row[2]
                country    = row[3]
                lat        = float(row[6])
                lon        = float(row[7])
                G.add_node(
                    airport_id,
                    type="Airport",
                    name=name,
                    city=city,
                    country=country,
                    coordinates=(lat, lon)
                )

        # Load Routes / Flights as Flight nodes with edges
        with open(routes_csv, newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                airline_code = row[0]
                src_airport   = row[2]
                dst_airport   = row[4]
                flight_num    = f"{airline_code}{row[5]}"

                # Add Airline node if not exist
                if not G.has_node(airline_code):
                    G.add_node(
                        airline_code,
                        type="Airline",
                        name=None,
                        country=None
                    )

                # Add Flight node with cancellation/delay flags
                flight_node = f"flight_{flight_num}_{src_airport}_{dst_airport}"
                cancelled = random.choice([True, False])
                delayed   = False if cancelled else random.choice([True, False])
                G.add_node(
                    flight_node,
                    type="Flight",
                    flight_number=flight_num,
                    cancelled=cancelled,
                    delayed=delayed
                )

                # Relationships
                G.add_edge(flight_node, airline_code, relationship="OPERATED_BY")
                G.add_edge(flight_node, src_airport,   relationship="DEPARTS_FROM")
                G.add_edge(flight_node, dst_airport,   relationship="ARRIVES_AT")

        return G
