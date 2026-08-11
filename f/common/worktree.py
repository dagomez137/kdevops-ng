# SPDX-License-Identifier: copyleft-next-0.3.1
"""Shared worktree helpers for the kdevops-ng build steps (not a runnable step).

Imported with:  from f.common.worktree import prepare, prepare_developer

Both lay a worktree of a project off the durable Bare at
`$SYSTEM_DIR/bare/<project>.git` (see `f/workbench/fetch.py`). The Bare borrows the
local mirror's objects, so cutting a worktree is cheap and every worker sees the same
trees. Their `git` comes from the flake (`nixos-flake#git`, resolved once), so the
worker needs only `nix` on PATH; the optional `b4 am` download runs in the
`nixos-flake#build` devShell and `git am` applies its mbox.

ADR-0010 splits the two kinds of worktree, and they want opposite policies, so each has
its own function:

`prepare()` lays the worker's build worktree under `workers/<WORKER_INDEX>/main/<project>`
(the fixed `main` group, since a worker has no developer groups). Nothing but the build
reads it, so it is detached and forced onto the target on every run and cleaned of
untracked files: a build wants a tree that carries the ref and nothing else. `build` and
`destdir` are children of the worktree, so `recreate_worktree=True` (which rm's the
worktree and lays a fresh detached checkout) discards them both; the durable run layer
lives in the Store, not `destdir`.

`prepare_developer()` syncs the developer worktree under
`$WORKTREES_DIR/<worktree-group>/<project>` (the worktree-group root, default the
Workbench; default group `vanilla`; `system` and `workers` are reserved). That is the
tree a developer edits in, so it never discards work. Its whole command vocabulary is
`worktree add` (no `--force`), `checkout <branch>` (no `--force`, no `-B`, no
`--ignore-other-worktrees`), `merge --ff-only`, and the b4 `git am`; it issues no
`--force`, `reset`, `clean`, `stash`, `--discard-changes` or `*-abort` anywhere, so
nothing it can run reverts a tracked edit or drops a staged one. When it cannot reach
the target without destroying something it prints the one specific reason, leaves the
tree exactly as it found it, and returns `synced: False` rather than raising: it runs as
the tail of a build that already succeeded and published, and throwing that away because
a developer's tree is dirty is worse than not syncing.

Equivalent host bash for the worker (PATH includes /nix/var/nix/profiles/default/bin):

    git config --global --add safe.directory '*'          # once per container
    # refresh upstream refs into the Bare's refs/remotes/mirror/* (developer
    # branches already live in the Bare's refs/heads/* on the same host):
    git -C "$BARE" fetch --tags --force --prune mirror
    # resolve to a commit (tag, else mirror/<ref>, else literal) and detach onto it:
    TARGET=$(git -C "$BARE" rev-parse --verify "refs/tags/$ref^{commit}" 2>/dev/null \\
             || git -C "$BARE" rev-parse --verify "mirror/$ref^{commit}" 2>/dev/null \\
             || git -C "$BARE" rev-parse --verify "$ref^{commit}")
    git -C "$BARE" worktree prune
    git -C "$WT" clean --force -d
    git -C "$WT" checkout --detach --force "$TARGET"
    git -C "$BARE" worktree add --force --detach "$WT" "$TARGET"   # if not a checkout yet

and for the developer, whose every command declines rather than clobbers:

    git -C "$BARE" fetch --tags --prune mirror
    # B is refs/heads/<ref> when its tip is TARGET and no other worktree holds it
    git -C "$BARE" for-each-ref \\
        --format='%(refname)|%(objectname)|%(worktreepath)' "refs/heads/$ref"
    git -C "$BARE" worktree add "$WT" "$B"          # absent, with a branch to attach
    git -C "$BARE" worktree add --detach "$WT" "$TARGET"    # absent, without one
    git -C "$WT" checkout "$B"                      # at TARGET but detached: re-attach
    git -C "$WT" merge --ff-only "$TARGET"          # behind TARGET and detached

and, for either, the optional series, in the devShell with cwd=$WT:

    b4 -c b4.midmask=https://lore.kernel.org/all/%s am --outdir "$tmp" "$b4_series"
    # the committer identity is passed per-invocation: a `git config` in a linked
    # worktree writes the Bare's shared config, which every worktree of it inherits
    git -c user.name=kdevops -c user.email=kdevops@kdevops -C "$WT" am "$tmp"/*.mbx
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
    return "f/common/worktree: shared worktree-prepare helpers"


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
    b4_series: str = "",
    label: str = "",
    recreate_worktree: bool = False,
    extra_dirs: tuple = (),
    wipe_dirs: tuple = (),
    version_file: str = "",
) -> dict:
    """Lay the worker's build worktree at `ref` and return its build identity."""
    if ref.startswith("-"):
        raise ValueError(f"invalid ref: {ref}")
    validate_group(worktree_group)

    git = Git()
    _allow_safe_directory(git)

    workers = Path(os.environ["WORKERS_DIR"])
    bare = _bare(project)
    index = os.environ["WORKER_INDEX"]
    worktree = workers / index / "main" / project
    build_dir = worktree / "build"
    _require_build_area(bare, workers)

    print(
        f"worker={index} group={worktree_group} project={project} ref={ref} "
        f"worktree={worktree}",
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
        _sanitize_worktree(git, worktree)
        git.ok("-C", str(worktree), "clean", "--force", "-d")
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
        try:
            b4_label = _apply_b4_series(git, workers, worktree, b4_series)
        except Exception:
            _sanitize_worktree(git, worktree)
            raise
        b4_branch = _publish_b4_branch(git, worktree, b4_series)

    for d in extra_dirs:
        target_dir = worktree / d
        if d in wipe_dirs:
            shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)
    _exclude_dirs(bare, extra_dirs)

    commit = git.capture("-C", str(worktree), "rev-parse", "HEAD").strip()
    _list_dir(worktree)

    result = {
        "project": project,
        # The group the worktree actually landed in, not the requested one: a worker
        # always builds under the fixed `main` group (ADR-0010) regardless of the
        # argument, so returning the argument would name a path that was not laid.
        "worktree_group": "main",
        "worker": index,
        "ref": ref,
        "commit": commit,
        "worktree": str(worktree),
        "label": _compute_label(
            user_label=label,
            b4_series=b4_series,
            b4_label=b4_label,
            is_tag=is_tag,
            ref=ref,
        ),
        "b4_series": b4_series or None,
        "b4_branch": b4_branch,
    }
    if "build" in extra_dirs:
        result["build_dir"] = str(build_dir)
    if "destdir" in extra_dirs:
        result["destdir"] = str(worktree / "destdir")
    if version_file:
        result["version"] = _read_version(worktree, version_file)
    return result


