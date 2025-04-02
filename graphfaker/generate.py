import networkx as nx
import json

# --- Graph Generators ---
def social_network(nodes=100, edges=500):
    """Generate a synthetic social network graph."""
    G = nx.gnm_random_graph(nodes, edges, directed=True)
    nx.set_node_attributes(G, {n: {'type': 'user'} for n in G.nodes})
    return G

def transportation_network(nodes=50, edges=200):
    """Generate a synthetic transportation network graph."""
    G = nx.gnm_random_graph(nodes, edges)
    nx.set_edge_attributes(G, {e: {'weight': nx.utils.uniform(1, 100)} for e in G.edges})
    return G

def knowledge_graph():
    """Generate a simple synthetic knowledge graph."""
    G = nx.DiGraph()
    entities = ["Person", "Organization", "Location"]
    edges = [("Person", "works_at", "Organization"),
             ("Person", "lives_in", "Location"),
             ("Organization", "located_in", "Location")]
    for e in edges:
        G.add_edge(e[0], e[2], relation=e[1])
    return G

# --- Export Functions ---
def export(G, format="graphml", filename="graph_output.graphml"):
    """Export the graph to various formats."""
    if format == "json":
        data = nx.node_link_data(G)
        with open(filename, "w") as f:
            json.dump(data, f, indent=4)
    elif format == "graphml":
        nx.write_graphml(G, filename)
    elif format == "csv":
        nx.write_edgelist(G, filename, delimiter=",")
    else:
        raise ValueError("Unsupported format. Choose from: json, graphml, csv")
    print(f"Graph exported as {filename}")

# --- Example Usage ---
if __name__ == "__main__":
    G = social_network(nodes=100, edges=500)
    export(G, format="graphml", filename="social_graph.graphml")
