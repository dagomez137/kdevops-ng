#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1
"""Run every commit check on each commit of a range.

    nix run .#check -- [RANGE]          # default: <base>..HEAD
    nix run .#check -- --tip [RANGE]    # only the range's last commit
    scripts/check.py --b4               # b4's prep-perpatch-check-cmd

Each commit is checked in its own detached worktree, with the flake, scripts
and docs of that commit, so a series passes only if every commit in it
passes: the flake check, check-style.sh, the docs build, and, for a commit
that touches a vendored project, that project's own steps
(scripts/check-vendor.sh). Commits run in parallel, --jobs at a time, as do
the checks of one commit, and report in series order. pyright is advisory: it runs on the base and on the
tip, and each error the tip adds is charged to the commit `git blame` names
for its line. The base is `upstream/main`, else `origin/main`. Worktrees and
logs live under the repository's `.git/kdevops-ng-check/`, not $TMPDIR, which
`nix develop` removes on exit; a passing run removes its own, and pyright
results stay there by tree id.

With --b4, the patch b4 pipes on stdin is matched to its commit in
<base>..HEAD by patch-id, and only findings are printed: an `ERROR:` line per
failed check, which b4 counts as a failure, and a line per new pyright error
charged to that commit, which it counts as a warning. No output means the
patch passed.
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASES = ("upstream/main", "origin/main")
VENDORED = ("nixos-flake", "qemu-system-units", "linux-config-fragments")
PY_INPUTS = (".py", "pyproject.toml", "pyrightconfig.json")
# git takes a lock on the shared worktree list.
WORKTREE = threading.Lock()


def git(*args: str, cwd: Path | None = None, stdin: bytes | None = None) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, input=stdin, capture_output=True, check=True
    )
    return out.stdout.decode()


def base() -> str:
    for ref in BASES:
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            capture_output=True,
        )
        if probe.returncode == 0:
            return ref
    sys.exit(f"check: none of {', '.join(BASES)} exists; pass a RANGE")


def worktree(path: Path, rev: str) -> None:
    with WORKTREE:
        git("worktree", "add", "--detach", "--quiet", str(path), rev)


def drop_worktree(path: Path) -> None:
    with WORKTREE:
        git("worktree", "remove", "--force", str(path))


def changed(rev_range: str) -> list[str]:
    return git("diff", "--name-only", rev_range.replace("..", "...", 1)).split()


def checks(out: Path, touched: list[str]) -> list[tuple[str, list[str], str]]:
    """The gating checks for one commit, each a name, the argv run in the
    commit's tree, and a file that tree must hold for the check to exist."""
    plan = [
        ("flake", ["nix", "flake", "check", "--quiet", "."], "flake.nix"),
        (
            "style",
            [
                "nix",
                "develop",
                ".#checks",
                "--command",
                "bash",
                "scripts/check-style.sh",
            ],
            "scripts/check-style.sh",
        ),
        (
            "docs",
            ["nix", "develop", ".#docs", "--command"]
            + ["sphinx-build", "--quiet", "--builder", "html", "--fail-on-warning"]
            + ["docs", str(out / "docs")],
            "docs/conf.py",
        ),
    ]
    for project in VENDORED:
        mine = [f for f in touched if f.startswith(f"vendor/{project}/")]
        if mine:
            plan.append(
                (
                    f"vendor/{project}",
                    ["nix", "develop", ".#vendor", "--command"]
                    + ["bash", "scripts/check-vendor.sh", project, *mine],
                    "scripts/check-vendor.sh",
                )
            )
    return plan


def quiet(_: str, __: str) -> None:
    pass


def tty_progress():
    """b4 reads the check's stdout and stderr as its verdict and prints a
    patch only once its check ends, so progress goes to the terminal."""
    try:
        tty = open("/dev/tty", "w")  # noqa: SIM115
    except OSError:
        return quiet
    # b4's own grey, green and red dots
    dots = {"pending": 90, "success": 32, "fail": 31}
    return lambda state, line: print(
        f"  \033[{dots[state]}m\u25cf\033[0m check.py: {line}", file=tty, flush=True
    )


