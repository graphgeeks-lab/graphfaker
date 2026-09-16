# Real-world networks

Sometimes the right substrate is real. GraphFaker loads three real-world sources as NetworkX graphs. They are loaded, not generated, so there is no seed and no ground truth; the plan is to use them as backbones for synthetic layers (deliveries on a real road network, passengers on a real flight network).

## OpenStreetMap road networks

Road, walking or cycling networks by place name, address or bounding box, through OSMnx, projected to UTM.

```bash
graphfaker gen --fetcher osm --place "Berlin, Germany" --network-type drive --export berlin.graphml
```

```python
from graphfaker import GraphFaker

G = GraphFaker().generate_graph(source="osm", place="Berlin, Germany", network_type="drive")
```

The [OSM quick start notebook](../notebooks/osm_quickstart.ipynb) walks through fetching, inspecting and exporting a network.

## Flight networks

Airlines, airports and flight legs from the US Bureau of Transportation Statistics on-time data, for a month or a date range, with cancellation and delay flags.

```bash
graphfaker gen --fetcher flights --country "United States" --year 2024 --month 1 --export flights.graphml
```

```python
G = GraphFaker().generate_graph(source="flights", country="United States", year=2024, month=1)
```

The fetcher downloads with TLS verification enabled. If your system fails to validate the BTS certificate chain, set `GRAPHFAKER_INSECURE_TLS=1` to opt out; this logs a warning and means the downloaded data is no longer authenticated.

## Wikipedia pages

`WikiFetcher` returns a page's title, summary, content, sections, links and references as JSON, for building your own knowledge graph or a retrieval pipeline.

```python
from graphfaker import WikiFetcher

page = WikiFetcher.fetch_page("Graph database")
WikiFetcher.export_page_json(page, "graph_database.json")
```

```{toctree}
:hidden:

../notebooks/osm_quickstart
```
