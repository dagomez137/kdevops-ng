# SPDX-License-Identifier: copyleft-next-0.3.1
"""Shared worktree-prepare helper for the kdevops-ng build steps (not a runnable step).

Imported with:  from f.common.worktree import prepare

`prepare()` lays down one detached worktree of a project off the durable Bare at
`$SYSTEM_DIR/bare/<project>.git` (see `f/workbench/fetch.py`). The Bare borrows the
local mirror's objects, so cutting a worktree is cheap and every worker sees the
same trees. Its `git` comes from the flake (`nixos-flake#git`, resolved once), so
the worker needs only `nix` on PATH; the optional `b4 am` download runs in the
`nixos-flake#build` devShell and `git am` applies its mbox.

A worker build resolves its own warm worktree under its sandbox at
`workers/<WORKER_INDEX>/main/<project>` (the fixed `main` group, since a worker
has no developer groups); a developer worktree (`developer=True`) resolves under
`$WORKTREES_DIR/<worktree-group>/<project>` (the worktree-group root, default the
Workbench; default group `vanilla`; `system` and `workers` are reserved). Both
share the one `<root>/<group>/<project>` shape. The worktree is reused for every
ref and
across runs. `build` and `destdir` are children of the worktree, so
`recreate_worktree=True` (which rm's the worktree and lays a fresh detached
checkout) discards them both; the durable run layer lives in the Store, not
`destdir`.

Equivalent host bash (PATH includes /nix/var/nix/profiles/default/bin):

    git config --global --add safe.directory '*'          # once per container
    # refresh upstream refs into the Bare's refs/remotes/mirror/* (developer
    # branches already live in the Bare's refs/heads/* on the same host):
    git -C "$BARE" fetch --tags --force --prune mirror
    # resolve to a commit (tag, else mirror/<ref>, else literal) and detach onto it:
    TARGET=$(git -C "$BARE" rev-parse --verify "refs/tags/$ref^{commit}" 2>/dev/null \
             || git -C "$BARE" rev-parse --verify "mirror/$ref^{commit}" 2>/dev/null \
             || git -C "$BARE" rev-parse --verify "$ref^{commit}")
    git -C "$BARE" worktree prune
    git -C "$WT" checkout --detach --force "$TARGET"
    git -C "$BARE" worktree add --force --detach "$WT" "$TARGET"   # if not a checkout yet
    git -C "$WT" config user.name kdevops                  # git am needs a committer
    git -C "$WT" config user.email kdevops@kdevops
    # optional, in the devShell, cwd=$WT: download the series, then apply its mbox
    b4 -c b4.midmask=https://lore.kernel.org/all/%s am --outdir "$tmp" "$b4_series"
    git -C "$WT" am "$tmp"/*.mbx
    git -C "$WT" update-ref "refs/heads/b4/$slug" HEAD     # publish the series to the Bare
    git -C "$WT" rev-parse HEAD
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from f.common.devshell import DevShell, Git, system_dir, vendor_dir, worktrees_dir

# Worktree-groups are directories directly under the worktree-group root
# (WORKTREES_DIR, default the Workbench); these names are reserved for the
# build-area infrastructure siblings (`system/`, `workers/`) of the default
# layout and may not be a group.
_RESERVED_GROUPS = ("system", "workers")


def main():
    """This module is a library imported by the build steps, not a runnable step."""
    return "f/common/worktree: shared worktree-prepare helper"


def validate_group(worktree_group: str) -> None:
    """Reject a worktree-group that collides with a reserved sibling or carries
    path/flag characters (it becomes a single directory name directly under the
    Workbench). It must be one plain path component: no `.`/`..`, no separators,
    no whitespace, no leading dash."""
    if (
        not worktree_group
        or worktree_group in (".", "..")
        or worktree_group.startswith("-")
        or any(c.isspace() for c in worktree_group)
        or Path(worktree_group).parts != (worktree_group,)
    ):
        raise ValueError(f"invalid worktree-group: {worktree_group!r}")
    if worktree_group in _RESERVED_GROUPS:
        raise ValueError(
            f"worktree-group {worktree_group!r} is reserved "
            f"(reserved: {', '.join(_RESERVED_GROUPS)})"
        )


def prepare(
    *,
    project: str,
    ref: str,
    worktree_group: str = "vanilla",
    developer: bool = False,
    b4_series: str = "",
    label: str = "",
    recreate_worktree: bool = False,
    extra_dirs: tuple = (),
    wipe_dirs: tuple = (),
    version_file: str = "",
) -> dict:
    if ref.startswith("-"):
        raise ValueError(f"invalid ref: {ref}")
    validate_group(worktree_group)

    git = Git()
    existing = git.capture(
        "config", "--global", "--get-all", "safe.directory", check=False
    )
    if "*" not in existing.split("\n"):
        git.run("config", "--global", "--add", "safe.directory", "*")

    workers = Path(os.environ["WORKERS_DIR"])
    bare = system_dir() / "bare" / f"{project}.git"
    if developer:
        # Developer checkout under the chosen worktree-group.
        root, group, location = worktrees_dir(), worktree_group, worktree_group
    else:
        # Worker sandbox under the fixed `main` group; one checkout per project.
        index = os.environ["WORKER_INDEX"]
        root, group, location = workers / index, "main", index
    worktree = root / group / project
    build_dir = worktree / "build"

    if not (bare / "objects").is_dir():
        raise FileNotFoundError(f"Bare {bare} missing; run f/workbench/init first")
    if not (vendor_dir(workers) / "nixos-flake/flake.nix").exists():
        raise FileNotFoundError(
            f"nixos-flake devShell missing at {vendor_dir(workers) / 'nixos-flake'}; "
            "provision it first"
        )

    who = "developer" if developer else f"worker={location}"
    print(
        f"{who} group={worktree_group} project={project} ref={ref} worktree={worktree}",
        flush=True,
    )

    worktree.parent.mkdir(parents=True, exist_ok=True)

    # Only upstream refs need a fetch; developer branches are already in the Bare's
    # refs/heads/* on the same host. A failed fetch is non-fatal: fall back to local refs.
    if not git.ok("-C", str(bare), "fetch", "--tags", "--force", "--prune", "mirror"):
        print(f"note: fetch of {bare} from mirror failed; using local refs", flush=True)
    target, is_tag = _resolve_ref(git, bare, ref)
    git.run("-C", str(bare), "worktree", "prune")
    if recreate_worktree:
        shutil.rmtree(worktree, ignore_errors=True)
        git.run("-C", str(bare), "worktree", "prune")
    if git.ok("-C", str(worktree), "rev-parse", "--git-dir"):
        _sanitize_worktree(git, worktree, clean=not developer)
        git.run("-C", str(worktree), "checkout", "--detach", "--force", target)
    else:
        shutil.rmtree(worktree, ignore_errors=True)
        git.run(
            "-C",
            str(bare),
            "worktree",
            "add",
            "--force",
            "--detach",
            str(worktree),
            target,
        )
    b4_branch = None
    b4_label = ""
    if b4_series:
        # git am needs a committer identity the worker container lacks.
        git.run("-C", str(worktree), "config", "user.name", "kdevops")
        git.run("-C", str(worktree), "config", "user.email", "kdevops@kdevops")
        b4_label = _apply_b4_series(git, workers, worktree, b4_series)
        # Publish the applied series to the Bare so a developer can check it out and
        # iterate (same host shares the Bare; the branch also keeps the commits alive
        # once `main` advances to the next ref). update-ref, not `branch --force`,
        # which refuses a branch another worktree has checked out; a failure is
        # non-fatal, the build already succeeded.
        b4_branch = f"b4/{_b4_slug(b4_series)}"
        if not git.ok(
            "-C", str(worktree), "update-ref", f"refs/heads/{b4_branch}", "HEAD"
        ):
            print(f"note: could not publish {b4_branch} to the Bare", flush=True)
            b4_branch = None

    for d in extra_dirs:
        target_dir = worktree / d
        if d in wipe_dirs:
            shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)
    _exclude_dirs(bare, extra_dirs)

    commit = git.capture("-C", str(worktree), "rev-parse", "HEAD").strip()
    _list_dir(worktree)

    label = _compute_label(
        user_label=label,
        b4_series=b4_series,
        b4_label=b4_label,
        is_tag=is_tag,
        ref=ref,
    )

    result = {
        "project": project,
        "worktree_group": worktree_group,
        "developer": developer,
        "ref": ref,
        "commit": commit,
        "worktree": str(worktree),
        "label": label,
        "b4_series": b4_series or None,
        "b4_branch": b4_branch,
    }
    if not developer:
        result["worker"] = location
    if "build" in extra_dirs:
        result["build_dir"] = str(build_dir)
    if "destdir" in extra_dirs:
        result["destdir"] = str(worktree / "destdir")
    if version_file:
        result["version"] = _read_version(worktree, version_file)
    return result


def _sanitize_worktree(git: Git, worktree: Path, *, clean: bool) -> None:
    """Make a reused worktree pristine before a fresh detached checkout.

    `git checkout --detach --force` does not clear an interrupted `git am` or
    rebase, so a killed `b4 shazam` leaves a sequencer dir behind and every later
    build wedges. Detect the in-progress operation by inspecting the git-dir the
    same way git itself does, then abort it best-effort.
    """
    gitdir_text = git.capture("-C", str(worktree), "rev-parse", "--git-dir").strip()
    if not gitdir_text:
        return
    # A linked worktree reports an absolute git-dir; resolve a relative one against
    # the worktree so the markers below are found wherever the step runs.
    gitdir = Path(gitdir_text)
    if not gitdir.is_absolute():
        gitdir = worktree / gitdir

    if (gitdir / "rebase-apply").is_dir():
        if (gitdir / "rebase-apply" / "applying").exists():
            print(f"worktree {worktree}: aborting in-progress git am", flush=True)
            git.ok("-C", str(worktree), "am", "--abort")
        else:
            print(f"worktree {worktree}: aborting in-progress rebase", flush=True)
            git.ok("-C", str(worktree), "rebase", "--abort")
    elif (gitdir / "rebase-merge").is_dir():
        print(f"worktree {worktree}: aborting in-progress rebase", flush=True)
        git.ok("-C", str(worktree), "rebase", "--abort")

    if (gitdir / "CHERRY_PICK_HEAD").exists():
        print(f"worktree {worktree}: aborting in-progress cherry-pick", flush=True)
        git.ok("-C", str(worktree), "cherry-pick", "--abort")
    if (gitdir / "REVERT_HEAD").exists():
        print(f"worktree {worktree}: aborting in-progress revert", flush=True)
        git.ok("-C", str(worktree), "revert", "--abort")
    if (gitdir / "MERGE_HEAD").exists():
        print(f"worktree {worktree}: aborting in-progress merge", flush=True)
        git.ok("-C", str(worktree), "merge", "--abort")

    if clean:
        git.ok("-C", str(worktree), "clean", "--force", "-d")


def _resolve_ref(git: Git, bare: Path, ref: str) -> tuple[str, bool]:
    """Resolve `ref` to a commit SHA and whether it matched an upstream tag.

    A tag is tried first, then the mirror remote, then the literal ref (a commit, or
    a developer branch in refs/heads/*). The worktree is always laid down detached,
    so a concrete commit is all the checkout/worktree-add needs, and resolving the
    mirror's branches via `refs/remotes/mirror/*` keeps them out of refs/heads/*,
    where developer pushes live (a tag like `v11.0.0` still wins outright). The tag
    flag drives the `vanilla` label for a plain upstream build.
    """
    for index, candidate in enumerate((f"refs/tags/{ref}", f"mirror/{ref}", ref)):
        sha = git.capture(
            "-C",
            str(bare),
            "rev-parse",
            "--verify",
            "--quiet",
            f"{candidate}^{{commit}}",
            check=False,
        ).strip()
        if sha:
            return sha, index == 0
    raise ValueError(
        f"could not resolve ref {ref!r} in {bare} "
        "(tried a tag, the mirror remote, and the literal ref)"
    )


def _exclude_dirs(bare: Path, extra_dirs: tuple) -> None:
    """Ignore each worktree-local extra dir via the Bare's shared exclude (all worktrees)."""
    gitdir = bare / ".git" if (bare / ".git").is_dir() else bare
    info = gitdir / "info"
    exclude = info / "exclude"
    present = exclude.read_text().splitlines() if exclude.is_file() else []
    missing = [f"/{d}/" for d in extra_dirs if f"/{d}/" not in present]
    if not missing:
        return
    info.mkdir(parents=True, exist_ok=True)
    with exclude.open("a") as handle:
        handle.write("".join(line + "\n" for line in missing))


def _b4_slug(b4_series: str) -> str:
    """Reduce a b4 message-id/URL to a filesystem-safe branch leaf (<=48 chars)."""
    value = b4_series.strip().strip("/")
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    value = value.split("@", 1)[0]
    return _slug(value)[:48] or "series"


def _slug(value: str) -> str:
    """Lowercase a string into a label-safe slug (no truncation): non
    `[A-Za-z0-9._-]` runs collapse to `-`, leading/trailing `-._` stripped."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._").lower()


def _apply_b4_series(git: Git, workers: Path, worktree: Path, b4_series: str) -> str:
    """Download the lore series with `b4 am`, apply its mbox with `git am`, and
    return a label slug from the series subject.

    `b4 am` writes the patch mbox (and a cover letter when the series carries one) to
    an output dir; `git am` of that mbox is exactly what `b4 shazam` runs internally,
    so the applied result is identical. The midmask override makes message-id
    resolution robust regardless of the worker's ambient b4 config. The cover, when
    present, holds the series title and version, so it feeds the label but is never
    passed to `git am`. A failed apply leaves a half-applied state, so it is
    sanitized (the abort path `_sanitize_worktree` handles) before the error
    re-raises.
    """
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        DevShell(workers).run(
            "b4",
            "-c",
            "b4.midmask=https://lore.kernel.org/all/%s",
            "am",
            "--outdir",
            tmp,
            b4_series,
            cwd=str(worktree),
        )
        out = Path(tmp)
        mboxes = sorted(out.glob("*.mbx"))
        if not mboxes:
            raise FileNotFoundError(f"b4 am produced no patch mbox in {out}")
        mbox = mboxes[0]
        covers = sorted(out.glob("*.cover"))
        label = _subject_label(_first_subject(covers[0] if covers else mbox))
        try:
            git.run("-C", str(worktree), "am", str(mbox))
        except Exception:
            _sanitize_worktree(git, worktree, clean=False)
            raise
    return label


def _first_subject(path: Path) -> str:
    """Return the value of the first `Subject:` header in an mbox/cover file."""
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("Subject:"):
            return line[len("Subject:") :].strip()
    return ""


def _subject_label(subject: str) -> str:
    """Slug a patch subject `[PATCH[ RFC][ vN][ M/K]] <summary>` into a label.

    The version `N` (default 1) is read from a `vN` token inside the bracket and
    appended as `-v<N>` only for v2 and later; the summary is the text after the
    final `]`.
    """
    version = 1
    bracket = re.match(r"\s*\[(.*?)\]", subject)
    if bracket:
        match = re.search(r"\bv(\d+)\b", bracket.group(1))
        if match:
            version = int(match.group(1))
    summary = subject.rsplit("]", 1)[1] if "]" in subject else subject
    slug = _slug(summary)
    if version >= 2:
        slug = f"{slug}-v{version}" if slug else f"v{version}"
    return slug


def _compute_label(
    *, user_label: str, b4_series: str, b4_label: str, is_tag: bool, ref: str
) -> str:
    """Pick the readable build-identity label (untruncated; bake_identity fits it).

    Precedence: a non-empty user override, else the b4 series subject, else the
    literal `vanilla` when the ref resolved to an upstream tag with no series, else a
    slug of the ref string (a branch or commit). An empty result means no label, and
    bake_identity falls back to the digest alone.
    """
    if user_label:
        return _slug(user_label)
    if b4_series:
        return b4_label
    if is_tag:
        return "vanilla"
    return _slug(ref)


def _read_version(worktree: Path, version_file: str) -> str | None:
    """Read a version string from `<worktree>/<version_file>` (absent -> None)."""
    path = worktree / version_file
    if not path.is_file():
        return None
    return path.read_text().strip() or None


def _list_dir(path: Path) -> None:
    """Log the worktree directory entry."""
    info = path.stat()
    print(f"{path}  (mode {info.st_mode & 0o777:o})", flush=True)
