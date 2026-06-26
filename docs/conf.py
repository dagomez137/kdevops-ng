# Configuration file for the Sphinx documentation builder.
import os

project = "kdevops-ng"
copyright = "2026, kdevops-ng authors"
author = "kdevops-ng authors"

extensions = [
    "sphinx_copybutton",
]

# Show the "$ " prompt but strip it (and follow "\" continuations) on copy.
copybutton_prompt_text = r"\$ "
copybutton_prompt_is_regexp = True
copybutton_line_continuation_character = "\\"

exclude_patterns = [
    "_build",
]

html_theme = "pydata_sphinx_theme"
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "/")
html_theme_options = {
    "navigation_with_keys": False,
    "navbar_align": "left",
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/dagomez137/kdevops-ng",
            "icon": "fa-brands fa-github",
        },
        {
            "name": "Discord",
            "url": "https://bit.ly/linux-kdevops-chat",
            "icon": "fa-brands fa-discord",
        },
        {
            "name": "IRC: #kdevops on OFTC",
            "url": "https://webchat.oftc.net/?channels=kdevops",
            "icon": "fa-solid fa-comments",
        },
        {
            "name": "Mailing list: kdevops@lists.linux.dev",
            "url": "mailto:kdevops@lists.linux.dev",
            "icon": "fa-solid fa-envelope",
        },
    ],
}
html_context = {
    "default_mode": "light",
}
