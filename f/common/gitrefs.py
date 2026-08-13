# SPDX-License-Identifier: copyleft-next-0.3.1
"""Ref listing for the build forms' ref pickers; imported as f.common.gitrefs.

Lists the branches and tags of a host-local bare repository by reading its
`packed-refs` file and loose `refs/` entries directly (no `git` subprocess, so
a form dynselect answers fast). The universe is the durable Bare the worktree
steps check out from (`$SYSTEM_DIR/bare/<repo>.git`), which carries developer
branches plus the mirror-fetched upstream refs; before the first fetch it
falls back to the mirror (`$MIRRORS_DIR/<repo>.git`). Developer branches come
first, then the mirror remote's branches as `mirror/<branch>` (a fresh Bare
has no local heads, so the upstream tips live there), then tags newest
version first, so the zero-config pick is the tip of a tree or the latest
release. The linux picker additionally leads with kernel.org's current
releases (`releases.json`), labeled by moniker, so the latest mainline,
stable, longterm, or linux-next is a one-click pick.

Every source is best-effort because a dynselect must never block: ref reads
that race git maintenance are skipped, and the kernel.org fetch runs under a
hard wall-clock deadline with failed attempts throttled. The worst outcome is
stale or partial data, returned fast.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path

_MAX_OPTIONS = 200

_VERSION_RE = re.compile(
    r"^v(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?(?:-rc(?P<rc>\d+))?$"
)

_KORG_URL = "https://www.kernel.org/releases.json"
_KORG_CACHE_TTL = 3600
# After a failed fetch no new attempt is made for this many seconds; the
# stale cache keeps being served meanwhile.
_KORG_RETRY_WINDOW = 60
# Hard wall-clock bound on the whole fetch. urlopen's timeout starts after
# DNS resolution, so it alone cannot bound a broken resolver.
_KORG_FETCH_DEADLINE = 3.0
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


# Picker namespaces: the key is the value the form submits, so a mirror
# branch keeps its `mirror/` prefix and resolves as typed.
_REF_KINDS = (
    ("head", "refs/heads/", ""),
    ("mirror", "refs/remotes/mirror/", "mirror/"),
    ("tag", "refs/tags/", ""),
)


def _read_refs(bare: Path) -> dict[str, str]:
    """Picker names (`refs/heads`, `refs/remotes/mirror`, `refs/tags`) -> kind.

    Loose entries win over packed ones. A source that fails mid-read (git
    maintenance repacking or pruning underneath us) is skipped; partial data
    beats an exception in a picker.
    """
    refs: dict[str, str] = {}
    packed = bare / "packed-refs"
    try:
        lines = packed.read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        if not line or line.startswith(("#", "^")):
            continue
        _, _, name = line.partition(" ")
        for kind, prefix, keyed in _REF_KINDS:
            if name.startswith(prefix):
                refs[keyed + name[len(prefix) :]] = kind
    for kind, prefix, keyed in _REF_KINDS:
        root = bare / "refs" / prefix[len("refs/") : -1]
        try:
            if root.is_dir():
                for path in root.rglob("*"):
                    if path.is_file():
                        refs[keyed + str(path.relative_to(root))] = kind
        except OSError:
            pass
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


def _mtime_within(path: Path | None, window: float) -> bool:
    """True when `path` exists and was modified less than `window` seconds ago."""
    if path is None:
        return False
    try:
        return time.time() - path.stat().st_mtime < window
    except OSError:
        return False


def _read_cache(cache: Path | None) -> str | None:
    if cache is None:
        return None
    try:
        return cache.read_text(errors="replace")
    except OSError:
        return None


def _write_atomic(cache: Path, raw: str) -> None:
    """Publish via a same-directory temp file and `os.replace`.

    A concurrent dynselect job reading the cache sees the old file or the new
    one, never a torn half-write.
    """
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_name(f".{cache.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(raw)
        os.replace(tmp, cache)
    finally:
        tmp.unlink(missing_ok=True)


def _fetch_korg() -> str:
    """One blocking `releases.json` GET; runs inside the deadline thread."""
    req = urllib.request.Request(_KORG_URL, headers={"User-Agent": _KORG_USER_AGENT})
    with urllib.request.urlopen(req, timeout=_KORG_FETCH_DEADLINE) as resp:
        return resp.read().decode()


def _fetch_korg_bounded() -> str | None:
    """`_fetch_korg` under a hard wall-clock deadline; None on any failure.

    urlopen's timeout does not bound getaddrinfo, so a broken resolver can
    hold a plain fetch for 30s+. The fetch runs in a one-shot daemon thread
    that is abandoned at the deadline; the job process exits right after, so
    an orphaned thread never lingers for long.
    """
    result: list[str] = []

    def fetch() -> None:
        try:
            result.append(_fetch_korg())
        except Exception:
            pass

    worker = threading.Thread(target=fetch, daemon=True)
    worker.start()
    worker.join(_KORG_FETCH_DEADLINE)
    return result[0] if result else None


def _parse_korg(raw: str | None) -> list[dict] | None:
    """`releases.json` text -> `{tag, moniker, iseol}` rows; None if malformed."""
    if raw is None:
        return None
    out: list[dict] = []
    try:
        for rel in json.loads(raw)["releases"]:
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
    except Exception:
        return None
    return out


def _korg_releases() -> list[dict]:
    """kernel.org's current releases as `{tag, moniker, iseol}`, best-effort.

    The JSON is disk-cached for `_KORG_CACHE_TTL` seconds because a dynselect
    re-runs on every filter keystroke. The fetch is bounded by a wall-clock
    deadline (DNS included) and only a payload that parses is cached, written
    atomically. A failed attempt stamps a sidecar marker so no new attempt is
    made for `_KORG_RETRY_WINDOW` seconds; either way the stale cache, then an
    empty list, keeps being served, so the picker never blocks on the network.
    """
    system = os.environ.get("SYSTEM_DIR", "")
    cache = Path(system) / "cache/korg-releases.json" if system else None
    marker = cache.with_name(cache.name + ".attempt") if cache else None
    if _mtime_within(cache, _KORG_CACHE_TTL):
        fresh = _parse_korg(_read_cache(cache))
        if fresh is not None:
            return fresh
    if not _mtime_within(marker, _KORG_RETRY_WINDOW):
        raw = _fetch_korg_bounded()
        fetched = _parse_korg(raw)
        if fetched is not None:
            if cache and raw is not None:
                try:
                    _write_atomic(cache, raw)
                except OSError:
                    pass
            if marker:
                try:
                    marker.unlink(missing_ok=True)
                except OSError:
                    pass
            return fetched
        if marker:
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
            except OSError:
                pass
    stale = _parse_korg(_read_cache(cache))
    return stale if stale is not None else []


def list_refs(repo: str, filterText: str = "") -> list[dict]:
    """Dynselect options for a repo's refs: branches, mirror branches, tags.

    Developer branches come first, then the mirror remote's branches as
    `mirror/<branch>`, then tags newest first. The linux picker leads with
    kernel.org's current releases (`releases.json`), each labeled with its
    moniker (mainline, stable, longterm, linux-next) in the upstream order,
    so the latest of each series is a one-click pick; a release the local
    Bare/mirror does not carry yet is labeled `not mirrored` since checking
    it out needs a mirror fetch first. Returns an empty list when neither
    the Bare nor the mirror exists yet; the form's manual-ref toggle stays
    the entry path on such a host.
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
    mirrors = sorted(
        n for n, k in refs.items() if k == "mirror" and needle in n.lower()
    )
    tags = sorted(
        (
            n
            for n, k in refs.items()
            if k == "tag" and n not in korg_tags and needle in n.lower()
        ),
        key=_tag_key,
    )
    options += [{"value": n, "label": n} for n in heads + mirrors + tags]
    return options[:_MAX_OPTIONS]


