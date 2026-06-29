# Configuration file for the Sphinx documentation builder.
import os

from docutils import nodes

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
html_static_path = ["_static"]
html_theme_options = {
    "logo": {
        "image_light": "_static/logo.png",
        "image_dark": "_static/logo.png",
        "alt_text": "kdevops-ng",
    },
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

# Monospaced external links. ":cmd:`name`" renders ``name`` (a literal, so it
# reads as a command) hyperlinked to its manual or source. The table is the one
# source for every command, tool, and systemd directive URL the docs cite, so a
# name is linked the same way everywhere and updated in one place. Linux
# man-pages for the base tools, the upstream systemd manual for the systemd
# ones (its per-directive anchors are why TimeoutStartSec/RuntimeMaxSec point
# there).
_SYSTEMD = "https://www.freedesktop.org/software/systemd/man/latest"
_MAN7 = "https://man7.org/linux/man-pages/man1"
cmd_links = {
    "ssh": f"{_MAN7}/ssh.1.html",
    "dmesg": f"{_MAN7}/dmesg.1.html",
    "~/.ssh/config": "https://man7.org/linux/man-pages/man5/ssh_config.5.html",
    "socat": "http://www.dest-unreach.org/socat/doc/socat.html",
    "systemctl": f"{_SYSTEMD}/systemctl.html",
    "journalctl": f"{_SYSTEMD}/journalctl.html",
    "timedatectl": f"{_SYSTEMD}/timedatectl.html",
    "loginctl": f"{_SYSTEMD}/loginctl.html",
    "systemd-analyze": f"{_SYSTEMD}/systemd-analyze.html",
    "machinectl": f"{_SYSTEMD}/machinectl.html",
    "hostnamectl": f"{_SYSTEMD}/hostnamectl.html",
    "systemd-run": f"{_SYSTEMD}/systemd-run.html",
    "systemd-ssh-proxy": f"{_SYSTEMD}/systemd-ssh-proxy.html",
    "systemd-journal-gatewayd": f"{_SYSTEMD}/systemd-journal-gatewayd.service.html",
    "systemd-machined": f"{_SYSTEMD}/systemd-machined.service.html",
    "TimeoutStartSec": f"{_SYSTEMD}/systemd.service.html#TimeoutStartSec=",
    "RuntimeMaxSec": f"{_SYSTEMD}/systemd.service.html#RuntimeMaxSec=",
}


def _cmd_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    uri = inliner.document.settings.env.config.cmd_links.get(text)
    code = nodes.literal(rawtext, text)
    if uri is None:
        msg = inliner.reporter.error(
            f"cmd: no link registered for {text!r} (add it to cmd_links in "
            f"docs/conf.py)",
            line=lineno,
        )
        return [code], [msg]
    return [nodes.reference(rawtext, "", code, refuri=uri)], []


def setup(app):
    app.add_config_value("cmd_links", {}, "env")
    app.add_role("cmd", _cmd_role)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
