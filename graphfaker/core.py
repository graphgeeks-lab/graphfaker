"""Social Knowledge Graph module.
A multi-domain network connecting entities across social, geographical, and commercial dimensions.
"""

import networkx as nx
import random
from faker import Faker
from graphfaker.fetchers.osm import OSMGraphFetcher
from graphfaker.fetchers.flights import FlightGraphFetcher
from graphfaker.logger import logger

fake = Faker()

# Define subtypes for each node category
PERSON_SUBTYPES = ["Student", "Professional", "Retiree", "Unemployed"]
PLACE_SUBTYPES = ["City", "Park", "Restaurant", "Airport", "University"]
ORG_SUBTYPES = ["TechCompany", "Hospital", "NGO", "University", "RetailChain"]
EVENT_SUBTYPES = ["Concert", "Conference", "Protest", "SportsGame"]
PRODUCT_SUBTYPES = ["Electronics", "Apparel", "Book", "Vehicle"]

# Define relationship possibilities
REL_PERSON_PERSON = ["FRIENDS_WITH", "COLLEAGUES", "MENTORS"]
REL_PERSON_PLACE = ["LIVES_IN", "VISITED", "BORN_IN"]
REL_PERSON_ORG = ["WORKS_AT", "STUDIED_AT", "OWNS"]
REL_ORG_PLACE = ["HEADQUARTERED_IN", "HAS_BRANCH"]
REL_PERSON_EVENT = ["ATTENDED", "ORGANIZED"]
REL_ORG_PRODUCT = ["MANUFACTURES", "SELLS"]
REL_PERSON_PRODUCT = ["PURCHASED", "REVIEWED"]

# Connection probability distribution (as percentages)
EDGE_DISTRIBUTION = {
    ("Person", "Person"): (REL_PERSON_PERSON, 0.40),
    ("Person", "Place"): (REL_PERSON_PLACE, 0.20),
    ("Person", "Organization"): (REL_PERSON_ORG, 0.15),
    ("Organization", "Place"): (REL_ORG_PLACE, 0.10),
    ("Person", "Event"): (REL_PERSON_EVENT, 0.08),
    ("Organization", "Product"): (REL_ORG_PRODUCT, 0.05),
    ("Person", "Product"): (REL_PERSON_PRODUCT, 0.02),
}


