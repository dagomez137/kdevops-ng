# SPDX-License-Identifier: copyleft-next-0.3.1
"""Provision the System workbench's merged git mirrors and their refresh timers.

Each project is ONE bare mirror under `WORKERS_DIR/system/mirror/<name>.git` holding
several upstream git trees as remotes that share its single object store (the kernel
mirror carries Linus's tree, -next, -stable, -modules and Axboe's block/io_uring/nvme
tree; QEMU is its own). This step does two things:

1. **Configure each mirror's remotes** from the shared config in `f.workbench.fetch`
   (`default_mirrors`/`remote_url`): the primary tree's heads land at `refs/heads/*`,
   every other tree at `refs/remotes/<tree>/*`, and each remote's clone URL is chosen by
   its `protocol` (git / https / https-googlesource). A bare mirror is created if absent;
   a stale remote not in the config (e.g. an old `--mirror` origin) is removed.
2. **Install the refresh timers**: a `git-mirror@.{service,timer}` pair, one enabled
   instance per mirror, so `git-mirror@<name>` runs `git remote update --prune` (refresh
   every remote) on a self-pacing loop — the first run fires `OnBootSec`/`OnActiveSec`
   after the timer activates, then `OnUnitInactiveSec` after each run finishes.

git is the flake's own resolved path (so the host unit needs nothing on PATH); the units
are driven through `f.common.devshell.Systemd` over the user bus, so this runs on a `vm`
worker. Check with `systemctl --user list-timers 'git-mirror@*'`.
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.devshell import Git, Systemd, _resolve_git
from f.workbench.fetch import DEFAULT_KERNEL_TREES, build_mirrors, remote_url

_SERVICE = """\
[Unit]
Description=Refresh the %i git mirror from upstream
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Nice=19
IOSchedulingClass=idle
ExecStart={git} -C {mirror_dir}/%i.git remote update --prune
"""

_TIMER = """\
[Unit]
Description=Refresh the %i git mirror on a timer

[Timer]
OnBootSec={on_boot}
OnActiveSec={on_boot}
OnUnitInactiveSec={on_inactive}
Persistent=true

[Install]
WantedBy=timers.target
"""


def _write_unit(path: Path, content: str) -> None:
    """Write a unit only when it changed (a same-bytes rewrite still bumps mtime,
    which daemon-reload reads as a fragment change)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.read_text() == content:
            print(f"unchanged {path}", flush=True)
            return
    except FileNotFoundError:
        pass
    path.write_text(content)
    print(f"wrote {path} ({len(content.encode())}B)", flush=True)


def _provision_remotes(git: Git, repo: Path, remotes: list[dict]) -> list[dict]:
    """Configure the bare mirror repo's remotes from the config (idempotent). The
    primary tree fetches into refs/heads/*; the others into refs/remotes/<name>/*."""
    if not (repo / "objects").is_dir():
        repo.parent.mkdir(parents=True, exist_ok=True)
        git.run("init", "--bare", str(repo))
    wanted = {r["name"] for r in remotes}
    for name in git.capture("-C", str(repo), "remote").split():
        if name not in wanted:
            git.run("-C", str(repo), "remote", "remove", name)
            print(f"{repo.name}: removed stale remote {name}", flush=True)
    results = []
    for r in remotes:
        name, url = r["name"], remote_url(r)
        refspec = ("+refs/heads/*:refs/heads/*" if r.get("primary")
                   else f"+refs/heads/*:refs/remotes/{name}/*")
        if git.ok("-C", str(repo), "remote", "get-url", name):
            git.run("-C", str(repo), "remote", "set-url", name, url)
        else:
            git.run("-C", str(repo), "remote", "add", name, url)
        git.run("-C", str(repo), "config", f"remote.{name}.fetch", refspec)
        git.run("-C", str(repo), "config", f"remote.{name}.tagOpt", "--tags")
        # A leftover `--mirror` flag would override our refspec with +refs/*:refs/*.
        git.ok("-C", str(repo), "config", "--unset", f"remote.{name}.mirror")
        print(f"{repo.name}/{name} -> {url}", flush=True)
        results.append({"name": name, "url": url, "primary": bool(r.get("primary"))})
    return results


def main(kernel_trees: list[str] | None = None, protocol: str = "https",
         extra_trees: list[str] | None = None, mirrors: list[dict] | None = None,
         on_boot: str = "10m", on_inactive: str = "10m", mirror_dir: str = "") -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    mdir = Path(mirror_dir) if mirror_dir else workers / "system/mirror"
    mirrors = mirrors or build_mirrors(
        DEFAULT_KERNEL_TREES if kernel_trees is None else kernel_trees,
        protocol, extra_trees or [], mdir)
    git = Git()
    gitbin = _resolve_git(workers)
    unit_dir = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "systemd/user"

    _write_unit(unit_dir / "git-mirror@.service",
                _SERVICE.format(git=gitbin, mirror_dir=mdir))
    _write_unit(unit_dir / "git-mirror@.timer",
                _TIMER.format(on_boot=on_boot, on_inactive=on_inactive))

    sd = Systemd(workers)
    sd.systemctl("daemon-reload")
    wanted = {m["name"] for m in mirrors}
    # Disable any leftover timer for a mirror no longer in the config (e.g. a tree that
    # was merged into another mirror, so its repo is gone and the service would just fail).
    wants = unit_dir / "timers.target.wants"
    for link in sorted(wants.glob("git-mirror@*.timer")):
        inst = link.name[len("git-mirror@"):-len(".timer")]
        if inst not in wanted:
            sd.systemctl("disable", "--now", link.name)
            print(f"disabled stale timer {link.name}", flush=True)
    provisioned = []
    for m in mirrors:
        remotes = _provision_remotes(git, Path(m["mirror"]), m["remotes"])
        # enable (the timers.target symlink, for the next boot) + restart (force a fresh
        # start now with the current template; a plain `enable --now` left an already
        # present timer inactive over the worker's user bus).
        sd.systemctl("enable", f"git-mirror@{m['name']}.timer")
        sd.systemctl("restart", f"git-mirror@{m['name']}.timer")
        provisioned.append({"name": m["name"], "remotes": remotes})

    return {"mirror_dir": str(mdir), "git": gitbin, "on_boot": on_boot,
            "on_inactive": on_inactive, "mirrors": provisioned}
