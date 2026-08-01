# tests/test_fetchers_wiki.py

from typing import ClassVar
from unittest.mock import patch

import pytest

from graphfaker.fetchers.wiki import WikiFetcher

wiki = WikiFetcher()


class _FakePage:
    """Stands in for `wikipedia.page`'s return value.

    The previous version of this test called the live Wikipedia API, so the suite
    failed intermittently on any network hiccup and could not run offline or in a
    sandboxed CI job. Mocking keeps the field-mapping assertions — which is what
    `fetch_page` is actually responsible for — without the flakiness.
    """

    title = "Graph theory"
    url = "https://en.wikipedia.org/wiki/Graph_theory"
    summary = "Graph theory is the study of graphs."
    content = "Graph theory is the study of graphs, which are mathematical structures."
    images: ClassVar[list[str]] = ["https://upload.wikimedia.org/example.png"]
    links: ClassVar[list[str]] = [
        "Vertex (graph theory)",
        "Edge (graph theory)",
        "Adjacency matrix",
    ]
    references: ClassVar[list[str]] = ["https://example.org/reference"]
    sections: ClassVar[list[str]] = ["History", "Definitions"]

    def section(self, name):
        return f"Body of {name}"


def test_wiki_fetch_page_maps_core_fields():
    with patch("wikipedia.page", return_value=_FakePage()) as mock_page:
        page = wiki.fetch_page("Graph Theory")

    mock_page.assert_called_once_with("Graph Theory")
    assert page["url"] == "https://en.wikipedia.org/wiki/Graph_theory"
    assert page["title"] == "Graph theory"
    assert "study of graphs" in page["summary"]
    assert page["links"][0] == "Vertex (graph theory)"


def test_wiki_export_page_json(tmp_path):
    import json

    with patch("wikipedia.page", return_value=_FakePage()):
        page = wiki.fetch_page("Graph Theory")

    target = tmp_path / "graph_theory.json"
    WikiFetcher.export_page_json(page, str(target))

    assert target.exists()
    reloaded = json.loads(target.read_text(encoding="utf-8"))
    assert reloaded["title"] == "Graph theory"


@pytest.mark.network
def test_wiki_fetch_page_live():
    """Hits the real API. Deselect with `-m "not network"`."""
    page = wiki.fetch_page("Graph Theory")
    assert page["url"] == "https://en.wikipedia.org/wiki/Graph_theory"
    assert page["title"] == "Graph theory"
