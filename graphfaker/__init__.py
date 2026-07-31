"""Top-level package for graphfaker."""

__author__ = """Dennis Irorere"""
__email__ = "denironyx@gmail.com"
__version__ = "0.4.0"

from .core import GraphFaker
from .fetchers.wiki import WikiFetcher
from .logger import add_file_logging, configure_logging, logger
from .resolve import (
    ResolutionResult,
    evaluate_clusters,
    merge_clusters,
    resolve_entities,
)

__all__ = [
    "GraphFaker",
    "WikiFetcher",
    "ResolutionResult",
    "resolve_entities",
    "merge_clusters",
    "evaluate_clusters",
    "logger",
    "configure_logging",
    "add_file_logging",
]
