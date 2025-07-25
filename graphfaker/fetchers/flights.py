# graphfaker/fetchers/flights.py
"""
Flight network fetcher for GraphFaker.

Provides methods to fetch airlines, airports, and flight performance data,
then build a unified NetworkX graph of Airlines, Airports, and Flights.

Node Types & Key Attributes:
  - Airline: carrier (IATA code), airline_name
  - Airport: faa code, name, city, country, coordinates
  - Flight: flight identifier, cancelled (bool), delayed (bool), date

Relationships:
  - (Flight) -[OPERATED_BY]-> (Airline)
  - (Flight) -[DEPARTS_FROM]-> (Airport)
  - (Flight) -[ARRIVES_AT]-> (Airport)

Usage:
    from graphfaker.fetchers.flights import FlightGraphFetcher
    airlines_df = FlightGraphFetcher.fetch_airlines()
    airports_df = FlightGraphFetcher.fetch_airports(country="United States")
    flights_df  = FlightGraphFetcher.fetch_flights(year=2024, month=1)
    G = FlightGraphFetcher.build_graph(airlines_df, airports_df, flights_df)
"""
import os
import io
import zipfile
from datetime import datetime, timedelta
from typing import Tuple, Optional
from io import StringIO
from graphfaker.logger import logger
import requests
import pandas as pd
from tqdm.auto import tqdm
import networkx as nx


# suppress only the single warning from unverified HTTPS
import urllib3
from urllib3.exceptions import InsecureRequestWarning

urllib3.disable_warnings(InsecureRequestWarning)

# Data source URLs
AIRLINE_LOOKUP_URL = (
    "https://transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_haVdhR_PNeeVRef"
)
AIRPORTS_URL = (
    "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat"
)
AIRPORT_COLS = [
    "id",
    "name",
    "city",
    "country",
    "faa",
    "icao",
    "lat",
    "lon",
    "alt",
    "tz",
    "dst",
    "tzone",
    "type",
    "source",
]

# Column mapping for BTS flight performance
COLUMN_MAP = {
    "Year": "year",
    "Month": "month",
    "DayofMonth": "day",
    "DepDelay": "dep_delay",
    "ArrDelay": "arr_delay",
    "Reporting_Airline": "carrier",
    "Flight_Number_Reporting_Airline": "flight",
    "Origin": "origin",
    "Dest": "dest",
    "Tail_Number": "tail_number",
}


