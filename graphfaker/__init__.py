"""Top-level package for graphfaker."""

__author__ = """Dennis Irorere"""
__email__ = "denironyx@gmail.com"
__version__ = "0.2.0"

from .core import GraphFaker
from .fetchers.wiki import WikiFetcher
from .logger import configure_logging, logger

__all__ = ["GraphFaker", "logger", "configure_logging", "add_file_logging"]
