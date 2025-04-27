# graphfaker/algorithms/ba.py

import networkx as nx

def barabasi_albert_generator(n, m, **_):
    return nx.barabasi_albert_graph
