# SPDX-License-Identifier: copyleft-next-0.3.1
"""Ref listing for the build forms' ref pickers; imported as f.common.gitrefs.

Lists the branches and tags of a host-local bare repository by reading its
`packed-refs` file and loose `refs/` entries directly (no `git` subprocess, so
a form dynselect answers fast). The universe is the durable Bare the worktree
steps check out from (`$SYSTEM_DIR/bare/<repo>.git`), which carries developer
branches plus the mirror-fetched upstream refs; before the first fetch it
falls back to the mirror (`$MIRRORS_DIR/<repo>.git`). Branches come first,
then tags newest version first, so the zero-config pick is the tip of a tree
or the latest release. The linux picker additionally leads with kernel.org's
current releases (`releases.json`), labeled by moniker, so the latest
mainline, stable, longterm, or linux-next is a one-click pick.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

_MAX_OPTIONS = 200

_VERSION_RE = re.compile(
    r"^v(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?(?:-rc(?P<rc>\d+))?$"
)

_KORG_URL = "https://www.kernel.org/releases.json"
_KORG_CACHE_TTL = 3600
# kernel.org requires an identifying User-Agent and bans anonymous-looking
# queries; this is the exact one the kdevops project sends.
_KORG_USER_AGENT = "kdevops/5.0.2 (kdevops@lists.linux.dev)"
# A numeric version gets the tag's leading `v`; linux-next stays `next-YYYYMMDD`.
_KORG_NUMERIC_RE = re.compile(r"^\d+\.\d+(\.\d+|-rc\d+)?$")


def _repo_dir(repo: str) -> Path | None:
    system = os.environ.get("SYSTEM_DIR", "")
    mirrors = os.environ.get("MIRRORS_DIR") or (
        str(Path(system) / "mirror") if system else ""
    )
    for root in ([Path(system) / "bare"] if system else []) + (
        [Path(mirrors)] if mirrors else []
    ):
        candidate = root / f"{repo}.git"
        if (candidate / "packed-refs").is_file() or (candidate / "refs").is_dir():
            return candidate
    return None


def _read_refs(bare: Path) -> dict[str, str]:
    """`refs/heads/...`/`refs/tags/...` names -> kind, loose over packed."""
    refs: dict[str, str] = {}
    packed = bare / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(errors="replace").splitlines():
            if not line or line.startswith(("#", "^")):
                continue
            _, _, name = line.partition(" ")
            for kind, prefix in (("head", "refs/heads/"), ("tag", "refs/tags/")):
                if name.startswith(prefix):
                    refs[name[len(prefix) :]] = kind
    for kind, sub in (("head", "heads"), ("tag", "tags")):
        root = bare / "refs" / sub
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file():
                    refs[str(path.relative_to(root))] = kind
    return refs


def _tag_key(name: str) -> tuple:
    """Sort key: released versions newest first, then rcs, then the rest."""
    m = _VERSION_RE.match(name)
    if not m:
        return (1, 0, 0, 0, 0, 0, name)
    rc = m.group("rc")
    return (
        0,
        -int(m.group("major")),
        -int(m.group("minor")),
        -int(m.group("patch") or 0),
        0 if rc is None else 1,
        -int(rc or 0),
        name,
    )


def _korg_releases() -> list[dict]:
    """kernel.org's current releases as `{tag, moniker, iseol}`, best-effort.

    The JSON is disk-cached for `_KORG_CACHE_TTL` seconds because a dynselect
    re-runs on every filter keystroke; a fetch failure falls back to a stale
    cache, then to an empty list, so the picker never blocks on the network.
    """
    system = os.environ.get("SYSTEM_DIR", "")
    cache = Path(system) / "cache/korg-releases.json" if system else None
    raw = None
    if cache and cache.is_file():
        if time.time() - cache.stat().st_mtime < _KORG_CACHE_TTL:
            raw = cache.read_text(errors="replace")
    if raw is None:
        try:
            req = urllib.request.Request(
                _KORG_URL, headers={"User-Agent": _KORG_USER_AGENT}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                raw = resp.read().decode()
            if cache:
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(raw)
                except OSError:
                    pass
        except Exception:
            raw = (
                cache.read_text(errors="replace") if cache and cache.is_file() else None
            )
    if raw is None:
        return []
    try:
        releases = json.loads(raw)["releases"]
    except Exception:
        return []
    out: list[dict] = []
    for rel in releases:
        version = str(rel.get("version", ""))
        if not version:
            continue
        tag = f"v{version}" if _KORG_NUMERIC_RE.match(version) else version
        out.append(
            {
                "tag": tag,
                "moniker": str(rel.get("moniker", "")),
                "iseol": bool(rel.get("iseol")),
            }
        )
    return out


def list_refs(repo: str, filterText: str = "") -> list[dict]:
    """Dynselect options for a repo's refs: branches first, tags newest first.

    The linux picker leads with kernel.org's current releases
    (`releases.json`), each labeled with its moniker (mainline, stable,
    longterm, linux-next) in the upstream order, so the latest of each series
    is a one-click pick; a release the local Bare/mirror does not carry yet is
    labeled `not mirrored` since checking it out needs a mirror fetch first.
    Returns an empty list when neither the Bare nor the mirror exists yet; the
    form's manual-ref toggle stays the entry path on such a host.
    """
    bare = _repo_dir(repo)
    if bare is None:
        return []
    refs = _read_refs(bare)
    needle = (filterText or "").lower()
    options: list[dict] = []
    korg_tags: set[str] = set()
    if repo == "linux":
        for rel in _korg_releases():
            notes = [rel["moniker"]] if rel["moniker"] else []
            if rel["iseol"]:
                notes.append("EOL")
            if rel["tag"] not in refs:
                notes.append("not mirrored")
            label = f"{rel['tag']} ({', '.join(notes)})" if notes else rel["tag"]
            if needle in label.lower():
                korg_tags.add(rel["tag"])
                options.append({"value": rel["tag"], "label": label})
    heads = sorted(n for n, k in refs.items() if k == "head" and needle in n.lower())
    tags = sorted(
        (
            n
            for n, k in refs.items()
            if k == "tag" and n not in korg_tags and needle in n.lower()
        ),
        key=_tag_key,
    )
    options += [{"value": n, "label": n} for n in heads + tags]
    return options[:_MAX_OPTIONS]


def main():
    """Library module imported by the build forms' ref pickers; not a step."""
    return "f/common/gitrefs: bare-repository ref listing"
