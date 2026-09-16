"""GraphFaker: synthetic graph data that behaves like the real thing.

Generate realistic graph datasets from a schema, or pick a ready-made domain
such as a bank with laundering patterns, and get entities, relationships and
events whose structure, attributes and timing agree, with the ground truth
included.

The public names are imported on first use rather than at package import.
``import graphfaker`` used to pull in networkx, pandas, the flight and OSM
fetchers and every domain, about three seconds warm and seven on a fresh
Windows process; a worker process that only needs the sampling engine, and a
CLI invocation that only needs one command, paid all of it.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

__author__ = """Dennis Irorere"""
__email__ = "denironyx@gmail.com"
__version__ = "0.6.1"

#: public name -> the module it lives in
_EXPORTS = {
    "GraphTables": "graphfaker.backends",
    "GraphFaker": "graphfaker.core",
    "Corpus": "graphfaker.corpus",
    "DuplicationReport": "graphfaker.corpus",
    "attribute_nodes": "graphfaker.corpus",
    "duplication_report": "graphfaker.corpus",
    "generate_corpus": "graphfaker.corpus",
    "GraphRun": "graphfaker.engine",
    "Manifest": "graphfaker.engine",
    "generate": "graphfaker.engine",
    "export_csv": "graphfaker.export",
    "export_cypher": "graphfaker.export",
    "export_neo4j_csv": "graphfaker.export",
    "WikiFetcher": "graphfaker.fetchers.wiki",
    "add_file_logging": "graphfaker.logger",
    "configure_logging": "graphfaker.logger",
    "logger": "graphfaker.logger",
    "compare_topology": "graphfaker.metrics",
    "graph_stats": "graphfaker.metrics",
    "ResolutionResult": "graphfaker.resolve",
    "evaluate_clusters": "graphfaker.resolve",
    "merge_clusters": "graphfaker.resolve",
    "resolve_entities": "graphfaker.resolve",
    "GraphSchema": "graphfaker.schema",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'graphfaker' has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


if TYPE_CHECKING:  # pragma: no cover
    from .backends import GraphTables as GraphTables
    from .core import GraphFaker as GraphFaker
    from .corpus import Corpus as Corpus
    from .corpus import DuplicationReport as DuplicationReport
    from .corpus import attribute_nodes as attribute_nodes
    from .corpus import duplication_report as duplication_report
    from .corpus import generate_corpus as generate_corpus
    from .engine import GraphRun as GraphRun
    from .engine import Manifest as Manifest
    from .engine import generate as generate
    from .export import export_csv as export_csv
    from .export import export_cypher as export_cypher
    from .export import export_neo4j_csv as export_neo4j_csv
    from .fetchers.wiki import WikiFetcher as WikiFetcher
    from .logger import add_file_logging as add_file_logging
    from .logger import configure_logging as configure_logging
    from .logger import logger as logger
    from .metrics import compare_topology as compare_topology
    from .metrics import graph_stats as graph_stats
    from .resolve import ResolutionResult as ResolutionResult
    from .resolve import evaluate_clusters as evaluate_clusters
    from .resolve import merge_clusters as merge_clusters
    from .resolve import resolve_entities as resolve_entities
    from .schema import GraphSchema as GraphSchema
