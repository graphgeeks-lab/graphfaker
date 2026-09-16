"""Sinks: land graph tables in a database or a loader-native file layout."""

from graphfaker.sinks.duckdb import duckdb_script, write_duckdb
from graphfaker.sinks.gen_fraud_graph import write_gen_fraud_graph
from graphfaker.sinks.ladybug import ladybug_script, write_ladybug
from graphfaker.sinks.neo4j import infer_endpoints, write_neo4j_admin
from graphfaker.sinks.neo4j_live import (
    LoadReport,
    Target,
    load_directory,
    load_tables,
    read_truth,
)
from graphfaker.sinks.neo4j_verify import (
    Verification,
    verify_directory,
    verify_tables,
)
from graphfaker.sinks.pyg import to_hetero_data, write_pyg

__all__ = [
    "LoadReport",
    "Target",
    "Verification",
    "duckdb_script",
    "infer_endpoints",
    "ladybug_script",
    "load_directory",
    "load_tables",
    "read_truth",
    "to_hetero_data",
    "verify_directory",
    "verify_tables",
    "write_duckdb",
    "write_gen_fraud_graph",
    "write_ladybug",
    "write_neo4j_admin",
    "write_pyg",
]