def prepare_developer(
    *,
    project: str,
    ref: str,
    worktree_group: str = "vanilla",
    b4_series: str = "",
    label: str = "",
    recreate_worktree: bool = False,
) -> dict:
    """Sync the developer worktree of `project` in `worktree_group` onto `ref`.

    Best-effort and never destructive (see the module docstring for the vocabulary
    this path is held to). Returns `synced` and, when it is False, the one `reason`
    the tree was left alone. `commit` is always the worktree's own HEAD, so a
    declined sync reports where the tree really is rather than where the build
    wanted it and the manifest stays true.
    """
    if ref.startswith("-"):
        raise ValueError(f"invalid ref: {ref}")
    validate_group(worktree_group)

    git = Git()
    _allow_safe_directory(git)

    workers = Path(os.environ["WORKERS_DIR"])
    bare = _bare(project)
    worktree = worktrees_dir() / worktree_group / project
    _require_build_area(bare, workers)

    print(
        f"developer group={worktree_group} project={project} ref={ref} "
        f"worktree={worktree}",
        flush=True,
    )

    worktree.parent.mkdir(parents=True, exist_ok=True)

    # No `--force`: a moved upstream tag is worth less here than a path with no
    # forcing verb in it at all. A failed fetch is non-fatal, as for the worker.
    if not git.ok("-C", str(bare), "fetch", "--tags", "--prune", "mirror"):
        print(f"note: fetch of {bare} from mirror failed; using local refs", flush=True)
    target, is_tag = _resolve_ref(git, bare, ref)
    git.ok("-C", str(bare), "worktree", "prune")

    reason, commit = _sync(git, bare, worktree, ref, target, recreate_worktree)
    if reason:
        print(f"developer worktree left as it is: {reason}", flush=True)

    b4_branch, b4_label = None, ""
    if b4_series and reason:
        print(f"b4 series not applied: {worktree} carries {_short(commit)}", flush=True)
    elif b4_series:
        b4_label = _apply_b4_series(git, workers, worktree, b4_series)
        b4_branch = _publish_b4_branch(git, worktree, b4_series)
        commit = _head(git, worktree)

    if worktree.exists():
        _list_dir(worktree)

    return {
        "project": project,
        "worktree_group": worktree_group,
        "ref": ref,
        "commit": commit,
        "worktree": str(worktree),
        "label": _compute_label(
            user_label=label,
            b4_series=b4_series,
            b4_label=b4_label,
            is_tag=is_tag,
            ref=ref,
        ),
        "b4_series": b4_series or None,
        "b4_branch": b4_branch,
        "synced": reason is None,
        "reason": reason,
    }