def _read_refnames(bare: Path) -> set[str]:
    """All full refnames, packed plus loose, best-effort like `_read_refs`."""
    names: set[str] = set()
    try:
        lines = (bare / "packed-refs").read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        if not line or line.startswith(("#", "^")):
            continue
        _, _, name = line.partition(" ")
        if name.startswith("refs/"):
            names.add(name)
    for sub in ("heads", "tags", "remotes"):
        root = bare / "refs" / sub
        try:
            if root.is_dir():
                for path in root.rglob("*"):
                    if path.is_file():
                        names.add(f"refs/{sub}/{path.relative_to(root)}")
        except OSError:
            pass
    return names


def qualify_ref(repo: str, ref: str) -> str | None:
    """The repo's fully qualified refname for `ref`, or None.

    The resolution order matches the worktree steps (`f.common.worktree`):
    an upstream tag wins, then the mirror remote's branches, then a
    developer branch, then any remote-tracking ref (a picked
    `mirror/<branch>` value, or a peer branch). A fetcher needs the full
    refname because it reads an unqualified one as `refs/heads/<ref>`; an
    already-qualified `refs/...` value passes through when it exists.
    """
    bare = _repo_dir(repo)
    if bare is None:
        return None
    names = _read_refnames(bare)
    candidates = (
        [ref]
        if ref.startswith("refs/")
        else [
            f"refs/tags/{ref}",
            f"refs/remotes/mirror/{ref}",
            f"refs/heads/{ref}",
            f"refs/remotes/{ref}",
        ]
    )
    for candidate in candidates:
        if candidate in names:
            return candidate
    return None


def main():
    """Library module imported by the build forms' ref pickers; not a step."""
    return "f/common/gitrefs: bare-repository ref listing"
