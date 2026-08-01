"""Social Knowledge Graph module.
A multi-domain network connecting entities across social, geographical, and commercial dimensions.
"""

import os
from typing import Any, Dict, Optional, Sequence
import networkx as nx
import random
from faker import Faker
from graphfaker.fetchers.osm import OSMGraphFetcher
from graphfaker.fetchers.flights import FlightGraphFetcher
from graphfaker.logger import logger
from graphfaker.resolve import ResolutionResult, resolve_entities

#: Module-level Faker kept for backwards compatibility only. Generation uses
#: the per-instance `GraphFaker._fake` so that seeding is reproducible without
#: reaching into global state.
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

#: Industries, replacing the `fake.job()` value that used to be stored here. A
#: job title is not an industry, and an organization whose industry was
#: "Chartered accountant" made attribute-based matching meaningless.
INDUSTRY_BY_SUBTYPE = {
    "TechCompany": ["Software", "Semiconductors", "Cloud Infrastructure", "Robotics"],
    "Hospital": ["Healthcare", "Medical Research", "Elder Care"],
    "NGO": ["Humanitarian Aid", "Conservation", "Human Rights", "Education Access"],
    "University": ["Higher Education", "Research"],
    "RetailChain": ["Grocery", "Apparel Retail", "Consumer Electronics", "Home Goods"],
}

#: Relationships a person can only sensibly have once. Nobody was born in four
#: cities, but the previous generator drew every Person-Place edge independently
#: and produced exactly that.
FUNCTIONAL_RELATIONSHIPS = {"LIVES_IN", "BORN_IN", "HEADQUARTERED_IN"}

#: Fraction of Person-Person edges formed by closing a triangle rather than by
#: attaching to a stranger. Triadic closure is what gives social graphs their
#: clustering; uniform attachment has essentially none.
TRIADIC_CLOSURE_RATE = 0.55

#: Probability an attachment ignores degree and picks uniformly. Keeps the tail
#: from running away and guarantees low-degree nodes stay reachable.
UNIFORM_ATTACHMENT_RATE = 0.20

#: Candidates drawn per edge before scoring by affinity. Sampling rather than
#: scoring every node keeps edge formation linear in the number of edges instead
#: of quadratic in the number of nodes.
AFFINITY_SAMPLE_SIZE = 8

#: Chance that candidates are drawn from the source's own community rather than
#: the whole graph. Scoring alone is not enough to produce group structure: with
#: a dozen communities, a global sample of 8 often contains no same-community
#: candidate at all, so there is nothing for the affinity term to prefer.
SAME_COMMUNITY_RATE = 0.75


class _AttachmentPool:
    """Samples nodes with probability rising in their degree.

    Implemented with the standard repeated-entry trick: a node is appended each
    time it gains an edge, so drawing uniformly from the list draws
    proportionally to ``degree + 1``. That keeps sampling O(1) instead of
    rebuilding a weight vector for every edge, which matters because the whole
    point is to run this once per edge.

    Seeding the list with every node once supplies the ``+1``, so a node with no
    edges yet can still be chosen.
    """

    def __init__(self, nodes: Sequence[Any], uniform_rate: float = UNIFORM_ATTACHMENT_RATE):
        self._all = list(nodes)
        self._weighted = list(nodes)
        self._uniform_rate = uniform_rate

    def __bool__(self) -> bool:
        return bool(self._all)

    def record(self, node: Any) -> None:
        self._weighted.append(node)

    def sample(self, rand: random.Random) -> Any:
        if rand.random() < self._uniform_rate:
            return rand.choice(self._all)
        return rand.choice(self._weighted)


