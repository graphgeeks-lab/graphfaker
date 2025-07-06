# tests/test_graph_from_source_osm.py

import pytest
from graphfaker.fetchers.wiki import WikiFetcher

wiki = WikiFetcher()

def test_wiki_fetch_page():
    page = wiki.fetch_page("Graph Theory")
    
    # check url 
    assert page['url'] == 'https://en.wikipedia.org/wiki/Graph_theory'
    # check title
    assert page['title'] == "Graph theory" 
    

