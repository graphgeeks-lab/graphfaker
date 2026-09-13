"""Sinks: land graph tables in a database or a loader-native file layout."""

from graphfaker.sinks.gen_fraud_graph import write_gen_fraud_graph
from graphfaker.sinks.ladybug import ladybug_script, write_ladybug
from graphfaker.sinks.neo4j import infer_endpoints, write_neo4j_admin

__all__ = ["infer_endpoints", "ladybug_script", "write_gen_fraud_graph", "write_ladybug", "write_neo4j_admin"]
