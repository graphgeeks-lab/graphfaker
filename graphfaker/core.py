"""GraphFaker: generate or load graphs.

Synthetic generation is schema-driven: ``GraphFaker.generate_graph(source="faker")``
builds the social schema from :mod:`graphfaker.domains.social` and runs it
through :mod:`graphfaker.engine`. Any :class:`~graphfaker.schema.GraphSchema`
can be generated with :meth:`GraphFaker.generate`. The ``osm`` and ``flights``
sources load real networks and are unchanged.
"""

from collections.abc import Sequence
from typing import Any

import networkx as nx

from graphfaker.domains import social
from graphfaker.engine import GraphRun
from graphfaker.engine import generate as _generate
from graphfaker.fetchers.flights import FlightGraphFetcher
from graphfaker.fetchers.osm import OSMGraphFetcher
from graphfaker.logger import logger
from graphfaker.resolve import ResolutionResult, resolve_entities
from graphfaker.schema import GraphSchema

# Re-exported for anyone who imported these constants from here.
PERSON_SUBTYPES = social.PERSON_SUBTYPES
PLACE_SUBTYPES = social.PLACE_SUBTYPES
ORG_SUBTYPES = social.ORG_SUBTYPES
EVENT_SUBTYPES = social.EVENT_SUBTYPES
PRODUCT_SUBTYPES = social.PRODUCT_SUBTYPES
INDUSTRY_BY_SUBTYPE = social.INDUSTRY_BY_SUBTYPE


