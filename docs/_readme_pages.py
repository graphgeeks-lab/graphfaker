"""Build site pages from README sections at Sphinx build time.

The README is the source of truth for installation, the first commands, the
Python usage and the command line. Rather than keep a second copy in the
docs, ``conf.py`` calls :func:`generate` to cut those sections out of
``README.md`` and write them as pages, with the README's repository-relative
links rewritten to site paths. The generated files are ignored by git.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = "https://github.com/graphgeeks-lab/graphfaker/blob/main/"

#: output page -> (title, first heading included, heading that ends the cut)
PAGES = {
    "get-started/install.md": ("Install", "## Install", "## Three things to try"),
    "get-started/first-commands.md": ("Three things to try", "## Three things to try", "## What you can generate"),
    "get-started/python.md": ("Using GraphFaker from Python", "## Schemas", "## Command line"),
    "domains/index.md": ("Domains", "## What you can generate", "## Schemas"),
    "reference/cli.md": ("Command line", "## Command line", "## Entity resolution"),
    "guides/entity-resolution.md": ("Entity resolution and duplication", "## Entity resolution", "## Performance and limits"),
}

#: appended to a generated page, after the README content
TRAILERS = {
    "domains/index.md": """
```{toctree}
:hidden:

social
fraud
real-world
```
""",
}


def _section(text: str, start: str, end: str) -> str:
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j].rstrip() + "\n"


def _rewrite_links(body: str) -> str:
    # docs/x.md -> x.md ; docs/notebooks/x.ipynb -> notebooks/x.ipynb ; other repo files -> GitHub
    body = re.sub(r"\]\(docs/", "](", body)
    body = body.replace("](#command-line)", "](reference/cli.md)")
    body = body.replace("](#what-the-options-mean)", "](reference/cli.md#what-the-options-mean)")
    body = re.sub(r"\]\((examples/[^)]+)\)", lambda m: f"]({REPO}{m.group(1)})", body)
    body = re.sub(r"\]\((LICENSE|HISTORY\.rst|CONTRIBUTING\.rst)\)", lambda m: f"]({REPO}{m.group(1)})", body)
    return body


def _demote(body: str) -> str:
    """Drop the README's own section heading; the page title becomes the H1
    and the README's ``###`` headings become ``##``."""
    body = body.split("\n", 1)[1] if body.startswith("## ") else body
    return re.sub(r"^(#{3,})", lambda m: "#" * (len(m.group(1)) - 1), body, flags=re.M)


def generate(docs_dir: Path) -> list[Path]:
    readme = (docs_dir.parent / "README.md").read_text(encoding="utf-8")
    written = []
    for name, (title, start, end) in PAGES.items():
        body = _rewrite_links(_demote(_section(readme, start, end)))
        # the page's own directory depth changes relative links to sibling pages
        depth = name.count("/")
        if depth:
            prefix = "../" * depth
            body = re.sub(r"\]\((?!https?://|#)([^)]+\.(?:md|ipynb|html)[^)]*)\)", lambda m, prefix=prefix: "](" + prefix + m.group(1) + ")", body)
        target = docs_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"<!-- Generated from README.md by docs/_readme_pages.py at build time. Edit the README. -->\n\n# {title}\n\n{body.lstrip()}{TRAILERS.get(name, '')}",
            encoding="utf-8",
        )
        written.append(target)
    return written