class FlightGraphFetcher:
    """
    FlightGraphFetcher provides static methods to fetch and transform flight-related data
    into a NetworkX graph.

    Methods:
        fetch_airlines() -> pd.DataFrame
            Download the BTS airlines lookup and return a DataFrame with columns:
                - carrier (IATA code)
                - airline_name

        fetch_airports(country: str = None, keep_only_with_faa: bool = True) -> pd.DataFrame
            Download and tidy the OpenFlights airports dataset, optionally filter by country
            and FAA code. Returns columns:
                - faa, name, city, country, lat, lon

        fetch_flights(year: int = None, month: int = None,
                      date_range: Optional[Tuple[Tuple[int,int], Tuple[int,int]]] = None)
            Fetch BTS on-time performance data for a single month or date range. Returns
            DataFrame with columns:
                - year, month, day, carrier, flight, origin, dest, cancelled, delayed

        build_graph(airlines_df: pd.DataFrame,
                    airports_df: pd.DataFrame,
                    flights_df: pd.DataFrame) -> nx.DiGraph
            Construct and return a directed graph with nodes and edges:
                • Airline nodes from airlines_df
                • Airport nodes from airports_df
                • Flight nodes from flights_df, with 'cancelled' and 'delayed' attributes
                • Relationships: OPERATED_BY, DEPARTS_FROM, ARRIVES_AT
    """

    @staticmethod
    def fetch_airlines() -> pd.DataFrame:
        """
        Download and tidy BTS airlines lookup table.
        Source:
            airline -> https://transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_haVdhR_PNeeVRef

        Returns:
            pd.DataFrame with columns ['carrier', 'airline_name']
        Raises:
            HTTPError if download fails.
        """
        logger.info("Fetching airlines lookup from BTS…")
        resp = requests.get(AIRLINE_LOOKUP_URL, verify=False)
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text))
        return df.rename(columns={"Code": "carrier", "Description": "airline_name"})

    @staticmethod
    def fetch_airports(
        country: Optional[str] = "United States", keep_only_with_faa: bool = True
    ) -> pd.DataFrame:
        """
        Download and tidy the OpenFlights airports dataset:
        Source:
            airports -> https://openflights.org/data.php

        Args:
            country: filter airports by country name (optional).
            keep_only_with_faa: drop records without FAA code if True.

        Returns:
            pd.DataFrame with columns ['faa','name','city','country','lat','lon']
        """
        logger.info("Fetching airports dataset from OpenFlights…")
        df = pd.read_csv(
            AIRPORTS_URL,
            header=None,
            names=AIRPORT_COLS,
            na_values=["", "NA", r"\N"],
            keep_default_na=True,
            dtype={
                "id": int,
                "name": str,
                "city": str,
                "country": str,
                "faa": str,
                "icao": str,
                "lat": float,
                "lon": float,
                "alt": float,
                "tz": float,
                "dst": str,
                "tzone": str,
                "type": str,
                "source": str,
            },
        )

        if country:
            df = df[df["country"] == country]
        if keep_only_with_faa:
            df = df[df["faa"].notna() & (df["faa"] != "")]

        df = df.sort_values("id").drop_duplicates(subset="faa", keep="first")
        return df[["faa", "name", "city", "country", "lat", "lon"]].reset_index(
            drop=True
        )

    @staticmethod
    def _download_extract_csv(url: str) -> io.BytesIO:
        """Stream-download a BTS zip file and return CSV data as BytesIO."""
        resp = requests.get(url, stream=True, verify=False)
        resp.raise_for_status()
        buf = io.BytesIO()
        total = int(resp.headers.get("content-length", 0))
        with tqdm.wrapattr(
            resp.raw, "read", total=total, desc=os.path.basename(url), leave=False
        ) as r:
            buf.write(r.read())
        buf.seek(0)
        with zipfile.ZipFile(buf) as z:
            name = next(f for f in z.namelist() if f.lower().endswith(".csv"))
            return io.BytesIO(z.read(name))

    @staticmethod
    def fetch_flights(
        year: Optional[int] = None,
        month: Optional[int] = None,
        date_range: Optional[Tuple[Tuple[int, int], Tuple[int, int]]] = None,
    ) -> pd.DataFrame:
        """
        Fetch BTS on-time performance data for a given month or a range of months.
        source:
        - https://www.transtats.bts.gov/TableInfo.asp?gnoyr_VQ=FGJ&QO_fu146_anzr=b0-gvzr&V0s1_b0yB=D


        Args:
            year: calendar year for single-month fetch.
            month: month (1-12) for single-month fetch.
            date_range: ((year0, month0), (year1, month1)) to fetch multiple months.

        Returns:
            pd.DataFrame with columns:
                ['year','month','day','carrier','flight','origin','dest',
                 'cancelled','delayed']

        Raises:
            ValueError if neither valid year/month nor date_range provided.
        """
        logger.info(
            f"Fetching flight performance data for {year}-{month:02d} "
            f"or date range {date_range}…"
        )

        def load_month(y, m):
            url = f"https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{y}_{m}.zip"
            buf = FlightGraphFetcher._download_extract_csv(url)
            df = pd.read_csv(buf, usecols=list(COLUMN_MAP.keys()))
            return df.rename(columns=COLUMN_MAP)

        if date_range:
            (y0, m0), (y1, m1) = date_range
            cur, last = datetime(y0, m0, 1), datetime(y1, m1, 1)
            dfs = []
            while cur <= last:
                dfs.append(load_month(cur.year, cur.month))
                nxt = cur + timedelta(days=32)
                cur = datetime(nxt.year, nxt.month, 1)
            df = pd.concat(dfs, ignore_index=True)
        else:
            if year is None or month is None:
                raise ValueError("Provide year & month or date_range.")
            df = load_month(year, month)
        # derive flags
        df["cancelled"] = df["dep_delay"].isna()
        df["delayed"] = df["arr_delay"] > 15

        return df

    @staticmethod
    def build_graph(
        airlines_df: pd.DataFrame, airports_df: pd.DataFrame, flights_df: pd.DataFrame
    ) -> nx.DiGraph:
        import time

        t0 = time.time()
        G = nx.DiGraph()

        # 1) Airlines
        logger.info(f"Adding {len(airlines_df)} airlines…")

        for _, r in airlines_df.iterrows():
            G.add_node(r["carrier"], type="Airline", name=r["airline_name"])

        # 2) Airports + City relationships
        logger.info(f"Adding {len(airports_df)} airports + city nodes…")

        for _, r in airports_df.iterrows():
            code = r["faa"]
            city = r["city"]

            # Add Airport node
            G.add_node(
                code,
                type="Airport",
                name=r["name"],
                country=r["country"],
                coordinates=(r["lat"], r["lon"]),
            )

            # Add City node if missing
            if not G.has_node(city):
                G.add_node(city, type="City", name=city)

            # Connect Airport -> City
            G.add_edge(code, city, relationship="LOCATED_IN")

        # 3) Flights + edges
        logger.info(f"Adding {len(flights_df)} flights + edges…")

        for _, r in flights_df.iterrows():
            fn = f"{r['carrier']}{r['flight']}_{r['origin']}_{r['dest']}_{r['year']}-{r['month']:02d}-{r['day']:02d}"

            # Check if required nodes exist first
            carrier_exists = G.has_node(r["carrier"])
            origin_exists = G.has_node(r["origin"])
            dest_exists = G.has_node(r["dest"])

            if not (carrier_exists and origin_exists and dest_exists):
                #logger.warning(f"⚠️ Skipping flight {fn}: missing carrier or airport(s)")

                continue  # Skip this flight

            # Add Flight node
            G.add_node(
                fn,
                type="Flight",
                year=int(r["year"]),
                month=int(r["month"]),
                day=int(r["day"]),
                carrier=r["carrier"],
                flight_number=r["flight"],
                tail_number=r.get("tail_number", None),
                origin=r["origin"],
                dest=r["dest"],
                cancelled=bool(r["cancelled"]),
                delayed=bool(r["delayed"]),
            )

            # Now safely add edges (no missing targets)
            G.add_edge(fn, r["carrier"], relationship="OPERATED_BY")
            G.add_edge(fn, r["origin"], relationship="DEPARTS_FROM")
            G.add_edge(fn, r["dest"], relationship="ARRIVES_AT")

        elapsed = time.time() - t0
        logger.info(
            f"✅ Graph built in {elapsed:.2f}s — "
            f"{G.number_of_nodes()} nodes, {G.number_of_edges()} edges"
        )

        return G
