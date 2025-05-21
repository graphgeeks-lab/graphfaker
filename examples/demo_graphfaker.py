from graphfaker.logger import logger
import marimo

__generated_with = "0.13.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import os
    import io
    import zipfile
    import warnings
    from datetime import datetime, timedelta
    from typing import Tuple, Optional
    from io import StringIO

    import requests
    import pandas as pd
    from tqdm.auto import tqdm
    import networkx as nx
    import time

    # suppress only the single warning from unverified HTTPS
    import urllib3
    from urllib3.exceptions import InsecureRequestWarning

    urllib3.disable_warnings(InsecureRequestWarning)
    return


@app.cell
def _():
    from graphfaker.fetchers.flights import FlightGraphFetcher

    return (FlightGraphFetcher,)


@app.cell
def _(FlightGraphFetcher):
    airlines = FlightGraphFetcher.fetch_airlines()
    airlines.head()
    return


@app.cell
def _(FlightGraphFetcher):
    airports = FlightGraphFetcher.fetch_airports()
    airports.head()
    return


@app.cell
def _(FlightGraphFetcher):
    flights = FlightGraphFetcher.fetch_flights(year=2024, month=1)
    return


@app.cell
def _():
    from graphfaker import GraphFaker

    return (GraphFaker,)


@app.cell
def _(GraphFaker):
    gf = GraphFaker()
    return (gf,)


@app.cell
def _(gf):
    G_flight = gf.generate_graph(source="flights", year=2024, month=1)
    return (G_flight,)


@app.cell
def _(G_flight):
    G_flight.number_of_edges()
    return


@app.cell
def _():
    import scipy

    return


@app.cell
def _():
    # gf.visualize_graph(G_flight)
    return


@app.cell
def _(G_flight):
    G_flight.number_of_nodes()
    return


@app.cell
def _(G_flight):
    import random

    # suppose G_flight is your big graph and you want at most 1000 nodes
    N = 500
    all_nodes = list(G_flight.nodes())
    if len(all_nodes) > N:
        sampled = random.sample(all_nodes, N)
        G_small = G_flight.subgraph(sampled).copy()
    else:
        G_small = G_flight.copy()
    logger.info(
        f"Original: {G_flight.number_of_nodes()} nodes, {G_flight.number_of_edges()} edges"
    )
    logger.info(
        f"Small   : {G_small.number_of_nodes()} nodes, {G_small.number_of_edges()} edges"
    )

    return (G_small,)


@app.cell
def _(G_small, gf):
    gf.visualize_graph(G_small)
    return


if __name__ == "__main__":
    app.run()