class GraphFaker:
    def __init__(self):
        # We'll use a directed graph for directional relationships.
        self.G = nx.DiGraph()

    def generate_nodes(self, total_nodes=100):
        """
        Generates nodes split into:
         - People (50%)
         - Places (20%)
         - Organizations (15%)
         - Events (10%)
         - Products (5%)
        """
        counts = {
            "Person": int(total_nodes * 0.50),
            "Place": int(total_nodes * 0.20),
            "Organization": int(total_nodes * 0.15),
            "Event": int(total_nodes * 0.10),
        }
        # Remaining nodes will be Products
        counts["Product"] = total_nodes - sum(counts.values())

        # Generate People
        for i in range(counts["Person"]):
            node_id = f"person_{i}"
            subtype = random.choice(PERSON_SUBTYPES)
            self.G.add_node(
                node_id,
                type="Person",
                name=fake.name(),
                age=random.randint(18, 80),
                occupation=fake.job(),
                email=fake.email(),
                education_level=random.choice(
                    ["High School", "Bachelor", "Master", "PhD"]
                ),
                skills=", ".join(fake.words(nb=3)),
                subtype=subtype,
            )
        # Generate Places
        for i in range(counts["Place"]):
            node_id = f"place_{i}"
            subtype = random.choice(PLACE_SUBTYPES)
            self.G.add_node(
                node_id,
                type="Place",
                name=fake.city(),
                place_type=subtype,
                population=random.randint(10000, 1000000),
                coordinates=(fake.latitude(), fake.longitude()),
            )
        # Generate Organizations
        for i in range(counts["Organization"]):
            node_id = f"org_{i}"
            subtype = random.choice(ORG_SUBTYPES)
            self.G.add_node(
                node_id,
                type="Organization",
                name=fake.company(),
                industry=fake.job(),
                revenue=round(random.uniform(1e6, 1e9), 2),
                employee_count=random.randint(50, 5000),
                subtype=subtype,
            )
        # Generate Events
        for i in range(counts["Event"]):
            node_id = f"event_{i}"
            subtype = random.choice(EVENT_SUBTYPES)
            self.G.add_node(
                node_id,
                type="Event",
                name=fake.catch_phrase(),
                event_type=subtype,
                start_date=fake.date(),
                duration=random.randint(1, 5),
            )  # days
        # Generate Products
        for i in range(counts["Product"]):
            node_id = f"product_{i}"
            subtype = random.choice(PRODUCT_SUBTYPES)
            self.G.add_node(
                node_id,
                type="Product",
                name=fake.word().capitalize(),
                category=subtype,
                price=round(random.uniform(10, 1000), 2),
                release_date=fake.date(),
            )

    def add_relationship(
        self, source, target, rel_type, attributes=None, bidirectional=False
    ):
        """
        Adds a relationship edge from source to target.
        If bidirectional, also adds the reverse edge.
        """
        if attributes is None:
            attributes = {}
        self.G.add_edge(source, target, relationship=rel_type, **attributes)
        if bidirectional:
            self.G.add_edge(target, source, relationship=rel_type, **attributes)

    def generate_edges(self, total_edges=1000):
        """
        Generate edges based on the EDGE_DISTRIBUTION probabilities.
        The number of edges for each relationship category is determined by the weight.
        """
        # Get node lists by type
        nodes_by_type = {
            "Person": [],
            "Place": [],
            "Organization": [],
            "Event": [],
            "Product": [],
        }
        for node, data in self.G.nodes(data=True):
            t = data.get("type")
            if t in nodes_by_type:
                nodes_by_type[t].append(node)

        # For each category in EDGE_DISTRIBUTION, calculate the number of edges
        for (src_type, tgt_type), (possible_rels, weight) in EDGE_DISTRIBUTION.items():
            num_edges = int(total_edges * weight)
            src_nodes = nodes_by_type.get(src_type, [])
            tgt_nodes = nodes_by_type.get(tgt_type, [])
            if not src_nodes or not tgt_nodes:
                continue

            for _ in range(num_edges):
                source = random.choice(src_nodes)
                target = random.choice(tgt_nodes)
                # Avoid self-loop in same category if not desired
                if src_type == tgt_type and source == target:
                    continue
                rel = random.choice(possible_rels)
                attr = {}
                # Add additional attributes for specific relationships
                if rel == "VISITED":
                    attr["visit_count"] = random.randint(1, 20)
                elif rel == "WORKS_AT":
                    attr["position"] = fake.job()
                elif rel == "PURCHASED":
                    attr["date"] = fake.date()
                    attr["amount"] = round(random.uniform(1, 500), 2)
                elif rel == "REVIEWED":
                    attr["rating"] = random.randint(1, 5)

                # Define directionality and bidirectionality
                # For Person-Person FRIENDS_WITH and COLLEAGUES, treat as bidirectional
                bidir = False
                if (
                    src_type == "Person"
                    and tgt_type == "Person"
                    and rel in ["FRIENDS_WITH", "COLLEAGUES"]
                ):
                    bidir = True

                self.add_relationship(
                    source, target, rel, attributes=attr, bidirectional=bidir
                )

    def _generate_osm(
        self,
        place: str = None,
        bbox: tuple = None,
        network_type: str = "drive",
        simplify: bool = True,
        retain_all: bool = False,
    ) -> nx.DiGraph:
        """Fetch an OSM network via OSMFetcher"""
        G = OSMGraphFetcher.fetch_network(
            place=place,
            bbox=bbox,
            network_type=network_type,
            simplify=simplify,
            retain_all=retain_all,
        )
        self.G = G
        return G

    def _generate_flights(
        self,
        country: str = "United States",
        year: int = None,
        month: int = None,
        date_range: tuple = None,
    ):
        """
        Fetch flights, airport, and airline via FlightFetcher
        """
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
        return G
        # Inform users of which span was downloaded
        if date_range:
            start, end = date_range
            logger.info(f"Flight data covers {start} -> {end}")

        else:
            logger.info(f"Flight data for {year}-{month:02d}")

    def _generate_faker(self, total_nodes=100, total_edges=1000):
        """Generates the complete Social Knowledge Graph."""
        self.generate_nodes(total_nodes=total_nodes)
        self.generate_edges(total_edges=total_edges)
        return self.G

    def generate_graph(
        self,
        source: str = "faker",
        total_nodes: int = 100,
        total_edges: int = 1000,
        place: str = None,
        address: str = None,
        bbox: tuple = None,
        network_type: str = "drive",
        simplify: bool = True,
        retain_all: bool = False,
        dist: float = 1000,
        country: str = "United States",
        year: int = 2024,
        month: int = 1,
        date_range: tuple = None,
    ) -> nx.DiGraph:
        """
        Unified entrypoint: choose 'random' or 'osm'.
        Pass kwargs depending on source.
        """
        if source == "faker":
            return self._generate_faker(total_nodes, total_edges)
        elif source == "osm":
            return self._generate_osm(
                place, address, bbox, network_type, simplify, retain_all, dist
            )
        elif source == "flights":
            return self._generate_flights(country, year, month, date_range)
        else:
            raise ValueError(f"Unknown source '{source}'. Use 'random' or 'osm'.")
