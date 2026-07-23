# Configuration file for the Sphinx documentation builder.
import os
import re

from docutils import nodes

project = "kdevops-ng"
copyright = "2026, kdevops-ng authors"
author = "kdevops-ng authors"

extensions = [
    "sphinx_copybutton",
    "sphinx_design",
]

# Show the "$ " host / "# " guest prompt but strip it (and follow "\"
# continuations) on copy.
copybutton_prompt_text = r"[$#] "
copybutton_prompt_is_regexp = True
copybutton_line_continuation_character = "\\"

exclude_patterns = [
    "_build",
]

html_theme = "pydata_sphinx_theme"
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "/")
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_favicon = "_static/favicon.ico"
html_theme_options = {
    "logo": {
        "image_light": "_static/logo.png",
        "image_dark": "_static/logo.png",
        "alt_text": "kdevops-ng",
    },
    "navigation_with_keys": False,
    "navbar_align": "left",
    "header_links_before_dropdown": 8,
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
_MAN8 = "https://man7.org/linux/man-pages/man8"
cmd_links = {
    "ssh": f"{_MAN7}/ssh.1.html",
    "mkfs": f"{_MAN8}/mkfs.8.html",
    "mount": f"{_MAN8}/mount.8.html",
    "xfs_info": f"{_MAN8}/xfs_info.8.html",
    "dmesg": f"{_MAN7}/dmesg.1.html",
    "cat": f"{_MAN7}/cat.1.html",
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
    "modules-load.d": f"{_SYSTEMD}/modules-load.d.html",
    "systemd-modules-load": f"{_SYSTEMD}/systemd-modules-load.service.html",
    "kmod-static-nodes": f"{_SYSTEMD}/kmod-static-nodes.service.html",
    "systemd-tmpfiles": f"{_SYSTEMD}/systemd-tmpfiles.html",
    "grafana": "https://grafana.com/docs/grafana/latest/",
    "prometheus": "https://prometheus.io/docs/prometheus/latest/",
    "loki": "https://grafana.com/docs/loki/latest/",
    "alloy": "https://grafana.com/docs/alloy/latest/",
    "RuntimeMaxSec": f"{_SYSTEMD}/systemd.service.html#RuntimeMaxSec=",
    "systemd-escape": f"{_SYSTEMD}/systemd-escape.html",
    "timeout": f"{_MAN7}/timeout.1.html",
    "git bisect": f"{_MAN7}/git-bisect.1.html",
    "git remote": f"{_MAN7}/git-remote.1.html",
    "git fetch": f"{_MAN7}/git-fetch.1.html",
    "qemu-img": "https://www.qemu.org/docs/master/tools/qemu-img.html",
    "virtiofsd": "https://gitlab.com/virtio-fs/virtiofsd",
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


# An f/ path reads as code, so ":src:`f/kernel/build`" renders the path as a
# monospaced literal hyperlinked to its source. The URL is resolved against the
# working tree (the path itself, then .flow, then .py), so a flow, a step, a
# shared module, a subsystem directory, or a concrete file all link correctly
# with no per-path table. An unresolvable path fails the build.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_BASE = "https://github.com/dagomez137/kdevops-ng"


def _src_url(path):
    for candidate in (path, f"{path}.flow", f"{path}.py"):
        full = os.path.join(_REPO_ROOT, candidate)
        if os.path.isdir(full):
            return f"{_SRC_BASE}/tree/main/{candidate}"
        if os.path.isfile(full):
            return f"{_SRC_BASE}/blob/main/{candidate}"
    return None


# Optional explicit title: ":src:`discover <f/fstests/discover>`" links the short
# label `discover` to the step's source, so prose can name a step by its bare name
# without spelling the whole path. Plain ":src:`f/fstests/discover`" still renders
# and links the path itself.
_EXPLICIT_TITLE_RE = re.compile(r"^(.+?)\s*<(.+?)>$", re.DOTALL)


def _src_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    m = _EXPLICIT_TITLE_RE.match(text)
    title, target = (m.group(1), m.group(2)) if m else (text, text)
    uri = _src_url(target)
    code = nodes.literal(rawtext, title)
    if uri is None:
        msg = inliner.reporter.error(
            f"src: no source found for {target!r} (looked for {target}, "
            f"{target}.flow, {target}.py under the repo root)",
            line=lineno,
        )
        return [code], [msg]
    return [nodes.reference(rawtext, "", code, refuri=uri)], []


def setup(app):
    app.add_config_value("cmd_links", {}, "env")
    app.add_role("cmd", _cmd_role)
    app.add_role("src", _src_role)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
