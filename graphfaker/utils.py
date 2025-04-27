import os
import io
import zipfile
import tempfile
import warnings
from datetime import datetime, timedelta
from typing import Tuple, Optional
from io import StringIO

import requests
import pandas as pd
# import polars as pl  # uncomment to enable Polars
from tqdm.auto import tqdm

import urllib3
from urllib3.exceptions import InsecureRequestWarning

# disable only the single warning about unverified HTTPS requests
urllib3.disable_warnings(InsecureRequestWarning)

# ---- Airline Lookup -----

AIRLINE_LOOKUP_URL = (
    "https://transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_haVdhR_PNeeVRef"
)

def fetch_airlines() -> pd.DataFrame:
    """Download BTS airlines CSV by disabling SSL verification and return a tidy DataFrame."""
    resp = requests.get(AIRLINE_LOOKUP_URL, verify=False)
    resp.raise_for_status()
    df = pd.read_csv(StringIO(resp.text))
    return df.rename(columns={'Code': 'carrier', 'Description': 'airline_name'})


# ---- Airport Lookup -----

AIRPORTS_URL = (
    "https://raw.githubusercontent.com/"
    "jpatokal/openflights/master/data/airports.dat"
)

AIRPORT_COLS = [
    "id", "name", "city", "country", "faa", "icao",
    "lat", "lon", "alt", "tz", "dst", "tzone", "type", "source"
]

def fetch_airports(
    country: Optional[str] = "United States",
    keep_only_with_faa: bool = True
) -> pd.DataFrame:
    """
    Download and tidy the OpenFlights airports dataset:
      - parse with proper column names
      - filter by country (default US)
      - drop missing FAA codes if requested
      - dedupe by FAA
    """
    df = pd.read_csv(
        AIRPORTS_URL,
        header=None,
        names=AIRPORT_COLS,
        na_values=["", "NA", r"\N"],
        keep_default_na=True,
        dtype={
            "id": int, "name": str, "city": str, "country": str,
            "faa": str, "icao": str, "lat": float, "lon": float,
            "alt": float, "tz": float, "dst": str, "tzone": str,
            "type": str, "source": str,
        }
    )

    if country:
        df = df[df["country"] == country]
    if keep_only_with_faa:
        df = df[df["faa"].notna() & (df["faa"] != "")]

    df = df.sort_values("id").drop_duplicates(subset="faa", keep="first")
    return df[["faa", "name", "city", "lat", "lon", "alt", "tz", "dst", "tzone"]].reset_index(drop=True)


# ---- Flight Data Helpers -----

def make_flight_url(year: int, month: int) -> str:
    """BTS uses non-zero-padded months (e.g. year_1.zip for January)."""
    return (
        "https://transtats.bts.gov/PREZIP/"
        f"On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
    )

COLUMN_MAP = {
    'Year': 'year', 'Month': 'month', 'DayofMonth': 'day',
    'DepTime': 'dep_time', 'CRSDepTime': 'sched_dep_time', 'DepDelay': 'dep_delay',
    'ArrTime': 'arr_time', 'CRSArrTime': 'sched_arr_time', 'ArrDelay': 'arr_delay',
    'Reporting_Airline': 'carrier', 'Flight_Number_Reporting_Airline': 'flight',
    'Tail_Number': 'tailnum', 'Origin': 'origin', 'Dest': 'dest',
    'AirTime': 'air_time', 'Distance': 'distance'
}

def download_and_extract_csv(url: str) -> io.BytesIO:
    """
    Stream-download a BTS ZIP, extract its CSV, and return a BytesIO of the CSV.
    """
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    total = int(resp.headers.get('content-length', 0))
    buf = io.BytesIO()
    with tqdm.wrapattr(resp.raw, 'read', total=total, desc=f"Downloading {os.path.basename(url)}", leave=False) as raw:
        buf.write(raw.read())
    #buf.seek(0)
    print(f"Download BTS ZIP to temporary file: {buf.seek(0)}")
    with zipfile.ZipFile(buf) as z:
        name = next(f for f in z.namelist() if f.lower().endswith('.csv'))
        data = z.read(name)
    return io.BytesIO(data)


