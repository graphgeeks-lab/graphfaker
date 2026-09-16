"""Sphinx configuration for the GraphFaker documentation site.

Theme: pydata-sphinx-theme, restyled in ``_static/custom.css`` to match the
site design (warm off-white ground, Manrope and JetBrains Mono, one teal
accent; the site is light only, code blocks are dark). The header carries the six sections, the left
sidebar the full page tree, the right sidebar the page outline. Markdown
pages and executed notebooks come in through MyST-NB.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(".."))

import graphfaker

# Pages cut from the README so the site and the README never drift.
sys.path.insert(0, os.path.dirname(__file__))
from _readme_pages import PAGES as _README_PAGES
from _readme_pages import generate as _generate_readme_pages

_generate_readme_pages(Path(__file__).parent)

project = "GraphFaker"
copyright = "2025, Dennis Irorere"
author = "Dennis Irorere"
version = graphfaker.__version__
release = graphfaker.__version__
language = "en"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "myst_nb",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "myst-nb",
    ".ipynb": "myst-nb",
}
master_doc = "index"
# docs/design/ is kept out of the repository (.gitignore), so it is kept out of the site too.
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "notebooks/build_*.py", "_readme_pages.py", "design/*"]
templates_path = ["_templates"]

# Notebooks are committed with their outputs; the site never re-runs them.
nb_execution_mode = "off"

myst_enable_extensions = ["colon_fence", "deflist", "substitution"]
myst_heading_anchors = 3

autodoc_default_options = {"members": True, "undoc-members": False, "show-inheritance": False}
autodoc_typehints = "description"
suppress_warnings = ["ref.python"]  # "type" is both a corpus field name and a Python role
autosummary_generate = False
napoleon_google_docstring = True
napoleon_numpy_docstring = False

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "polars": ("https://docs.pola.rs/api/python/stable/", None),
}

# --------------------------------------------------------------------- html

html_theme = "pydata_sphinx_theme"
html_title = "GraphFaker"
html_static_path = ["_static"]
html_favicon = "_static/logo.svg"
html_css_files = [
    "https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap",
    "custom.css",
]
html_show_sourcelink = False

html_theme_options = {
    "logo": {"text": "graphfaker", "image_light": "_static/logo.svg", "image_dark": "_static/logo.svg"},
    # header: logo, the six sections, search, version, GitHub, light/dark
    "navbar_start": ["navbar-logo"],
    "navbar_center": ["navbar-nav"],
    "navbar_persistent": ["search-button-field"],
    "navbar_end": ["gf-version", "gf-github"],
    "navbar_align": "left",
    "header_links_before_dropdown": 6,
    # article: breadcrumb above, previous/next below
    "article_header_start": ["breadcrumbs"],
    "show_prev_next": True,
    # right sidebar: the page outline and an edit link
    "secondary_sidebar_items": {"**": ["page-toc", "gf-edit"], "index": []},
    "show_toc_level": 2,
    "primary_sidebar_end": [],
    "footer_start": ["gf-footer-left"],
    "footer_center": [],
    "footer_end": ["gf-footer-right"],
    "use_edit_page_button": True,
    "back_to_top_button": False,
    # code blocks are dark on the light page, as in the design
    "pygments_light_style": "github-dark",
    "pygments_dark_style": "github-dark",
}

# The full page tree in the left sidebar on every page; none on the landing page.
html_sidebars = {"**": ["gf-sidebar"], "index": []}

html_context = {
    "github_user": "graphgeeks-lab",
    "github_repo": "graphfaker",
    "github_version": "main",
    "doc_path": "docs",
    # light only: no theme switcher in the header, and no following the system setting
    "default_mode": "light",
    # generated pages link their edit button to the README they came from
    "readme_pages": [name.removesuffix(".md") for name in _README_PAGES],
}

htmlhelp_basename = "graphfakerdoc"
