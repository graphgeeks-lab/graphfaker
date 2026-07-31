"""Top-level package for graphfaker."""

__author__ = """Dennis Irorere"""
__email__ = "denironyx@gmail.com"
__version__ = "0.4.0"

from .core import GraphFaker
from .corpus import (
    Corpus,
    DuplicationReport,
    attribute_nodes,
    duplication_report,
    generate_corpus,
)
from .export import export_csv, export_cypher, export_neo4j_csv
from .fetchers.wiki import WikiFetcher
from .logger import add_file_logging, configure_logging, logger
from .resolve import (
    ResolutionResult,
    evaluate_clusters,
    merge_clusters,
    resolve_entities,
)

__all__ = [
    "Corpus",
    "DuplicationReport",
    "GraphFaker",
    "ResolutionResult",
    "WikiFetcher",
    "add_file_logging",
    "attribute_nodes",
    "configure_logging",
    "duplication_report",
    "evaluate_clusters",
    "export_csv",
    "export_cypher",
    "export_neo4j_csv",
    "generate_corpus",
    "logger",
    "merge_clusters",
    "resolve_entities",
]