def load_month_df(
    year: int,
    month: int,
    engine: str = 'pandas'
):
    """Load one month of BTS on-time flight data into pandas or Polars."""
    buf = download_and_extract_csv(make_flight_url(year, month))

    if engine == 'polars':
        import polars as pl
        df = pl.read_csv(buf).select(list(COLUMN_MAP.keys()))
        return df.rename(COLUMN_MAP)
    else:
        df = pd.read_csv(buf, usecols=list(COLUMN_MAP.keys()))
        return df.rename(columns=COLUMN_MAP)


def fetch_flight_data_single(year: int, month: int):
    """Fetch one month of flight data with pandas."""
    return load_month_df(year, month, engine='pandas')


def fetch_flight_data_range(
    start: Tuple[int, int],
    end:   Tuple[int, int],
    engine: str = 'polars'
):
    """Fetch a range of months; uses Polars by default for speed."""
    y0, m0 = start
    y1, m1 = end
    cur = datetime(y0, m0, 1)
    last = datetime(y1, m1, 1)
    dfs = []
    while cur <= last:
        dfs.append(load_month_df(cur.year, cur.month, engine=engine))
        # advance one month
        nxt = cur + timedelta(days=32)
        cur = datetime(nxt.year, nxt.month, 1)

    if engine == 'polars':
        import polars as pl
        return pl.concat(dfs)
    return pd.concat(dfs, ignore_index=True)


def fetch_flights(
    year: Optional[int] = None,
    month: Optional[int] = None,
    date_range: Optional[Tuple[Tuple[int,int], Tuple[int,int]]] = None
):
    """
    Unified fetch:
      - If date_range is provided (start, end), fetch that span with Polars.
      - Else require year and month to fetch a single month with pandas.
    """
    if date_range is not None:
        start, end = date_range
        warnings.warn(
            "Fetching multiple months with Polars; may be slow if span is large.",
            UserWarning
        )
        return fetch_flight_data_range(start, end, engine='polars')
    if year is None or month is None:
        raise ValueError("Provide either date_range or both year and month.")
    return fetch_flight_data_single(year, month)


# --- Timing Helper ----
import time
def time_function(fn, *args, **kwargs):
    t0 = time.time()
    result = fn(*args, **kwargs)
    print(f"Elapsed: {time.time() - t0:.1f} sec")
    return result


# ─── Example Usage ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # 1) Airline lookup
    airlines = fetch_airlines()
    print(airlines.head())

    """
  carrier                   airline_name
0     02Q                  Titan Airways
1     04Q             Tradewind Aviation
2     05Q            Comlux Aviation, AG
3     06Q  Master Top Linhas Aereas Ltd.
4     07Q            Flair Airlines Ltd.
    """

    # 2) Fetch U.S. airports with FAA codes
    airports_us = fetch_airports()
    print(airports_us.head())

    """
       faa                        name           city  ...    tz  dst              tzone
0  BTI  Barter Island LRRS Airport  Barter Island  ...  -9.0    A  America/Anchorage
1  LUR  Cape Lisburne LRRS Airport  Cape Lisburne  ...  -9.0    A  America/Anchorage
2  PIZ      Point Lay LRRS Airport      Point Lay  ...  -9.0    A  America/Anchorage
3  ITO  Hilo International Airport           Hilo  ... -10.0    N   Pacific/Honolulu
4  ORL   Orlando Executive Airport        Orlando  ...  -5.0    A   America/New_York
    """

    # 3) Single month
    df = time_function(fetch_flights, year=2024, month=1)
"""
year,month,day,carrier,tailnum,flight,origin,dest,sched_dep_time,dep_time,dep_delay,sched_arr_time,arr_time,arr_delay,air_time,distance
2024,1,8,9E,N485PX,4801,LGA,OMA,856,851.0,-5.0,1135,1124.0,-11.0,184.0,1148.0
2024,1,9,9E,N912XJ,4801,LGA,OMA,856,851.0,-5.0,1135,1107.0,-28.0,168.0,1148.0
2024,1,10,9E,N918XJ,4801,LGA,OMA,856,850.0,-6.0,1135,1110.0,-25.0,177.0,1148.0
2024,1,11,9E,N490PX,4801,LGA,OMA,856,919.0,23.0,1135,1202.0,27.0,188.0,1148.0
2024,1,12,9E,N915XJ,4801,LGA,OMA,856,851.0,-5.0,1135,1137.0,2.0,185.0,1148.0

"""
