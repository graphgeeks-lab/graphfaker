=====
Usage
=====

To use graphfaker in a project::

    import graphfaker

Flight Data
==========

GraphFaker supports generating airline flight network graphs using real-world airline, airport, and flight data.

To generate a flight network graph for the United States in January 2024 from the command line::

    python -m graphfaker.cli gen --mode flights --country "United States" --year 2024 --month 1 --export flights.graphml

To use the Python API::

    from graphfaker import GraphFaker
    gf = GraphFaker()
    G = gf.generate_graph(source="flights", country="United States", year=2024, month=1)
    gf.visualize_graph(title="US Flight Network (Jan 2024)")
    gf.export_graph("flights.graphml")

You can also specify a date range::

    G = gf.generate_graph(source="flights", country="United States", date_range=("2024-01-01", "2024-01-15"))

See the documentation for more details on available options.