def run_check(tree: Path, out: Path, name: str, argv: list[str], say=quiet) -> tuple:
    """Run one check in tree; return its log if it failed, and its lines."""
    log = out / f"{name.replace('/', '-')}.log"
    say("pending", f"{tree.parent.name} {name}: running")
    lines = [f"  $ {shlex.join(argv)}"]
    start = time.monotonic()
    with log.open("wb") as fh:
        rc = subprocess.run(argv, cwd=tree, stdout=fh, stderr=subprocess.STDOUT)
    took = time.monotonic() - start
    state = "fail" if rc.returncode else "success"
    say(state, f"{tree.parent.name} {name}: exit {rc.returncode} ({took:.1f}s)")
    if rc.returncode == 0:
        return None, lines + [f"    {name}: ok ({took:.1f}s)"]
    lines.append(f"    {name}: FAILED, exit {rc.returncode}, log {log} ({took:.1f}s)")
    tail = log.read_text(errors="replace").splitlines()[-20:]
    return log, lines + [f"    | {line}" for line in tail]


def run_commit(
    sha: str, scratch: Path, touched: list[str], say=quiet
) -> tuple[list, list]:
    """Check one commit, its checks side by side; return the failed checks
    with their logs, and the lines to print for it."""
    out = scratch / sha[:12]
    tree = out / "tree"
    out.mkdir(parents=True, exist_ok=True)
    worktree(tree, sha)
    failed, lines = [], [f"  $ cd {tree}"]
    try:
        present = []
        for name, argv, needs in checks(out, touched):
            if (tree / needs).exists():
                present.append((name, argv))
            else:
                lines.append(f"    {name}: skipped, {needs} is not in this commit")
        with ThreadPoolExecutor(len(present) or 1) as pool:
            runs = [
                pool.submit(run_check, tree, out, name, argv, say)
                for name, argv in present
            ]
            for (name, _), run in zip(present, runs, strict=True):
                log, more = run.result()
                lines += more
                if log:
                    failed.append((name, log))
    finally:
        drop_worktree(tree)
    return failed, lines


def pyright(rev: str, home: Path, scratch: Path) -> list[dict]:
    """pyright's errors for rev's tree, cached by tree id, since a tree's
    flake pins the pyright that reads it."""
    tree_id = git("rev-parse", f"{rev}^{{tree}}").strip()
    cache = home / "pyright" / f"{tree_id}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    tree = scratch / f"pyright-{tree_id[:12]}"
    worktree(tree, rev)
    try:
        with (scratch / f"pyright-{tree_id[:12]}.log").open("wb") as fh:
            proc = subprocess.run(
                ["nix", "develop", ".#checks", "--command", "pyright", "--outputjson"],
                cwd=tree,
                stdout=subprocess.PIPE,
                stderr=fh,
            )
            fh.write(proc.stdout)
        report = json.loads(proc.stdout or b"{}")
        errors = [
            {
                "file": str(Path(d["file"]).relative_to(tree)),
                "line": d["range"]["start"]["line"] + 1,
                "rule": d.get("rule", ""),
                "message": d["message"].splitlines()[0],
            }
            for d in report.get("generalDiagnostics", [])
            if d.get("severity") == "error"
        ]
    finally:
        drop_worktree(tree)
    if "generalDiagnostics" in report:
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps(errors))
    return errors


def new_pyright(
    base_rev: str, tip: str, commits: list[str], home: Path, scratch: Path
) -> dict[str, list[str]]:
    """Advisory: the pyright errors tip adds over base_rev, by the commit that
    last touched the line, or by "" when no commit in the range did."""
    with ThreadPoolExecutor(2) as pool:
        before, after = pool.map(
            lambda rev: pyright(rev, home, scratch), (base_rev, tip)
        )

    def key(e: dict) -> tuple[str, str, str]:
        return e["file"], e["rule"], e["message"]

    new = Counter(map(key, after)) - Counter(map(key, before))
    found: dict[str, list[str]] = {}
    for e in after:
        if new[key(e)] <= 0:
            continue
        new[key(e)] -= 1
        blame = git(
            "blame",
            "--porcelain",
            "-L",
            f"{e['line']},{e['line']}",
            tip,
            "--",
            e["file"],
        ).split()[0]
        who = blame if blame in commits else ""
        found.setdefault(who, []).append(f"{e['file']}: {e['message']} ({e['rule']})")
    return found


