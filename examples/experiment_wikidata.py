import marimo
from graphfaker.logger import logger

__generated_with = "0.13.1"
app = marimo.App()


@app.cell
def _():
    import pandas as pd
    import SPARQLWrapper as sw

    return (pd,)


@app.cell
def _():
    import requests

    return (requests,)


@app.cell
def _():
    import networkx as nx
    import json
    import random
    from faker import Faker

    fake = Faker()

    WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
    OVERPASS_API_URL = "http://overpass-api.de/api/interpreter"
    return Faker, WIKIDATA_SPARQL_URL, fake, nx, random


@app.cell
def _(WIKIDATA_SPARQL_URL, requests):
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

    return (run_sparql_query,)


@app.cell
def _(nx, run_sparql_query):
    def fetch_ceos_and_companies():
        """Fetch CEOs and their companies from Wikidata."""
        query = """
        SELECT ?ceo ?ceoLabel ?company ?companyLabel WHERE {
          ?company wdt:P169 ?ceo.
          SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
        }
        LIMIT 20
        """
        data = run_sparql_query(query)
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

    return (fetch_ceos_and_companies,)


@app.cell
def _():
    import matplotlib.pyplot as plt

    return


@app.cell
def _(fetch_ceos_and_companies):
    ceos_and_companies = fetch_ceos_and_companies()
    return (ceos_and_companies,)


@app.cell
def _(ceos_and_companies, nx):
    nx.draw(ceos_and_companies, with_labels=False, font_weight="bold")
    return


@app.cell
def _(fake, nx, random):
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
                    p,
                    org,
                    relationship=random.choice(["works_at", "consults_for", "owns"]),
                )

        @staticmethod
        def connect_people_to_places(G, people_nodes, place_nodes):
            for p in people_nodes:
                place = random.choice(place_nodes)
                G.add_edge(
                    p, place, relationship=random.choice(["lives_in", "born_in"])
                )

    return (Graphfaker,)


@app.cell
def _(Graphfaker):
    G_people = Graphfaker.generate_people(10)
    G_fake_places = Graphfaker.generate_places(5)
    G_fake_orgs = Graphfaker.generate_organizations(5)

    Graphfaker.connect_people_to_organizations(
        G_people, list(G_people.nodes), list(G_fake_orgs.nodes)
    )
    Graphfaker.connect_people_to_places(
        G_people, list(G_people.nodes), list(G_fake_places.nodes)
    )
    return (G_people,)


@app.cell
def _(G_people):
    G_people.nodes()
    return


@app.cell
def _(G_people, nx):
    nx.draw(G_people, with_labels=True, font_weight="bold")
    return


@app.cell
def _(ceos_and_companies):
    logger.info(ceos_and_companies.nodes(data=True))
    return


@app.cell
def _(WikidataFetcher, nx):
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

    return


@app.cell
def _():
    query = """
    SELECT ?city ?cityLabel ?location ?locationLabel ?founding_date
    WHERE {
      ?city wdt:P31/wdt:P279* wd:Q515.
      ?city wdt:P17 wd:Q30.
      ?city wdt:P625 ?location.
      ?city wdt:P571 ?founding_date.
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    """
    return (query,)


@app.cell
def _(pd):
    import sys
    from typing import List, Dict
    from SPARQLWrapper import SPARQLWrapper, JSON

    class WikiDataQueryResults:
        """
        A class that can be used to query data from Wikidata using SPARQL and return the results as a Pandas DataFrame or a list
        of values for a specific key.
        """

        def __init__(self, query: str):
            """
            Initializes the WikiDataQueryResults object with a SPARQL query string.
            :param query: A SPARQL query string.
            """
            self.user_agent = "WDQS-example Python/%s.%s" % (
                sys.version_info[0],
                sys.version_info[1],
            )
            self.endpoint_url = "https://query.wikidata.org/sparql"
            self.sparql = SPARQLWrapper(self.endpoint_url, agent=self.user_agent)
            self.sparql.setQuery(query)
            self.sparql.setReturnFormat(JSON)

        def __transform2dicts(self, results: List[Dict]) -> List[Dict]:
            """
            Helper function to transform SPARQL query results into a list of dictionaries.
            :param results: A list of query results returned by SPARQLWrapper.
            :return: A list of dictionaries, where each dictionary represents a result row and has keys corresponding to the
            variables in the SPARQL SELECT clause.
            """
            new_results = []
            for result in results:
                new_result = {}
                for key in result:
                    new_result[key] = result[key]["value"]
                new_results.append(new_result)
            return new_results

        def _load(self) -> List[Dict]:
            """
            Helper function that loads the data from Wikidata using the SPARQLWrapper library, and transforms the results into
            a list of dictionaries.
            :return: A list of dictionaries, where each dictionary represents a result row and has keys corresponding to the
            variables in the SPARQL SELECT clause.
            """
            results = self.sparql.queryAndConvert()["results"]["bindings"]
            results = self.__transform2dicts(results)
            return results

        def load_as_dataframe(self) -> pd.DataFrame:
            """
            Executes the SPARQL query and returns the results as a Pandas DataFrame.
            :return: A Pandas DataFrame representing the query results.
            """
            results = self._load()
            return pd.DataFrame.from_dict(results)

    return (WikiDataQueryResults,)


@app.cell
def _(WikiDataQueryResults, query):
    data_extracter = WikiDataQueryResults(query)
    return (data_extracter,)


@app.cell
def _(data_extracter):
    data_extracter
    return


@app.cell
def _(data_extracter):
    df = data_extracter.load_as_dataframe()
    logger.info(df.head())
    return


@app.cell
def _(Faker):
    fake_1 = Faker()
    return (fake_1,)


@app.cell
def _(fake_1):
    fake_1.name()
    return


@app.cell
def _(fake_1):
    fake_1.company()
    return


@app.cell
def _():
    query_1 = '\n        SELECT ?ceo ?ceoLabel ?company ?companyLabel WHERE {\n          ?company wdt:P169 ?ceo.\n          SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }\n        }\n        LIMIT 20\n        '
    return


if __name__ == "__main__":
    app.run()
