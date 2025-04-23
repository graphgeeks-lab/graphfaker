# graphfaker/cli.py
"""
Command-line interface for GraphFaker.
"""
import typer
from graphfaker.core import GraphFaker
from graphfaker.fetchers.osm import OSMGraphFetcher

app = typer.Typer()

@app.command()
def gen(
    mode: str = typer.Option("random", help="Generation mode: random | synthetic ( barabasi - more to come)"),

    # for random mode
    total_nodes: int = typer.Option(100, help="Total nodes for random mode"),
    total_edges: int = typer.Option(1000, help="Total edges for random mode"),

    # for osm mode
    place: str = typer.Option(None, help="OSM place name (e.g., 'Soho Square, London, UK')"),
    bbox: str = typer.Option(
        None,
        help="OSM bounding box as 'north,south,east,west'"
    ),
    network_type: str = typer.Option(
        "drive",
        help="OSM network type: drive | walk | bike | all"
    ),
    simplify: str = typer.Option(True, help="Simplify OSM graph topology"),
    retain_all: bool = typer.Option(False, help="Retain all components in OSM graph"),

    # common
    export: bool = typer.Option("graph.graphml", help="File path to export GraphML"),
    visualize: bool = typer.Option(False, help="Display the graph after generation"),
):
    """Generate a graph using GraphFaker."""
    gf = GraphFaker()
    if mode == "random":
        G = gf.generate_graph(total_nodes=total_nodes, total_edges=total_edges)

    elif mode == "osm":
        # parse bbox string if provided
        bbox_tuple = None
        if bbox:
            north, south, east, west = map(float, bbox.split(","))
            bbox_tuple = (north, south, east, west)
        G = OSMGraphFetcher.fetch_network(
            place=place,
            bbox=bbox_tuple,
            network_type=network_type,
            simplify=simplify,
            retain_all=retain_all
        )

    else:
        typer.echo(f"Mode '{mode}' not supported.")
        raise typer.Exit(code=1)

    if visualize:
        gf.visualize_graph(G, title=f"GraphFaker ({mode})")
    gf.export_graph(G, export)
    typer.echo(f"Generated {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")

if __name__ == "__main__":
    app()
