from enum import Enum


class FetcherType(str, Enum):
    """Enum for different fetcher types."""

    OSM = "osm"
    FLIGHTS = "flights"
    FAKER = "faker"