class GraphFaker:
    """Generate or load graphs into NetworkX.

    Args:
        seed: Optional integer making synthetic generation reproducible. The
            same seed and the same arguments always produce an identical graph,
            in any process. Seeding is per-instance: two ``GraphFaker(seed=1)``
            objects do not interfere with each other or with the global
            ``random`` module.
    """

    def __init__(self, seed: int | None = None):
        self.G: nx.DiGraph = nx.DiGraph()
        self.seed = seed
        #: The last synthetic run: tables, truth and manifest. ``None`` until
        #: :meth:`generate` or ``generate_graph(source="faker")`` is called.
        self.run: GraphRun | None = None

    def reseed(self, seed: int | None) -> None:
        """Set the seed used by the next generation call."""
        self.seed = seed

    # ------------------------------------------------------------ synthetic

    def generate(self, schema: GraphSchema, seed: int | None = None, **kwargs: Any) -> GraphRun:
        """Generate any schema. Sets ``self.G`` to the NetworkX view and returns
        the full run (tables, truth, manifest).

        Args:
            schema: A :class:`~graphfaker.schema.GraphSchema`, e.g. from
                :func:`graphfaker.domains.social.schema`.
            seed: Overrides the instance seed for this call.
            **kwargs: Passed to :func:`graphfaker.engine.generate`.
        """
        if seed is not None:
            self.reseed(seed)
        self.run = _generate(schema, seed=self.seed, **kwargs)
        self.G = self.run.to_networkx()
        return self.run

    def generate_nodes(self, total_nodes: int = 100, communities: int | None = None) -> None:
        """Generate only the nodes of the social schema onto ``self.G``.

        Kept for callers that built graphs in two steps; :meth:`generate` is
        the primary path.
        """
        schema = social.schema(total_nodes=total_nodes, total_edges=0, communities=communities)
        self.generate(schema)

    def generate_edges(self, total_edges: int = 1000, topology: str = "realistic") -> None:
        """Form edges over nodes produced by :meth:`generate_nodes`."""
        from graphfaker.backends import GraphTables
        from graphfaker.engine.seeding import Streams
        from graphfaker.engine.topology import build_edges

        communities = len({d.get("community") for _, d in self.G.nodes(data=True)}) or None
        schema = social.schema(
            total_nodes=self.G.number_of_nodes(),
            total_edges=total_edges,
            topology=topology,
            communities=communities,
        )
        tables = build_edges(GraphTables.from_networkx(self.G), schema, Streams.root(self.seed))
        self.G = tables.to_networkx()

    def _generate_faker(
        self,
        total_nodes: int,
        total_edges: int,
        topology: str,
        communities: int | None,
    ) -> nx.DiGraph:
        schema = social.schema(
            total_nodes=total_nodes,
            total_edges=total_edges,
            topology=topology,
            communities=communities,
        )
        self.generate(schema)
        return self.G

    # ------------------------------------------------------------ real data

    def _generate_osm(
        self,
        place: str | None = None,
        address: str | None = None,
        bbox: tuple | None = None,
        network_type: str = "drive",
        simplify: bool = True,
        retain_all: bool = False,
        dist: float = 1000,
    ) -> nx.DiGraph:
        """Fetch an OSM network via OSMFetcher"""
        try:
            if bbox and len(bbox) != 4:
                raise ValueError("Bounding box (bbox) must be a tuple of 4 values: (minx, miny, maxx, maxy).")
            if dist <= 0:
                raise ValueError("Distance (dist) must be greater than 0.")
            G = OSMGraphFetcher.fetch_network(
                place=place,
                address=address,
                bbox=bbox,
                network_type=network_type,
                simplify=simplify,
                retain_all=retain_all,
                dist=dist,
            )
            self.G = G
            return G
        except Exception as e:
            logger.error(f"Failed to generate OSM graph: {e}")
            raise

    def _generate_flights(
        self,
        country: str = "United States",
        year: int | None = None,
        month: int | None = None,
        date_range: tuple | None = None,
    ):
        """
        Fetch flights, airport, and airline via FlightFetcher
        """
        try:
            if year and (year < 1900 or year > 2100):
                raise ValueError("Year must be between 1900 and 2100.")
            if month and (month < 1 or month > 12):
                raise ValueError("Month must be between 1 and 12.")
            if date_range and len(date_range) != 2:
                raise ValueError("Date range must be a tuple of two dates: (start_date, end_date).")

            # 1) Fetch  airline and airport tables
            airlines_df = FlightGraphFetcher.fetch_airlines()
            airports_df = FlightGraphFetcher.fetch_airports(country=country)

            # Fetch flight transit on-time performance data
            flights_df = FlightGraphFetcher.fetch_flights(
                year=year, month=month, date_range=date_range
            )
            logger.info(
                f"Fetched {len(airlines_df)} airlines, "
                f"{len(airports_df)} airports, "
                f"{len(flights_df)} flights."
            )

            G = FlightGraphFetcher.build_graph(airlines_df, airports_df, flights_df)
            self.G = G

            # Inform users of which span was downloaded
            if date_range:
                start, end = date_range
                logger.info(f"Flight data covers {start} -> {end}")

            else:
                logger.info(f"Flight data for {year}-{month:02d}")
            return G
        except Exception as e:
            logger.error(f"Failed to generate flight graph: {e}")
            raise

    def generate_graph(
        self,
        source: str = "faker",
        total_nodes: int = 100,
        total_edges: int = 1000,
        place: str | None = None,
        address: str | None = None,
        bbox: tuple | None = None,
        network_type: str = "drive",
        simplify: bool = True,
        retain_all: bool = False,
        dist: float = 1000,
        country: str = "United States",
        year: int = 2024,
        month: int = 1,
        date_range: tuple | None = None,
        seed: int | None = None,
        topology: str = "realistic",
        communities: int | None = None,
    ) -> nx.DiGraph:
        """
        Unified entrypoint: choose 'faker', 'osm', or 'flights'.
        Pass kwargs depending on source.

        Args:
            seed: Reseed before generating, making the result reproducible.
                Only affects the 'faker' source; 'osm' and 'flights' fetch real
                data and are not randomised.
            topology: For the 'faker' source. ``"realistic"`` (default) builds a
                heavy-tailed, clustered, community-structured graph.
                ``"uniform"`` reproduces the pre-0.5 Erdos-Renyi behaviour and
                exists for comparison only.
            communities: Number of latent groups. Defaults to about one per 25
                nodes.
        """
        if seed is not None:
            self.reseed(seed)

        if source == "faker":
            return self._generate_faker(
                total_nodes=total_nodes,
                total_edges=total_edges,
                topology=topology,
                communities=communities,
            )
        elif source == "osm":
            logger.info(
                f"Generating OSM graph with source={source}, "
                f"place={place}, address={address}, bbox={bbox}, "
                f"network_type={network_type}, simplify={simplify}, "
                f"retain_all={retain_all}, dist={dist}"
            )
            return self._generate_osm(
                place=place,
                address=address,
                bbox=bbox,
                network_type=network_type,
                simplify=simplify,
                retain_all=retain_all,
                dist=dist,
            )
        elif source == "flights":
            return self._generate_flights(
                country=country,
                year=year,
                month=month,
                date_range=date_range,
            )
        else:
            raise ValueError(
                f"Unknown source '{source}'. Use 'faker', 'osm', or 'flights'."
            )
    def resolve(
        self,
        G: nx.Graph | None = None,
        on: Sequence[str] = ("name",),
        threshold: float = 0.85,
        structural_weight: float = 0.5,
        **kwargs: Any,
    ) -> ResolutionResult:
        """Find nodes that look like duplicates of the same entity.

        Scores candidate pairs on attribute similarity *and* neighbourhood
        overlap, the signal a tabular record-linkage tool cannot see. Nothing
        is modified; call `.apply()` on the result to get a merged graph.

        Args:
            G: Graph to resolve. Defaults to the graph held on this instance.
            on: Attribute keys to compare, most identifying first.
            threshold: Minimum combined score to link a pair, in [0, 1].
            structural_weight: How much shared-neighbour evidence may lift a
                pair's score. 0 reduces this to plain attribute matching.
            **kwargs: Passed through to
                :func:`graphfaker.resolve.resolve_entities`.

        Example:
            >>> gf = GraphFaker(seed=42)
            >>> _ = gf.generate_graph(source="faker", total_nodes=100)
            >>> result = gf.resolve(on=["name", "email"], threshold=0.9)
            >>> clean = result.apply()
        """
        target = self.G if G is None else G
        if target is None:
            raise ValueError("No graph available to resolve.")
        return resolve_entities(
            target,
            on=on,
            threshold=threshold,
            structural_weight=structural_weight,
            **kwargs,
        )

    def export_graph(
        self, G: nx.Graph | None = None, source: str | None = None, path: str = "graph.graphml"
    ):
        """
        Export the graph to GraphML format.

        Args:
            G: Optional NetworkX graph. If None, uses self.G.
            source: Optional string, if "osm" uses osmnx for export.
            path: Destination file path for .graphml output.

        Notes:
            GraphML is useful for visualization in tools like Gephi or Cytoscape.
            Node/edge attributes should be simple types (str, int, float).
        """
        import os

        abs_path = os.path.abspath(path)
        os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)

        if G is None:
            G = self.G
        if G is None:
            raise ValueError("No graph available to export.")

        # Sanitize attributes that are not GraphML-friendly. GraphML only
        # accepts scalars, so containers (coordinate tuples, merge provenance
        # from resolve(), anything a user attached) are flattened to strings.
        for _, data in G.nodes(data=True):
            if 'coordinates' in data and isinstance(data['coordinates'], tuple):
                lat, lon = data['coordinates']
                data['coordinates'] = f"{lat},{lon}"
            for key, value in list(data.items()):
                if value is None:
                    # GraphML has no null; the writer raises on NoneType.
                    data[key] = ""
                elif isinstance(value, (list, tuple, set)):
                    data[key] = ",".join(str(item) for item in value)
                elif isinstance(value, dict):
                    data[key] = "; ".join(
                        f"{k}={v}" for k, v in sorted(value.items(), key=str)
                    )

        if source == "osm":
            try:
                import osmnx as ox
                ox.io.save_graphml(G, filepath=abs_path)
            except ImportError as exc:
                raise ImportError("osmnx is required to export OSM graphs.") from exc
        else:
            nx.write_graphml(G, abs_path)

        print(f"✅ Graph exported to: {abs_path}")