def _sync(
    git: Git,
    bare: Path,
    worktree: Path,
    ref: str,
    target: str,
    recreate_worktree: bool,
) -> tuple[str | None, str | None]:
    """Bring a developer worktree onto `target`, or say why it was left alone.

    Returns `(reason, commit)`: `reason` is None once the tree carries `target`, else
    the one specific thing that could not be done without discarding work, and
    `commit` is the tree's actual HEAD either way. First match wins, in the order
    below, and every branch either issues a command that git itself refuses rather
    than clobbers, or issues none at all.
    """
    if _occupied(worktree):
        if not _is_worktree_of(git, worktree, bare):
            return f"{worktree} is not a worktree of {bare}", _head(git, worktree)
        if recreate_worktree:
            print(f"removing {worktree}: recreate_worktree", flush=True)
            shutil.rmtree(worktree, ignore_errors=True)
            git.ok("-C", str(bare), "worktree", "prune")

    if not _occupied(worktree):
        branch = _attachable_branch(git, bare, ref, target)
        argv = (
            [str(worktree), branch] if branch else ["--detach", str(worktree), target]
        )
        if not git.ok("-C", str(bare), "worktree", "add", *argv):
            return f"could not add {worktree} at {branch or _short(target)}", None
        return None, _head(git, worktree)

    operation = _in_progress(_gitdir(git, worktree))
    if operation:
        return f"an in-progress {operation} holds {worktree}", _head(git, worktree)

    head = _head(git, worktree)
    attached = _attached_branch(git, worktree)
    if head != target:
        if attached:
            return (
                f"{worktree} is on branch {attached} at {_short(head)}, not "
                f"{_short(target)}; a developer's branch ref is never moved",
                head,
            )
        # --ff-only can neither rewind nor orphan, and it aborts on conflicting dirt
        # leaving HEAD where it was. It also reports success for a target already
        # behind HEAD, so the verdict is HEAD itself, not the exit status.
        git.ok("-C", str(worktree), "merge", "--ff-only", target)
        moved = _head(git, worktree)
        if moved != target:
            return (
                f"{worktree} cannot fast-forward from {_short(head)} to "
                f"{_short(target)}",
                moved,
            )
        return None, moved

    if attached:
        print(f"{worktree}: already on {attached} at {_short(head)}", flush=True)
        return None, head
    branch = _attachable_branch(git, bare, ref, target)
    if not branch:
        print(f"{worktree}: already at {_short(head)}, detached", flush=True)
        return None, head
    # Re-attach a tree an earlier detaching run left at the right commit under no
    # branch. checkout carries a modified or staged file across rather than reset it.
    # A failure here (another worktree claimed the branch since the probe) leaves the
    # tree detached at the right commit, which still indexes, so it is a note rather
    # than a decline: the sync's contract is the commit, not the attachment.
    if not git.ok("-C", str(worktree), "checkout", branch):
        print(f"{worktree}: left detached, could not attach {branch}", flush=True)
    return None, head