class GraphFaker:
    """Generate or load graphs into NetworkX.

    Args:
        seed: Optional integer making synthetic generation reproducible. The
            same seed and the same arguments always produce an identical graph.
            Seeding is per-instance: two `GraphFaker(seed=1)` objects do not
            interfere with each other or with the global `random` module.
    """

    def __init__(self, seed: Optional[int] = None):
        # We'll use a directed graph for directional relationships.
        self.G = nx.DiGraph()
        self.seed = seed
        self._rand = random.Random(seed)
        self._fake = Faker()
        if seed is not None:
            self._fake.seed_instance(seed)

    def reseed(self, seed: Optional[int]) -> None:
        """Reset the random state so the next generation call is reproducible."""
        self.seed = seed
        self._rand = random.Random(seed)
        self._fake = Faker()
        if seed is not None:
            self._fake.seed_instance(seed)

    def generate_nodes(self, total_nodes=100, communities=None):
        """
        Generates nodes split into:
         - People (50%)
         - Places (20%)
         - Organizations (15%)
         - Events (10%)
         - Products (5%)

        Every node is assigned a latent ``community``, and its attributes are
        drawn from that community's distribution rather than independently. This
        is what lets homophily appear during edge formation: without a hidden
        variable shared between attributes and attachment, "like attaches to
        like" has nothing to key on and the graph cannot help but look random.

        Places, organizations, and products also receive a heavy-tailed
        ``prominence``, which edge formation uses to decide popularity. Real
        cities, employers, and products differ in scale by orders of magnitude,
        so drawing them from a uniform range made every one interchangeable.

        Args:
            total_nodes: Total node count, split by the proportions above.
            communities: How many latent groups to create. Defaults to roughly
                one per 25 nodes, bounded to [2, 12].
        """
        counts = {
            "Person": int(total_nodes * 0.50),
            "Place": int(total_nodes * 0.20),
            "Organization": int(total_nodes * 0.15),
            "Event": int(total_nodes * 0.10),
        }
        # Remaining nodes will be Products
        counts["Product"] = total_nodes - sum(counts.values())

        if communities is None:
            communities = max(2, min(12, total_nodes // 25 or 2))
        self.communities = communities

        # Each community gets its own attribute distribution, so that community
        # membership is inferable from attributes and vice versa.
        profiles = {}
        for index in range(communities):
            profiles[index] = {
                "mean_age": self._rand.randint(24, 62),
                "education": self._rand.choices(
                    ["High School", "Bachelor", "Master", "PhD"],
                    weights=self._rand.sample([1, 2, 3, 4], 4),
                    k=1,
                )[0],
                "region": self._fake.city(),
            }
        self._community_profiles = profiles

        def pick_community():
            return self._rand.randrange(communities)

        def prominence():
            """Heavy-tailed scale factor.

            Lognormal rather than uniform: it is the difference between a graph
            where every place is the same size and one with a handful of hubs.
            """
            return round(self._rand.lognormvariate(0.0, 1.1), 4)

        # Places first, so that people can be given a home drawn from their own
        # community's places.
        places_by_community = {index: [] for index in range(communities)}
        for i in range(counts["Place"]):
            node_id = f"place_{i}"
            subtype = self._rand.choice(PLACE_SUBTYPES)
            community = pick_community()
            scale = prominence()
            self.G.add_node(
                node_id,
                type="Place",
                name=self._fake.city(),
                place_type=subtype,
                # Population now tracks prominence, so the biggest cities are
                # also the most connected ones.
                population=int(8_000 * (1 + scale) ** 2.2) + self._rand.randint(0, 5_000),
                coordinates=(self._fake.latitude(), self._fake.longitude()),
                community=community,
                prominence=scale,
            )
            places_by_community[community].append(node_id)

        for i in range(counts["Person"]):
            node_id = f"person_{i}"
            subtype = self._rand.choice(PERSON_SUBTYPES)
            community = pick_community()
            profile = profiles[community]
            # Age clusters around the community mean instead of spanning the
            # whole adult range uniformly, which is what produces measurable age
            # homophily across edges.
            age = int(min(80, max(18, self._rand.gauss(profile["mean_age"], 9))))
            # Small graphs can leave a community with no places at all, so fall
            # back to any place and then to the empty string. Storing None here
            # is not an option: GraphML cannot serialise it, which broke export
            # for any graph under about 40 nodes.
            local_places = places_by_community[community]
            all_places = [
                node
                for members in places_by_community.values()
                for node in members
            ]
            home = self._rand.choice(local_places or all_places) if all_places else ""
            self.G.add_node(
                node_id,
                type="Person",
                name=self._fake.name(),
                age=age,
                occupation=self._fake.job(),
                email=self._fake.email(),
                education_level=(
                    profile["education"]
                    if self._rand.random() < 0.55
                    else self._rand.choice(
                        ["High School", "Bachelor", "Master", "PhD"]
                    )
                ),
                skills=", ".join(self._fake.words(nb=3)),
                subtype=subtype,
                community=community,
                home_place=home,
            )

        for i in range(counts["Organization"]):
            node_id = f"org_{i}"
            subtype = self._rand.choice(ORG_SUBTYPES)
            community = pick_community()
            scale = prominence()
            self.G.add_node(
                node_id,
                type="Organization",
                name=self._fake.company(),
                # An industry, not a job title.
                industry=self._rand.choice(INDUSTRY_BY_SUBTYPE[subtype]),
                revenue=round(2.5e5 * (1 + scale) ** 3, 2),
                # Filled in after edges exist, from the actual WORKS_AT count.
                employee_count=0,
                subtype=subtype,
                community=community,
                prominence=scale,
            )

        for i in range(counts["Event"]):
            node_id = f"event_{i}"
            subtype = self._rand.choice(EVENT_SUBTYPES)
            community = pick_community()
            scale = prominence()
            self.G.add_node(
                node_id,
                type="Event",
                name=self._fake.catch_phrase(),
                event_type=subtype,
                start_date=self._fake.date(),
                duration=self._rand.randint(1, 5),
                community=community,
                prominence=scale,
            )  # days

        for i in range(counts["Product"]):
            node_id = f"product_{i}"
            subtype = self._rand.choice(PRODUCT_SUBTYPES)
            community = pick_community()
            scale = prominence()
            self.G.add_node(
                node_id,
                type="Product",
                name=self._fake.word().capitalize(),
                category=subtype,
                price=round(12 * (1 + scale) ** 2.5, 2),
                release_date=self._fake.date(),
                community=community,
                prominence=scale,
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

    def _affinity(self, source: str, target: str) -> float:
        """How plausible an edge between two nodes is, higher being better.

        Only ever used to rank a small sample of candidates, so the scale is
        arbitrary; what matters is that shared community and similar attributes
        win. This is the homophily term.
        """
        a = self.G.nodes[source]
        b = self.G.nodes[target]
        score = 0.0
        if a.get("community") == b.get("community"):
            score += 1.0
        # Prominent targets are attractive regardless of similarity: a famous
        # city draws visitors from everywhere.
        score += 0.45 * float(b.get("prominence", 0.0))
        age_a, age_b = a.get("age"), b.get("age")
        if isinstance(age_a, int) and isinstance(age_b, int):
            score += 0.8 * max(0.0, 1.0 - abs(age_a - age_b) / 35.0)
        if a.get("education_level") and a.get("education_level") == b.get(
            "education_level"
        ):
            score += 0.25
        return score

    def _choose_affine(
        self,
        pool: "_AttachmentPool",
        source: str,
        exclude=(),
        local_pool: "_AttachmentPool | None" = None,
    ) -> Any:
        """Draw several candidates by degree preference, keep the most plausible.

        Sampling then ranking gives homophily *and* a heavy tail while staying
        linear in edges. Scoring every node for every edge would be quadratic and
        is the reason a naive implementation reaches for uniform choice instead.

        When a community-local pool is supplied, most candidates are drawn from
        it. That is what actually creates group structure — relying on the
        affinity score alone leaves modularity near zero, because a global sample
        usually contains no same-community candidate to prefer.
        """
        best = None
        best_score = float("-inf")
        for _ in range(AFFINITY_SAMPLE_SIZE):
            source_pool = pool
            if local_pool and self._rand.random() < SAME_COMMUNITY_RATE:
                source_pool = local_pool
            candidate = source_pool.sample(self._rand)
            if candidate == source or candidate in exclude:
                continue
            score = self._affinity(source, candidate)
            if score > best_score:
                best, best_score = candidate, score
        return best

    def generate_edges(self, total_edges=1000, topology="realistic"):
        """
        Generate edges based on the EDGE_DISTRIBUTION probabilities.
        The number of edges for each relationship category is determined by the weight.

        Args:
            total_edges: Approximate number of edges to create.
            topology: ``"realistic"`` (default) forms edges by preferential
                attachment, triadic closure, and homophily, producing a
                heavy-tailed degree distribution with clustering and community
                structure. ``"uniform"`` restores the pre-0.5 behaviour of
                drawing both endpoints uniformly at random, which yields an
                Erdos-Renyi graph. Keep it only for comparison — see
                :func:`graphfaker.metrics.compare_topology`.
        """
        if topology not in ("realistic", "uniform"):
            raise ValueError(
                f"topology must be 'realistic' or 'uniform', got {topology!r}"
            )

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

        pools = {
            node_type: _AttachmentPool(nodes)
            for node_type, nodes in nodes_by_type.items()
            if nodes
        }
        # One pool per (type, community), so candidates can be drawn from the
        # source's own group.
        local_pools: dict = {}
        for node_type, nodes in nodes_by_type.items():
            grouped: dict = {}
            for node in nodes:
                grouped.setdefault(self.G.nodes[node].get("community"), []).append(node)
            for community, members in grouped.items():
                local_pools[(node_type, community)] = _AttachmentPool(members)
        # Tracks which functional relationships a node already has, so that
        # LIVES_IN and BORN_IN stay singular.
        assigned: dict = {}
        neighbours: dict = {node: set() for node in self.G.nodes()}

        for (src_type, tgt_type), (possible_rels, weight) in EDGE_DISTRIBUTION.items():
            num_edges = int(total_edges * weight)
            src_nodes = nodes_by_type.get(src_type, [])
            tgt_nodes = nodes_by_type.get(tgt_type, [])
            if not src_nodes or not tgt_nodes:
                continue

            # Pure preferential attachment leaves a large share of nodes never
            # selected at all — measured at 24% isolated on a 600-node graph,
            # against 0.5% for uniform attachment. Real graphs have a giant
            # component, so the first pass over each category walks every source
            # node once before preference takes over.
            coverage = list(src_nodes)
            self._rand.shuffle(coverage)

            # Budget counts edges actually added, not loop iterations. A
            # bidirectional FRIENDS_WITH adds two directed edges while a skipped
            # functional relationship adds none, so counting iterations made the
            # result overshoot by ~14% or undershoot by ~12% depending on the
            # mix. `total_edges` should mean what `number_of_edges()` reports.
            added = 0
            index = 0
            attempts = 0
            while added < num_edges and attempts < num_edges * 8 + 32:
                attempts += 1
                index += 1
                edges_before = self.G.number_of_edges()
                rel = self._rand.choice(possible_rels)

                if topology == "uniform":
                    source = self._rand.choice(src_nodes)
                    target = self._rand.choice(tgt_nodes)
                else:
                    source = (
                        coverage[index - 1]
                        if index <= len(coverage)
                        else pools[src_type].sample(self._rand)
                    )
                    local = local_pools.get(
                        (tgt_type, self.G.nodes[source].get("community"))
                    )

                    # A person already living somewhere does not acquire a
                    # second home; reuse the assignment instead of inventing one.
                    if rel in FUNCTIONAL_RELATIONSHIPS:
                        key = (source, rel)
                        if key in assigned:
                            continue
                        if rel == "LIVES_IN":
                            target = self.G.nodes[source].get("home_place")
                        else:
                            target = self._choose_affine(pools[tgt_type], source, local_pool=local)
                        if target is None:
                            continue
                        assigned[key] = target
                    elif (
                        src_type == "Person"
                        and tgt_type == "Person"
                        and neighbours[source]
                        and self._rand.random() < TRIADIC_CLOSURE_RATE
                    ):
                        # Close a triangle: befriend a friend of a friend. This
                        # is the entire source of clustering; picking a stranger
                        # every time is why the old generator had none.
                        friend = self._rand.choice(tuple(neighbours[source]))
                        candidates = [
                            candidate
                            for candidate in neighbours.get(friend, ())
                            if candidate != source
                            and candidate not in neighbours[source]
                            and self.G.nodes[candidate].get("type") == "Person"
                        ]
                        target = (
                            self._rand.choice(candidates)
                            if candidates
                            else self._choose_affine(
                                pools[tgt_type], source, local_pool=local
                            )
                        )
                    else:
                        target = self._choose_affine(pools[tgt_type], source, local_pool=local)

                if target is None or source == target:
                    continue

                attr = {}
                # Add additional attributes for specific relationships
                if rel == "VISITED":
                    attr["visit_count"] = self._rand.randint(1, 20)
                elif rel == "WORKS_AT":
                    attr["position"] = self._fake.job()
                elif rel == "PURCHASED":
                    attr["date"] = self._fake.date()
                    attr["amount"] = round(self._rand.uniform(1, 500), 2)
                elif rel == "REVIEWED":
                    attr["rating"] = self._rand.randint(1, 5)

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
                added += self.G.number_of_edges() - edges_before

                if topology == "realistic":
                    pools[src_type].record(source)
                    pools[tgt_type].record(target)
                    neighbours[source].add(target)
                    neighbours[target].add(source)

        if topology == "realistic":
            self._top_up_edges(total_edges, nodes_by_type, pools, local_pools, neighbours)
            self._reconcile_attributes()

    def _top_up_edges(
        self,
        total_edges: int,
        nodes_by_type: dict,
        pools: dict,
        local_pools: dict,
        neighbours: dict,
    ) -> None:
        """Attach leftover nodes and make up the edge shortfall.

        Two problems are fixed here, both consequences of honest edge formation
        rather than bugs in it:

        1. **Isolated nodes.** Only source types get a coverage pass, so types
           that appear mostly as targets — places, events, products — can be
           missed entirely. A graph where an eighth of the nodes are unreachable
           does not resemble anything real.
        2. **Edge shortfall.** Functional relationships are skipped once a node
           already has one, so a run asks for 2400 edges and produces about 2000.
           Silently returning 15% fewer edges than requested is a bad contract.

        Only non-functional relationships are used, so topping up cannot give
        anyone a second birthplace.
        """
        # Relationship options that may be repeated, per type pair.
        repeatable = []
        for (src_type, tgt_type), (rels, weight) in EDGE_DISTRIBUTION.items():
            usable = [rel for rel in rels if rel not in FUNCTIONAL_RELATIONSHIPS]
            if usable and nodes_by_type.get(src_type) and nodes_by_type.get(tgt_type):
                repeatable.append((src_type, tgt_type, usable, weight))
        if not repeatable:
            return

        # Which type pair can reach a given node as a target.
        reachable_as_target: dict = {}
        for src_type, tgt_type, usable, _ in repeatable:
            reachable_as_target.setdefault(tgt_type, []).append((src_type, usable))

        def connect(source, target, rel) -> bool:
            if source is None or target is None or source == target:
                return False
            if self.G.has_edge(source, target):
                return False
            self.add_relationship(source, target, rel)
            pools[self.G.nodes[source]["type"]].record(source)
            pools[self.G.nodes[target]["type"]].record(target)
            neighbours[source].add(target)
            neighbours[target].add(source)
            return True

        for node in list(self.G.nodes()):
            if self.G.degree(node) > 0:
                continue
            options = reachable_as_target.get(self.G.nodes[node].get("type"))
            if not options:
                continue
            src_type, usable = self._rand.choice(options)
            community = self.G.nodes[node].get("community")
            pool = local_pools.get((src_type, community)) or pools[src_type]
            connect(pool.sample(self._rand), node, self._rand.choice(usable))

        weights = [weight for _, _, _, weight in repeatable]
        attempts = 0
        limit = max(200, 12 * total_edges)
        while self.G.number_of_edges() < total_edges and attempts < limit:
            attempts += 1
            src_type, tgt_type, usable, _ = self._rand.choices(repeatable, weights)[0]
            source = pools[src_type].sample(self._rand)
            local = local_pools.get((tgt_type, self.G.nodes[source].get("community")))
            target = self._choose_affine(pools[tgt_type], source, local_pool=local)
            rel = self._rand.choice(usable)
            if not connect(source, target, rel):
                # Affinity concentrates candidates, so late in the fill the same
                # plausible pairs keep coming back already connected. One uniform
                # retry finds a fresh pair and stops the budget from stalling
                # ~12% short.
                connect(source, self._rand.choice(nodes_by_type[tgt_type]), rel)

        logger.debug(
            "generate_edges: %d edges after top-up (%d requested, %d attempts)",
            self.G.number_of_edges(),
            total_edges,
            attempts,
        )

    def _reconcile_attributes(self) -> None:
        """Make scale attributes agree with the structure that was built.

        An organization whose ``employee_count`` is unrelated to how many people
        actually work there is a trap for anyone using the graph to test
        aggregation or validation logic, because the attribute and the topology
        tell different stories.
        """
        for node, data in self.G.nodes(data=True):
            if data.get("type") != "Organization":
                continue
            employees = sum(
                1
                for source, _, edge in self.G.in_edges(node, data=True)
                if edge.get("relationship") == "WORKS_AT"
            )
            # Employees present in the graph are a sample, not the whole
            # workforce, so scale up by prominence rather than reporting the
            # raw count.
            data["employee_count"] = max(
                employees, int(employees * (12 + 40 * data.get("prominence", 0.0))) or 1
            )

    def _generate_osm(
        self,
        place: Optional[str] = None,
        address: Optional[str] = None,
        bbox: Optional[tuple] = None,
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
        year: Optional[int] = None,
        month: Optional[int] = None,
        date_range: Optional[tuple] = None,
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


    def _generate_faker(
        self, total_nodes=100, total_edges=1000, topology="realistic", communities=None
    ):
        """Generates the complete Social Knowledge Graph."""
        self.G = nx.DiGraph()  # Reset the graph to a new instance
        self.generate_nodes(total_nodes=total_nodes, communities=communities)
        self.generate_edges(total_edges=total_edges, topology=topology)
        return self.G

    def generate_graph(
        self,
        source: str = "faker",
        total_nodes: int = 100,
        total_edges: int = 1000,
        place: Optional[str] = None,
        address: Optional[str] = None,
        bbox: Optional[tuple] = None,
        network_type: str = "drive",
        simplify: bool = True,
        retain_all: bool = False,
        dist: float = 1000,
        country: str = "United States",
        year: int = 2024,
        month: int = 1,
        date_range: Optional[tuple] = None,
        seed: Optional[int] = None,
        topology: str = "realistic",
        communities: Optional[int] = None,
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
        G: Optional[nx.Graph] = None,
        on: Sequence[str] = ("name",),
        threshold: float = 0.85,
        structural_weight: float = 0.5,
        **kwargs: Any,
    ) -> ResolutionResult:
        """Find nodes that look like duplicates of the same entity.

        Scores candidate pairs on attribute similarity *and* neighbourhood
        overlap — the signal a tabular record-linkage tool cannot see. Nothing
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

    def export_graph(self, G: nx.Graph = None, source: str = None, path: str = "graph.graphml"):
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
            except ImportError:
                raise ImportError("osmnx is required to export OSM graphs.")
        else:
            nx.write_graphml(G, abs_path)

        print(f"✅ Graph exported to: {abs_path}")
