import os
import io
import zipfile
import tempfile
import warnings
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
from io import StringIO

import requests
import pandas as pd
# import polars as pl  # uncomment if you want to try Polars
from tqdm.auto import tqdm

# ─── Airline Lookup ─────────────────────────────────────────────────────────────

AIRLINE_LOOKUP_URL = "https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_haVdhR_PNeeVRef"

def fetch_airlines() -> pd.DataFrame:
    """Download BTS airlines CSV by disabling SSL verification."""
    resp = requests.get(AIRLINE_LOOKUP_URL, verify=False)
    resp.raise_for_status()
    df = pd.read_csv(StringIO(resp.text))
    return df.rename(columns={'Code': 'carrier', 'Description': 'airline_name'})

# ─── Flight Data URLs ────────────────────────────────────────────────────────────

def make_flight_url(year: int, month: int) -> str:
    mm = str(month)
    return (
        "https://transtats.bts.gov/PREZIP/"
        f"On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{mm}.zip"
    )

# ─── Download + Extract ─────────────────────────────────────────────────────────

def download_and_extract_csv(url: str) -> io.BytesIO:
    """
    Download a ZIP file with a progress bar, extract its single CSV,
    and return a BytesIO buffer of the CSV.
    """
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    total = int(resp.headers.get('content-length', 0))
    buf = io.BytesIO()
    with tqdm.wrapattr(resp.raw, "read", total=total, desc="Downloading", leave=False) as raw:
        buf.write(raw.read())
    buf.seek(0)

    # unzip
    with zipfile.ZipFile(buf) as z:
        # assume there's exactly one CSV inside
        name = next(f for f in z.namelist() if f.lower().endswith('.csv'))
        data = z.read(name)
    return io.BytesIO(data)

# ─── Schema + Loader ────────────────────────────────────────────────────────────

COLUMN_MAP = {
    'Year': 'year',
    'Month': 'month',
    'DayofMonth': 'day',
    'DepTime': 'dep_time',
    'CRSDepTime': 'sched_dep_time',
    'DepDelay': 'dep_delay',
    'ArrTime': 'arr_time',
    'CRSArrTime': 'sched_arr_time',
    'ArrDelay': 'arr_delay',
    'Reporting_Airline': 'carrier',
    'Flight_Number_Reporting_Airline': 'flight',
    'Tail_Number': 'tailnum',
    'Origin': 'origin',
    'Dest': 'dest',
    'AirTime': 'air_time',
    'Distance': 'distance',
}

def load_month_df(
    year: int,
    month: int,
    *,
    engine: str = 'pandas'
) -> pd.DataFrame:
    """
    Download one month’s on‐time data, rename columns, and return a DataFrame.
    engine: 'pandas' (default) or 'polars'
    """
    url = make_flight_url(year, month)
    buf = download_and_extract_csv(url)

    if engine == 'polars':
        import polars as pl
        df = pl.read_csv(buf).select(list(COLUMN_MAP.keys()))
        df = df.rename(COLUMN_MAP)
        return df
    else:
        df = pd.read_csv(buf, usecols=list(COLUMN_MAP.keys()))
        return df.rename(columns=COLUMN_MAP)

# ─── Higher‐Level Fetchers ───────────────────────────────────────────────────────

def fetch_flight_data_single(year: int, month: int, **kwargs) -> pd.DataFrame:
    """Get one month of flight data."""
    return load_month_df(year, month, **kwargs)

def fetch_flight_data_range(
    start: Tuple[int,int],
    end:   Tuple[int,int],
    engine: str = 'pandas'
) -> pd.DataFrame:
    """
    Fetch data from start=(year,month) to end=(year,month) inclusive.
    May take a while if the span is large.
    """
    y0, m0 = start
    y1, m1 = end
    dates = []
    cur = datetime(y0, m0, 1)
    last = datetime(y1, m1, 1)
    while cur <= last:
        dates.append((cur.year, cur.month))
        # advance one month
        nxt = cur + timedelta(days=32)
        cur = datetime(nxt.year, nxt.month, 1)

    dfs = []
    for y, m in dates:
        print(f"Fetching {y}-{m:02d}…")
        dfs.append(load_month_df(y, m, engine=engine))
    if engine == 'polars':
        import polars as pl
        return pl.concat(dfs)
    return pd.concat(dfs, ignore_index=True)

def fetch_past_year(engine: str = 'pandas') -> pd.DataFrame:
    """
    Convenience: fetch the past 12 months up to now.
    WARNING: can be VERY SLOW.
    """
    warnings.warn(
        "Pulling 12 months of data will download ~1GB+. Be patient!",
        UserWarning
    )
    today = datetime.today()
    one_year_ago = today - timedelta(days=365)
    start = (one_year_ago.year, one_year_ago.month)
    end   = (today.year, today.month)
    return fetch_flight_data_range(start, end, engine=engine)

# ─── Simple Timing Helper ───────────────────────────────────────────────────────

import time
def time_function(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) once and print elapsed seconds."""
    t0 = time.time()
    result = fn(*args, **kwargs)
    print(f"Elapsed: {time.time() - t0:.1f} sec")
    return result

# ─── Example Usage ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # 1) Airline lookup
    airlines = fetch_airlines()
    print(airlines.head())

    # 2) Single month
    df_jan = time_function(fetch_flight_data_single, 2024, 1)

    # 3) Multi‐month span
    df_span = time_function(fetch_flight_data_range, (2023,10), (2024,1))

    # 4) Past year (with warning)
    df_year = time_function(fetch_past_year)

    # 5) Try Polars for a single month
    # df_jan_pl = time_function(fetch_flight_data_single, 2024, 1, engine='polars')
