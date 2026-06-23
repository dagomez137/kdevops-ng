# SPDX-License-Identifier: copyleft-next-0.3.1
"""Provision the System workbench's git-mirror refresh timers (runnable step).

Each local mirror under `WORKERS_DIR/system/mirror/<repo>.git` is a `--mirror`
clone whose `origin` points upstream. This installs a `git-mirror@.{service,timer}`
template pair into the host `systemd --user` manager and enables one instance per
repo, so `git-mirror@<repo>.timer` force-refreshes that mirror from upstream on a
self-pacing loop: the first run fires `OnBootSec`/`OnActiveSec` after the timer
activates (at boot, or when it is enabled — `OnActiveSec` is what makes a timer
enabled long after boot still start), then `OnUnitInactiveSec` after each run
*finishes* (no overlap, and each repo paces independently — a slow `linux` fetch
never delays `qemu`). The Bares borrow these mirrors' objects; this refresh is
separate from (and slower-cadence than) the per-build fetch of the one target ref.

`git` is the flake's own, resolved once to a stable gc-rooted path
(`f.common.devshell._resolve_git` → `shared/gitbin/bin/git`), so the host unit needs
nothing on PATH. Units are written into the host user-manager search path and driven
through `f.common.devshell.Systemd` (systemctl --user over the user bus), exactly as
the qsu steps drive the host manager — so this runs on a `vm` worker.

Equivalent manual workflow:

    install -m644 git-mirror@.service git-mirror@.timer ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now git-mirror@linux.timer git-mirror@qemu.timer ...
    systemctl --user list-timers 'git-mirror@*'
"""

from __future__ import annotations

import os
from pathlib import Path

from f.common.devshell import Systemd, _resolve_git

DEFAULT_REPOS = ["linux", "linux-next", "linux-stable", "linux-modules", "qemu"]

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


def main(repos: list[str] | None = None, on_boot: str = "10m",
         on_inactive: str = "10m", mirror_dir: str = "") -> dict:
    workers = Path(os.environ["WORKERS_DIR"])
    repos = [r.strip() for r in (repos or DEFAULT_REPOS) if r and r.strip()]
    mdir = Path(mirror_dir) if mirror_dir else workers / "system/mirror"
    git = _resolve_git(workers)
    unit_dir = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "systemd/user"

    _write_unit(unit_dir / "git-mirror@.service",
                _SERVICE.format(git=git, mirror_dir=mdir))
    _write_unit(unit_dir / "git-mirror@.timer",
                _TIMER.format(on_boot=on_boot, on_inactive=on_inactive))

    sd = Systemd(workers)
    sd.systemctl("daemon-reload")
    enabled, skipped = [], []
    for repo in repos:
        # Don't enable a timer for a mirror that is not on disk — it would just fail
        # every cycle. The relocation/clone of the mirror itself is provisioned apart.
        if not (mdir / f"{repo}.git").is_dir():
            print(f"skip {repo}: no mirror at {mdir / f'{repo}.git'}", flush=True)
            skipped.append(repo)
            continue
        # enable (the timers.target symlink, for the next boot) + restart (force a
        # fresh start now with the current template; a plain `enable --now` left an
        # already-present timer inactive over the worker's user bus).
        sd.systemctl("enable", f"git-mirror@{repo}.timer")
        sd.systemctl("restart", f"git-mirror@{repo}.timer")
        enabled.append(repo)

    return {"mirror_dir": str(mdir), "git": git, "on_boot": on_boot,
            "on_inactive": on_inactive, "enabled": enabled, "skipped": skipped}
