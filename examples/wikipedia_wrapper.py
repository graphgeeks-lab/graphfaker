

import marimo

__generated_with = "0.13.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import wikipedia
    return (wikipedia,)


@app.cell
def _(wikipedia):
    print(wikipedia.search("Bill"))
    return


@app.cell
def _(wikipedia):
    print(wikipedia.page("Graph Theory").content)
    return


@app.cell
def _(wikipedia):
    print(wikipedia.page("Brett Cooper").content)
    return


@app.cell
def _(wikipedia):
    print(wikipedia.page("Brett Cooper").url)
    return


@app.cell
def _(wikipedia):
    gt = wikipedia.WikipediaPage("graph theory")
    return (gt,)


@app.cell
def _(gt):
    print(gt.html())
    return


@app.cell
def _(gt):
    gt.images
    return


@app.cell
def _(gt):
    gt.categories
    return


@app.cell
def _(gt):
    gt.url
    return


@app.cell
def _(gt):
    print(gt.summary)
    return


@app.cell
def _(gt):
    gt.section()

    return


@app.cell
def _():
    import wikidata
    return


@app.cell
def _():
    from typing import Dict, Any, List, Optional
    return Any, Dict


@app.cell
def _(Any, Dict, wikipedia):
    class wikiFetcher:

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

    return (wikiFetcher,)


@app.cell
def _(wikiFetcher):
    wiki = wikiFetcher()
    return (wiki,)


@app.cell
def _(wiki):
    game_theory = wiki.fetch_page(title="Graph Theory")
    return (game_theory,)


@app.cell
def _(game_theory):
    game_theory
    return


@app.cell
def _(Any, Dict, wikipedia):
    def fetch_page(title: str) -> Dict[str, Any]:
            """ 
            Retrieve a Wikipedia page by title and return core fields.

            Args: 
                title (str): The title of the Wikipedia page to fetch.

            Returns:
                Dict with keys: 
                    - title: str
                    - summary: str
                    - content: str
                    - images: List[Dict]
                    - links: List[str]
                    - references: List[str]

            """
            page = wikipedia.page(title)
            data = {
                "title": page.title,
                "summary": page.summary,
                "content": page.content,
                "images": page.images,
                "links": page.links, 
            }
            # references attributes may not exist in older wikipedia module
            refs = getattr(page, "references", None)
            data["references"] = refs if isinstance(refs, list) else []
            return data
    return (fetch_page,)


@app.cell
def _(fetch_page):
    theory = fetch_page(title= "Graph Theory")
    return (theory,)


@app.cell
def _(fetch_page):
    fetch_page(title="Graph Theory")
    return


@app.cell
def _(theory):
    print(theory['content'])
    return


@app.cell
def _():
    import graphfaker
    return


@app.cell
def _():
    from graphfaker import WikiFetcher
    return


@app.cell
def _(wikiFetcher):
    wiki2 = wikiFetcher()
    return


@app.cell
def _(wiki):
    page = wiki.fetch_page("Graph Theory")
    return (page,)


@app.cell
def _(page):
    page['url']
    return


if __name__ == "__main__":
    app.run()
