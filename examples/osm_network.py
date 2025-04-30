

import marimo

__generated_with = "0.13.1"
app = marimo.App(width="medium")


@app.cell
def _():
    from graphfaker.core import GraphFaker
    return (GraphFaker,)


@app.cell
def _(GraphFaker):
    gf = GraphFaker()
    return (gf,)


@app.cell
def _(gf):
    G_rand = gf.generate_graph(mode="random", total_nodes=50, total_edges=200)
    return (G_rand,)


@app.cell
def _(G_rand, gf):
    gf.visualize_graph(G_rand)
    return


@app.cell
def _(gf):
    G_osm = gf.generate_graph(
        mode="osm",
        place="Chinatown, San Francisco, California",
        network_type="drive"
    )
    return (G_osm,)


@app.cell
def _(G_osm):
    G_osm
    return


@app.cell
def _(G_osm, gf):
    gf.visualize_graph(G_osm)
    return


@app.cell
def _():
    return


@app.cell
def _(G_osm, gf):
    gf.visualize_osm(G_osm, node_size=100)
    return


@app.cell
def _():
    from graphfaker.fetchers.osm import OSMGraphFetcher
    return


@app.cell
def _(G_osm, gf):
    gf.basic_stats(G_osm)
    return


@app.cell
def _():
    import networkx as nx
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
    return (basic_stats,)


@app.cell
def _(G_osm, basic_stats):
    basic_stats(G_osm)
    return


@app.cell
def _():
    import osmnx as ox

    # Example: Download street network for "Birmingham, B16, UK"
    G = ox.graph_from_place("Chinatown, San Francisco, California", network_type="drive")

    # Visualize the graph using a static map
    fig, ax = ox.plot_graph(
        G,
        node_size=50,
        node_color="red",
        edge_color="black",
        edge_linewidth=2,
        bgcolor="white",
    )

    # Customize the plot (optional)
    ax.set_title("Birmingham, B16 Street Network (Driving)")
    return G, fig, ox


@app.cell
def _(G, fig, ox):

    # Or visualize using an interactive web map
    m = ox.plot_graph_folium(G, popup_attribute="name", weight=3, color="blue")

    # Display the plot
    fig.show()
    m.show()
    return


if __name__ == "__main__":
    app.run()
