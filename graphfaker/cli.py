# graphfaker/cli.py
"""
Command-line interface for GraphFaker.
"""
import typer
from graphfaker.core import GraphFaker

app = typer.Typer()

@app.command()
def gen(
    mode: str = typer.Option("random", help="Generation mode: random | synthetic ( barabasi - more to come)"),
    total_nodes: int = typer.Option(100, help="Total nodes for random mode"),
    total_edges: int = typer.Option(1000, help="Total edges for random mode"),
    export: str = typer.Option("graph.graphml", help="File path to export GraphML"),
    visualize: bool = typer.Option(False, help="Whether to display the graph after generation")
):
    """Generate a graph using GraphFaker."""
    gf = GraphFaker()
    if mode == "random":
        G = gf.generate_graph(total_nodes=total_nodes, total_edges=total_edges)
    else:
        typer.echo(f"Mode '{mode}' not implemented yet.")
        raise typer.Exit(code=1)

    if visualize:
        gf.visualize_graph(G, title=f"GraphFaker ({mode})")
    gf.export_graph(G, export)
    typer.echo(f"Generated {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")

if __name__ == "__main__":
    app()