def first_error(log: Path) -> str:
    """The line that names the failure: a failed Nix build's own log lines
    (prefixed `>`) say more than the `failed to build` wrapper around them."""
    lines = [
        ln
        for ln in log.read_text(errors="replace").splitlines()
        if "(ignored)" not in ln
    ]
    builder = [ln.strip()[1:].strip() for ln in lines if ln.strip().startswith(">")]
    hit = next((ln for ln in builder if ln and not ln.startswith("-->")), None)
    hit = hit or next(
        (ln for ln in lines if "error" in ln.lower() or "warning" in ln.lower()), None
    )
    return (hit or (lines[-1] if lines else "failed")).strip()[:200]


def commit_for_patch(patch: bytes, rev_range: str) -> str | None:
    want = git("patch-id", "--stable", stdin=patch).split()
    if not want:
        return None
    log = subprocess.run(
        ["git", "log", "--patch", "--no-merges", "--format=commit %H", rev_range],
        capture_output=True,
        check=True,
    ).stdout
    for line in git("patch-id", "--stable", stdin=log).splitlines():
        patch_id, sha = line.split()
        if patch_id == want[0]:
            return sha
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "check").splitlines()[0])
    parser.add_argument("range", nargs="?", help="commits to check (<base>..HEAD)")
    parser.add_argument(
        "--b4", action="store_true", help="check the patch on stdin, b4's way"
    )
    parser.add_argument(
        "--tip",
        action="store_true",
        help="check only the last commit, with every vendored project the range "
        "touches",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=max(1, (os.cpu_count() or 4) // 4),
        help="commits checked at once (default: a quarter of the CPUs)",
    )
    args = parser.parse_args()

    os.chdir(git("rev-parse", "--show-toplevel").strip())
    rev_range = args.range or f"{base()}..HEAD"
    base_rev, _, tip_ref = rev_range.partition("..")
    tip = git("rev-parse", tip_ref or "HEAD").strip()
    home = (
        Path(git("rev-parse", "--path-format=absolute", "--git-common-dir").strip())
        / "kdevops-ng-check"
    )
    home.mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="run-", dir=home))
    commits = git("rev-list", "--reverse", "--no-merges", rev_range).split()
    python = any(f.endswith(PY_INPUTS) for f in changed(rev_range))

    if args.b4:
        sha = commit_for_patch(sys.stdin.buffer.read(), rev_range)
        if sha is None:
            print(f"ERROR: check: no commit in {rev_range} matches this patch")
            return 1
        touched = git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()
        say = tty_progress()
        failed, _ = run_commit(sha, scratch, touched, say)
        for name, log in failed:
            print(f"ERROR: {name}: {first_error(log)} (log {log})")
        if python:
            say(
                "pending",
                f"{sha[:12]} pyright: on {base_rev} and the tip, cached by tree",
            )
            found = new_pyright(base_rev, tip, commits, home, scratch)
            for line in found.get(sha, []):
                print(f"pyright (advisory): {line}")
        if not failed:
            shutil.rmtree(scratch)
        return 1 if failed else 0

    series = commits
    if args.tip:
        commits = commits[-1:]
    print(
        f"check: {len(commits)} commits in {rev_range} "
        f"(base {git('rev-parse', '--short', base_rev).strip()}), "
        f"{args.jobs} at a time, {scratch}",
        flush=True,
    )

    def one(sha: str) -> tuple[bool, list[str]]:
        if args.tip:
            touched = changed(rev_range)
        else:
            touched = git("diff-tree", "--no-commit-id", "--name-only", "-r", sha)
            touched = touched.split()
        if not touched:
            return False, ["    skipped: empty commit (a b4 cover letter?)"]
        failed, lines = run_commit(sha, scratch, touched)
        return bool(failed), lines

    with ThreadPoolExecutor(args.jobs + 1) as pool:
        advice = pool.submit(
            lambda: new_pyright(base_rev, tip, series, home, scratch) if python else {}
        )
        bad = 0
        runs = [pool.submit(one, sha) for sha in commits]
        for sha, run in zip(commits, runs, strict=True):
            subject = git("log", "--max-count=1", "--format=%s", sha).strip()
            failed, lines = run.result()
            bad += failed
            print(f"{sha[:12]} {subject}", *lines, sep="\n", flush=True)
        found = advice.result()
    if python:
        count = sum(map(len, found.values()))
        print(f"pyright: {count} new errors over {base_rev} (advisory)")
        for who, lines in found.items():
            label = who[:12] if who else "no commit in the range"
            print(f"  {label}", *(f"    | {line}" for line in lines), sep="\n")
    print(f"check: {len(commits) - bad}/{len(commits)} commits passed")
    if not bad:
        shutil.rmtree(scratch)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