def _occupied(worktree: Path) -> bool:
    """Whether something already sits at the path (an empty directory does not)."""
    if not worktree.exists():
        return False
    if not worktree.is_dir():
        return True
    return any(worktree.iterdir())


def _is_worktree_of(git: Git, worktree: Path, bare: Path) -> bool:
    """Whether the path is a checkout of this Bare rather than someone else's tree."""
    common = git.capture(
        "-C", str(worktree), "rev-parse", "--git-common-dir", check=False
    ).strip()
    if not common:
        return False
    path = Path(common)
    if not path.is_absolute():
        path = worktree / path
    try:
        return path.resolve() == bare.resolve()
    except OSError:
        return False


def _attachable_branch(git: Git, bare: Path, ref: str, target: str) -> str:
    """The branch `ref` names when it is attachable, else an empty string.

    Attachable means `refs/heads/<ref>` exists, its tip is already `target` (so
    attaching moves no ref and touches no file), and no other worktree has it checked
    out. A bare repository's own HEAD names a branch and reports itself as that
    branch's worktreepath, which is not a real holder: git lets a worktree check it
    out, so the Bare is not counted. One `for-each-ref` answers all three, and its
    pattern matches at `/` boundaries (`refs/heads/b4` also matches `refs/heads/b4/x`),
    so the refname is compared rather than trusted.
    """
    line = git.capture(
        "-C",
        str(bare),
        "for-each-ref",
        "--format=%(refname)|%(objectname)|%(worktreepath)",
        f"refs/heads/{ref}",
        check=False,
    ).strip()
    for candidate in line.splitlines():
        refname, _, rest = candidate.partition("|")
        tip, _, holder = rest.partition("|")
        if refname != f"refs/heads/{ref}" or tip != target:
            continue
        if holder and Path(holder).resolve() != bare.resolve():
            return ""
        return ref
    return ""


def _attached_branch(git: Git, worktree: Path) -> str:
    """The branch HEAD is attached to, or an empty string when HEAD is detached."""
    name = git.capture(
        "-C", str(worktree), "rev-parse", "--symbolic-full-name", "HEAD", check=False
    ).strip()
    prefix = "refs/heads/"
    return name[len(prefix) :] if name.startswith(prefix) else ""


def _head(git: Git, worktree: Path) -> str | None:
    """The worktree's own HEAD commit, or None when nothing readable sits there."""
    return (
        git.capture("-C", str(worktree), "rev-parse", "HEAD", check=False).strip()
        or None
    )


def _short(sha: str | None) -> str:
    """Abbreviate a commit for a log line (`-` when there is none)."""
    return sha[:12] if sha else "-"


def _bare(project: str) -> Path:
    """The project's durable Bare in the System workbench."""
    return system_dir() / "bare" / f"{project}.git"


def _allow_safe_directory(git: Git) -> None:
    """Let git work the build area's trees whatever uid the worker container runs as."""
    existing = git.capture(
        "config", "--global", "--get-all", "safe.directory", check=False
    )
    if "*" not in existing.split("\n"):
        git.run("config", "--global", "--add", "safe.directory", "*")


