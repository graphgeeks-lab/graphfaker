from enum import Enum


class FetcherType(str, Enum):
    """Enum for different fetcher types."""

    OSM = "osm"
    FLIGHTS = "flights"
    FAKER = "faker"


class ExportFormat(str, Enum):
    """Output formats a generated graph can be written to."""

    GRAPHML = "graphml"
    CSV = "csv"
    NEO4J_CSV = "neo4j-csv"
    CYPHER = "cypher"
    OPENCYPHER = "opencypher"
    GQL = "gql"
