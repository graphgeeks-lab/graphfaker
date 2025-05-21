import networkx as nx
import requests
import json
import random
from faker import Faker
from graphfaker.logger import logger

fake = Faker()

WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
OVERPASS_API_URL = "http://overpass-api.de/api/interpreter"


class WikidataFetcher:
    @staticmethod
    def run_sparql_query(query):
        """Fetch data from Wikidata using SPARQL."""
        headers = {"Accept": "application/json"}
        response = requests.get(
            WIKIDATA_SPARQL_URL,
            params={"query": query, "format": "json"},
            headers=headers,
        )
        if response.status_code == 200:
            return response.json()["results"]["bindings"]
        else:
            logger.error(
                "SPARQL Query Failed! Error: %s", response.status_code, exc_info=True
            )

            return []

    @staticmethod
    def fetch_ceos_and_companies():
        """Fetch CEOs and their companies from Wikidata."""
        query = """
        SELECT ?ceo ?ceoLabel ?company ?companyLabel WHERE {
          ?company wdt:P169 ?ceo.
          SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
        }
        LIMIT 20
        """
        data = WikidataFetcher.run_sparql_query(query)
        G = nx.DiGraph()

        for item in data:
            ceo_id = item["ceo"]["value"].split("/")[-1]
            company_id = item["company"]["value"].split("/")[-1]
            ceo_name = item["ceoLabel"]["value"]
            company_name = item["companyLabel"]["value"]

            G.add_node(ceo_id, type="Person", name=ceo_name)
            G.add_node(company_id, type="Organization", name=company_name)
            G.add_edge(ceo_id, company_id, relationship="CEO_of")

        return G

    @staticmethod
    def fetch_places():
        """Fetch major cities from Wikidata."""
        query = """
        SELECT ?city ?cityLabel WHERE {
          ?city wdt:P31 wd:Q515.
          SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
        }
        LIMIT 20
        """
        data = WikidataFetcher.run_sparql_query(query)
        G = nx.DiGraph()

        for item in data:
            city_id = item["city"]["value"].split("/")[-1]
            city_name = item["cityLabel"]["value"]

            G.add_node(city_id, type="Place", name=city_name)

        return G

    @staticmethod
    def fetch_organizations():
        """Fetch major organizations from Wikidata."""
        query = """
        SELECT ?org ?orgLabel WHERE {
          ?org wdt:P31 wd:Q43229.
          SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
        }
        LIMIT 20
        """
        data = WikidataFetcher.run_sparql_query(query)
        G = nx.DiGraph()

        for item in data:
            org_id = item["org"]["value"].split("/")[-1]
            org_name = item["orgLabel"]["value"]

            G.add_node(org_id, type="Organization", name=org_name)

        return G


class OverpassFetcher:
    @staticmethod
    def fetch_transportation_network(
        bbox="-0.489,51.28,0.236,51.686",
    ):  # London Bounding Box
        """Fetch transportation network from OpenStreetMap using Overpass API."""
        query = f"""
        [out:json];
        (
          way["highway"]({bbox});
          relation["route"]({bbox});
        );
        out body;
        >;
        out skel qt;
        """
        response = requests.get(OVERPASS_API_URL, params={"data": query})
        if response.status_code == 200:
            data = response.json()["elements"]
            G = nx.Graph()
            for elem in data:
                if elem["type"] == "node":
                    G.add_node(elem["id"], lat=elem["lat"], lon=elem["lon"])
                elif elem["type"] == "way":
                    nodes = elem["nodes"]
                    for i in range(len(nodes) - 1):
                        G.add_edge(nodes[i], nodes[i + 1], type="road")
            return G
        else:
            logger.error(
                "Overpass Query Failed! Error: %s", response.status_code, exc_info=True
            )

            return None


class Graphfaker:
    @staticmethod
    def generate_people(num=10):
        G = nx.Graph()
        for i in range(num):
            pid = f"person_{i}"
            G.add_node(
                pid,
                type="Person",
                name=fake.name(),
                email=fake.email(),
                age=random.randint(18, 80),
            )
        return G

    @staticmethod
    def generate_places(num=5):
        G = nx.Graph()
        for i in range(num):
            cid = f"city_{i}"
            G.add_node(cid, type="Place", name=fake.city(), country=fake.country())
        return G

    @staticmethod
    def generate_organizations(num=5):
        G = nx.Graph()
        for i in range(num):
            oid = f"org_{i}"
            G.add_node(
                oid, type="Organization", name=fake.company(), industry=fake.job()
            )
        return G

    @staticmethod
    def connect_people_to_organizations(G, people_nodes, org_nodes):
        for p in people_nodes:
            org = random.choice(org_nodes)
            G.add_edge(
                p, org, relationship=random.choice(["works_at", "consults_for", "owns"])
            )

    @staticmethod
    def connect_people_to_places(G, people_nodes, place_nodes):
        for p in people_nodes:
            place = random.choice(place_nodes)
            G.add_edge(p, place, relationship=random.choice(["lives_in", "born_in"]))


# Example Usage
if __name__ == "__main__":
    G_ceos = WikidataFetcher.fetch_ceos_and_companies()
    G_places = WikidataFetcher.fetch_places()
    G_orgs = WikidataFetcher.fetch_organizations()
    G_transport = OverpassFetcher.fetch_transportation_network()

    G_people = Graphfaker.generate_people(10)
    G_fake_places = Graphfaker.generate_places(5)
    G_fake_orgs = Graphfaker.generate_organizations(5)

    Graphfaker.connect_people_to_organizations(
        G_people, list(G_people.nodes), list(G_fake_orgs.nodes)
    )
    Graphfaker.connect_people_to_places(
        G_people, list(G_people.nodes), list(G_fake_places.nodes)
    )

    logger.info(
        "Graph Summary:\n"
        f"  CEOs and Companies: {len(G_ceos.nodes())} nodes.\n"
        f"  Places: {len(G_places.nodes())} nodes.\n"
        f"  Organizations: {len(G_orgs.nodes())} nodes.\n"
        f"  Transportation Network: {len(G_transport.nodes()) if G_transport else 'Failed to fetch'} nodes.\n"
        f"  Synthetic People: {len(G_people.nodes())} nodes."
    )