def _require_build_area(bare: Path, workers: Path) -> None:
    """Fail before touching anything when the Bare or the vendored flake is missing."""
    if not (bare / "objects").is_dir():
        raise FileNotFoundError(f"Bare {bare} missing; run f/workbench/init first")
    if not (vendor_dir(workers) / "nixos-flake/flake.nix").exists():
        raise FileNotFoundError(
            f"nixos-flake devShell missing at {vendor_dir(workers) / 'nixos-flake'}; "
            "provision it first"
        )


def _gitdir(git: Git, worktree: Path) -> Path | None:
    """The worktree's git-dir, resolved the way git itself does (None when unreadable).

    A linked worktree reports an absolute git-dir; a relative one resolves against the
    worktree so the sequencer markers below it are found wherever the step runs.
    """
    text = git.capture(
        "-C", str(worktree), "rev-parse", "--git-dir", check=False
    ).strip()
    if not text:
        return None
    gitdir = Path(text)
    return gitdir if gitdir.is_absolute() else worktree / gitdir


def _in_progress(gitdir: Path | None) -> str | None:
    """Name the sequencer operation in flight in a worktree, or None when it is idle.

    Read off the git-dir the same way git itself does, since no plumbing command
    reports it in one call.
    """
    if gitdir is None:
        return None
    if (gitdir / "rebase-apply").is_dir():
        return "git am" if (gitdir / "rebase-apply/applying").exists() else "rebase"
    if (gitdir / "rebase-merge").is_dir():
        return "rebase"
    for marker, name in (
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
        ("MERGE_HEAD", "merge"),
    ):
        if (gitdir / marker).exists():
            return name
    return None


def _sanitize_worktree(git: Git, worktree: Path) -> None:
    """Clear an interrupted sequencer operation out of the worker's build worktree.

    `git checkout --detach --force` does not clear an interrupted `git am` or rebase,
    so a killed `b4 shazam` leaves a sequencer dir behind and every later build wedges.
    Aborting is safe here and only here: the build worktree is the worker's sandbox and
    holds nothing a person authored. The developer path never calls this.
    """
    gitdir = _gitdir(git, worktree)
    if gitdir is None:
        return

    if (gitdir / "rebase-apply").is_dir():
        if (gitdir / "rebase-apply/applying").exists():
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


def _resolve_ref(git: Git, bare: Path, ref: str) -> tuple[str, bool]:
    """Resolve `ref` to a commit SHA and whether it matched an upstream tag.

    A tag is tried first, then the mirror remote, then the literal ref (a commit, or
    a developer branch in refs/heads/*). Resolving the mirror's branches via
    `refs/remotes/mirror/*` keeps them out of refs/heads/*, where developer pushes
    live (a tag like `v11.0.0` still wins outright). The tag flag drives the `vanilla`
    label for a plain upstream build.
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


def _split_trailing_version(slug: str) -> tuple[str, str]:
    """Split a trailing `-v<N>` revision suffix off a label slug.

    Returns `(head, suffix)` where `suffix` is the trailing `-v\\d+` (including
    its leading dash) when the slug carries one, else `(slug, "")`. The identity
    modules use it to carry a matched series revision through truncation."""
    match = re.search(r"-v\d+$", slug)
    if match:
        return slug[: match.start()], match.group(0)
    return slug, ""


def _apply_b4_series(git: Git, workers: Path, worktree: Path, b4_series: str) -> str:
    """Download the lore series with `b4 am`, apply its mbox with `git am`, and
    return a label slug derived from the series-root (cover) subject.

    `b4 am` writes the patch mbox to an output dir; `git am` of that mbox is
    exactly what `b4 shazam` runs internally, so the applied result is identical.
    The midmask override makes message-id resolution robust regardless of the
    worker's ambient b4 config. `b4 am` does not save the cover letter, which is
    where a series like `... v3` carries its title and revision, so the label
    comes from a separate `b4 mbox --single-message` fetch of the series-root
    message; that fetch is best-effort and falls back to the first patch subject.

    `git am` needs a committer identity the worker container lacks, and it is passed
    per-invocation: `git config user.name` inside a linked worktree writes the Bare's
    shared config, which every worktree cut from that Bare then inherits, so a
    developer's own tree ends up committing as kdevops.
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
        cover_subject = _cover_subject(workers, worktree, b4_series)
        if cover_subject:
            label = _subject_label(cover_subject)
        else:
            label = _subject_label(_first_subject(mbox))
            print(
                "note: no series-root subject; fell back to the patch subject",
                flush=True,
            )
        git.run(
            "-c",
            "user.name=kdevops",
            "-c",
            "user.email=kdevops@kdevops",
            "-C",
            str(worktree),
            "am",
            str(mbox),
        )
    return label


