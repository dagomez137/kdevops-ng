# Configuration file for the Sphinx documentation builder.
import os

project = "kdevops-ng"
copyright = "2026, kdevops-ng authors"
author = "kdevops-ng authors"

extensions = [
    "sphinx_copybutton",
]

exclude_patterns = [
    "_build",
]

html_theme = "pydata_sphinx_theme"
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "/")
html_theme_options = {
    "navigation_with_keys": False,
    "navbar_align": "left",
}
html_context = {
    "default_mode": "light",
}
