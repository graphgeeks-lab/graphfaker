# graphfaker/fetchers/wiki.py

"""
WikipediaFetcher: Fetch unstructured content from Wikipedia that can be used for graph generation.

Provides methods to retrieve articles by title, capture key fields (title, summary, content, sections, links, references),
and export as JSON. Designed to give users raw data they can use to build their own graphs, tranform in Knowledge Graphs,
peform entity resolutions, and more.

Usage:
    from graphfaker import WikiFetcher
    wiki = WikiFetcher()
    
    # Fetch raw page data
    page = wiki.fetch_page("Graph Theory")
    
    # Access and Print key fields
    print(page['title'])
    print(page['summary'])
    print(page['content'])
    print(page['sections'], page['links'][:5], page['references'][:5])
    wiki.export_page_json(page, "graph_theory.json")    
"""

import json
from typing import Dict, Any, List, Optional
import wikipedia

class WikiFetcher:
    """
    Fetch and prepare unstructured Wikipedia content for graph construction.
    """
    
    @staticmethod
    def fetch_page(title: str) -> Dict[str, Any]:
            """ 
            Retrieve a Wikipedia page by title and return core fields.
            
            Args: 
                title (str): The title of the Wikipedia page to fetch.
            
            Returns:
                Dict with keys: 
                    - title: str
                    - url: str
                    - summary: str
                    - content: str
                    - images: List[Dict]
                    - links: List[str]
                    - references: List[str]
            
            """
            page = wikipedia.page(title)
            data = {
                "title": page.title,
                "url": page.url,
                "summary": page.summary,
                "content": page.content,
                "images": page.images,
                "links": page.links,    
            }
            # references attributes may not exist in older wikipedia module
            refs = getattr(page, "references", None)
            data["references"] = refs if isinstance(refs, list) else []
            return data
    
    @staticmethod
    def export_page_json(page: Dict[str, Any], filename: str) -> None:
        """
        Write the fetched Wikipedia page data to a JSON file.

        Args:
            page_data: Dict returned by `fetch_page`.
            filename: Destination JSON file path.
        """
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(page, f, ensure_ascii=False, indent=2)
        print(f"Exported Wikipedia page data to '{filename}'")