def _publish_b4_branch(git: Git, worktree: Path, b4_series: str) -> str | None:
    """Publish the applied series to the Bare as `b4/<slug>`, or None when it failed.

    A developer can then check it out and iterate (the same host shares the Bare; the
    branch also keeps the commits alive once the build worktree advances to the next
    ref). update-ref, not `branch --force`, which refuses a branch another worktree
    has checked out; a failure is non-fatal, the build already succeeded.
    """
    branch = f"b4/{_b4_slug(b4_series)}"
    if git.ok("-C", str(worktree), "update-ref", f"refs/heads/{branch}", "HEAD"):
        return branch
    print(f"note: could not publish {branch} to the Bare", flush=True)
    return None


def _cover_subject(workers: Path, worktree: Path, b4_series: str) -> str:
    """Fetch the series-root message and return its `Subject:` (best-effort).

    `b4 mbox --single-message` saves exactly the one series-root message,
    normally the cover, whose subject carries the series title and revision that
    `b4 am`'s patch mbox lacks. Returns an empty string when the fetch fails or
    saves no message, so the caller falls back to the patch subject.
    """
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        try:
            DevShell(workers).run(
                "b4",
                "-c",
                "b4.midmask=https://lore.kernel.org/all/%s",
                "mbox",
                "--single-message",
                "--outdir",
                tmp,
                b4_series,
                cwd=str(worktree),
            )
        except Exception:
            return ""
        mboxes = sorted(Path(tmp).glob("*.mbx"))
        return _first_subject(mboxes[0]) if mboxes else ""


def _first_subject(path: Path) -> str:
    """Return the value of the first `Subject:` header in an mbox/cover file."""
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("Subject:"):
            return line[len("Subject:") :].strip()
    return ""


def _subject_label(subject: str) -> str:
    """Slug a patch or cover subject into a build-identity label.

    Accepts `[PATCH[ RFC][ vN][ M/K]] <summary>`, a bracket-less cover
    `<summary> vN`, or a plain `<summary>` with no version. The version `N` is
    read from a `vN` token inside the leading bracket, else from a standalone
    trailing `vN` at the very end of the summary (the `... v3` cover
    convention). A `-v<N>` suffix is appended only when a version token is
    actually matched and is v2 or later; no match means no suffix and no
    invented version. The summary is the text after the final `]` when a bracket
    is present, else the whole subject, with the trailing version token stripped
    so it does not also slug into the title.
    """
    version = None
    bracket = re.match(r"\s*\[(.*?)\]", subject)
    if bracket:
        match = re.search(r"\bv(\d+)\b", bracket.group(1))
        if match:
            version = int(match.group(1))
    summary = subject.rsplit("]", 1)[1] if "]" in subject else subject
    if version is None:
        trailing = re.search(r"(?:^|\s)v(\d+)\s*$", summary)
        if trailing:
            version = int(trailing.group(1))
            summary = summary[: trailing.start()]
    slug = _slug(summary)
    if version is not None and version >= 2:
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
