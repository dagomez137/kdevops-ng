# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the worktree path addressing, argv, and developer sync policy
(`f.common.worktree`)."""

import ast
import inspect
import subprocess
from pathlib import Path
from typing import cast

import pytest

from f.common import worktree

ENV = (
    "WORKBENCH_DIR",
    "WORKERS_DIR",
    "WORKTREES_DIR",
    "SYSTEM_DIR",
    "VENDOR_DIR",
    "WORKER_INDEX",
)

TARGET = "a" * 40
OTHER = "b" * 40


class _GitLog:
    """Argv-recording stand-in for the flake `git`: no subprocess ever runs.

    `answers` pairs a marker tuple with the stdout `capture` returns for any argv
    carrying every marker, first match winning, so the more specific markers come
    first (`--symbolic-full-name` before a bare `HEAD`). `refuse` lists markers whose
    argv `ok` reports as a failure. Every call is recorded, so a test reads back the
    exact sequence a step issued.
    """

    def __init__(self, answers=(), refuse=()):
        self.argv: list[tuple[str, ...]] = []
        self._answers = tuple(answers)
        self._refuse = tuple(refuse)

    def __call__(self, *args, **kwargs):
        return self

    def run(self, *args, check=True):
        self.argv.append(args)
        # Mirror `Git.run`, which defaults to check=True and so raises: a refused
        # argv issued through `run` must fail a test the way it would fail a flow.
        if check and any(marker in args for marker in self._refuse):
            raise subprocess.CalledProcessError(1, args)
        self._lay(args)
        return 0

    def ok(self, *args):
        self.argv.append(args)
        allowed = not any(marker in args for marker in self._refuse)
        if allowed:
            self._lay(args)
        return allowed

    def capture(self, *args, check=True):
        self.argv.append(args)
        for markers, value in self._answers:
            if all(marker in args for marker in markers):
                return value
        return ""

    def issued(self, *markers):
        """Every recorded argv carrying all of `markers`."""
        return [a for a in self.argv if all(m in a for m in markers)]

    @staticmethod
    def _lay(args):
        """`worktree add` makes the checkout dir, which the step then stats."""
        if "worktree" in args and "add" in args:
            Path(args[-2]).mkdir(parents=True, exist_ok=True)


def _resolves(ref, sha=TARGET, *, as_tag=True):
    """Answers landing `_resolve_ref` on `sha`, as an upstream tag or a plain ref."""
    if as_tag:
        return [((f"refs/tags/{ref}^{{commit}}",), sha)]
    return [
        ((f"refs/tags/{ref}^{{commit}}",), ""),
        ((f"mirror/{ref}^{{commit}}",), ""),
        ((f"{ref}^{{commit}}",), sha),
    ]


def _git(monkeypatch, *answers, refuse=()):
    """Install a recording git programmed with `answers` and hand it back."""
    log = _GitLog([*answers, (("--get-all",), "*\n")], refuse)
    monkeypatch.setattr(worktree, "Git", log)
    return log


@pytest.fixture
def build_area(monkeypatch, tmp_path):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path / "workers"))
    monkeypatch.setenv("VENDOR_DIR", str(tmp_path / "vendor"))
    (tmp_path / "system/bare/linux.git/objects").mkdir(parents=True)
    (tmp_path / "vendor/nixos-flake").mkdir(parents=True)
    (tmp_path / "vendor/nixos-flake/flake.nix").write_text("")
    return tmp_path


@pytest.fixture
def bare(build_area):
    return build_area / "system/bare/linux.git"


@pytest.fixture
def developer(build_area):
    """The default developer worktree path, and the git-dir a Bare would give it."""
    return build_area / "vanilla" / "linux"


def _occupy(path, gitdir):
    """Make a path look occupied and give it a git-dir for the sequencer probes."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".keep").write_text("")
    gitdir.mkdir(parents=True, exist_ok=True)


# --- the worker build worktree ---------------------------------------------------


def test_worker_worktree_roots_under_the_fixed_main_group(build_area, monkeypatch):
    monkeypatch.setenv("WORKER_INDEX", "3")
    _git(monkeypatch, *_resolves("v6.9"), (("rev-parse", "HEAD"), TARGET))
    result = worktree.prepare(
        project="linux",
        ref="v6.9",
        worktree_group="ignored",
        extra_dirs=("build", "destdir"),
    )
    expected = build_area / "workers" / "3" / "main" / "linux"
    assert result["worktree"] == str(expected)
    assert result["worker"] == "3"
    # The reported group is the one built in, the fixed `main`, not any argument.
    assert result["worktree_group"] == "main"
    assert result["build_dir"] == str(expected / "build")
    assert result["destdir"] == str(expected / "destdir")
    assert result["commit"] == TARGET
    assert result["label"] == "vanilla"


def test_worker_reuse_issues_the_unchanged_argv(build_area, bare, monkeypatch):
    """The forced, cleaning reuse path is the regression guard for the helper split."""
    monkeypatch.setenv("WORKER_INDEX", "3")
    wt = build_area / "workers/3/main/linux"
    gitdir = bare / "worktrees/linux"
    _occupy(wt, gitdir)
    git = _git(
        monkeypatch,
        *_resolves("v6.9"),
        (("--git-dir",), str(gitdir)),
        (("rev-parse", "HEAD"), TARGET),
    )
    worktree.prepare(project="linux", ref="v6.9")
    assert git.argv == [
        ("config", "--global", "--get-all", "safe.directory"),
        ("-C", str(bare), "fetch", "--tags", "--force", "--prune", "mirror"),
        (
            "-C",
            str(bare),
            "rev-parse",
            "--verify",
            "--quiet",
            "refs/tags/v6.9^{commit}",
        ),
        ("-C", str(bare), "worktree", "prune"),
        ("-C", str(wt), "rev-parse", "--git-dir"),
        ("-C", str(wt), "rev-parse", "--git-dir"),
        ("-C", str(wt), "clean", "--force", "-d"),
        ("-C", str(wt), "checkout", "--detach", "--force", TARGET),
        ("-C", str(wt), "rev-parse", "HEAD"),
    ]


def test_worker_fresh_issues_the_unchanged_argv(build_area, bare, monkeypatch):
    monkeypatch.setenv("WORKER_INDEX", "3")
    wt = build_area / "workers/3/main/linux"
    git = _git(
        monkeypatch,
        *_resolves("v6.9"),
        (("rev-parse", "HEAD"), TARGET),
        refuse=("--git-dir",),
    )
    worktree.prepare(project="linux", ref="v6.9")
    assert git.argv == [
        ("config", "--global", "--get-all", "safe.directory"),
        ("-C", str(bare), "fetch", "--tags", "--force", "--prune", "mirror"),
        (
            "-C",
            str(bare),
            "rev-parse",
            "--verify",
            "--quiet",
            "refs/tags/v6.9^{commit}",
        ),
        ("-C", str(bare), "worktree", "prune"),
        ("-C", str(wt), "rev-parse", "--git-dir"),
        ("-C", str(bare), "worktree", "add", "--force", "--detach", str(wt), TARGET),
        ("-C", str(wt), "rev-parse", "HEAD"),
    ]


def test_worker_recreate_prunes_twice(build_area, bare, monkeypatch):
    monkeypatch.setenv("WORKER_INDEX", "3")
    git = _git(
        monkeypatch,
        *_resolves("v6.9"),
        (("rev-parse", "HEAD"), TARGET),
        refuse=("--git-dir",),
    )
    worktree.prepare(project="linux", ref="v6.9", recreate_worktree=True)
    assert git.issued("worktree", "prune") == [
        ("-C", str(bare), "worktree", "prune"),
        ("-C", str(bare), "worktree", "prune"),
    ]


def test_prepare_rejects_a_flag_shaped_ref():
    with pytest.raises(ValueError, match="invalid ref"):
        worktree.prepare(project="linux", ref="--exec=evil")


# --- the developer worktree: addressing ------------------------------------------


def test_developer_worktree_roots_under_its_group(build_area, monkeypatch):
    git = _git(monkeypatch, *_resolves("v6.9"), (("rev-parse", "HEAD"), TARGET))
    result = worktree.prepare_developer(
        project="linux", ref="v6.9", worktree_group="lace"
    )
    assert result["worktree"] == str(build_area / "lace" / "linux")
    assert result["worktree_group"] == "lace"
    assert "worker" not in result
    assert result["label"] == "vanilla"
    assert (result["synced"], result["reason"], result["commit"]) == (
        True,
        None,
        TARGET,
    )
    assert git.issued("worktree", "add")


def test_worktrees_dir_relocates_the_developer_groups(build_area, monkeypatch):
    monkeypatch.setenv("WORKTREES_DIR", str(build_area / "trees"))
    _git(monkeypatch, *_resolves("v6.9"), (("rev-parse", "HEAD"), TARGET))
    result = worktree.prepare_developer(project="linux", ref="v6.9")
    assert result["worktree"] == str(build_area / "trees" / "vanilla" / "linux")


def test_prepare_developer_rejects_a_flag_shaped_ref():
    with pytest.raises(ValueError, match="invalid ref"):
        worktree.prepare_developer(project="linux", ref="--exec=evil")


def test_developer_fetch_carries_no_forcing_flag(build_area, bare, monkeypatch):
    git = _git(monkeypatch, *_resolves("v6.9"), (("rev-parse", "HEAD"), TARGET))
    worktree.prepare_developer(project="linux", ref="v6.9")
    assert git.issued("fetch") == [
        ("-C", str(bare), "fetch", "--tags", "--prune", "mirror")
    ]


# --- the developer worktree: one test per row of the state table -----------------


def test_a_foreign_path_is_declined_and_never_removed(
    build_area, bare, developer, monkeypatch
):
    _occupy(developer, build_area / "elsewhere.git")
    git = _git(
        monkeypatch,
        *_resolves("v6.9"),
        (("--git-common-dir",), str(build_area / "elsewhere.git")),
        (("rev-parse", "HEAD"), OTHER),
    )
    result = worktree.prepare_developer(project="linux", ref="v6.9")
    assert result["synced"] is False
    assert result["reason"] == f"{developer} is not a worktree of {bare}"
    assert result["commit"] == OTHER
    assert (developer / ".keep").is_file()
    assert not git.issued("worktree", "add")
    assert not git.issued("checkout")


@pytest.mark.parametrize(
    "marker,operation",
    [
        ("rebase-apply/applying", "git am"),
        ("rebase-apply/patch", "rebase"),
        ("rebase-merge/done", "rebase"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
        ("MERGE_HEAD", "merge"),
    ],
)
def test_an_in_progress_operation_is_declined_by_name(
    bare, developer, monkeypatch, marker, operation
):
    gitdir = bare / "worktrees/linux"
    _occupy(developer, gitdir)
    (gitdir / marker).parent.mkdir(parents=True, exist_ok=True)
    (gitdir / marker).write_text("")
    git = _git(
        monkeypatch,
        *_resolves("v6.9"),
        (("--git-common-dir",), str(bare)),
        (("--git-dir",), str(gitdir)),
        (("rev-parse", "HEAD"), OTHER),
    )
    result = worktree.prepare_developer(project="linux", ref="v6.9")
    assert result["synced"] is False
    assert result["reason"] == f"an in-progress {operation} holds {developer}"
    assert result["commit"] == OTHER
    assert not git.issued("checkout")
    assert not git.issued("merge")


def test_already_at_the_target_on_a_branch_issues_no_command(
    bare, developer, monkeypatch
):
    gitdir = bare / "worktrees/linux"
    _occupy(developer, gitdir)
    git = _git(
        monkeypatch,
        *_resolves("wip", as_tag=False),
        (("--git-common-dir",), str(bare)),
        (("--git-dir",), str(gitdir)),
        (("--symbolic-full-name",), "refs/heads/wip\n"),
        (("rev-parse", "HEAD"), TARGET),
    )
    result = worktree.prepare_developer(project="linux", ref="wip")
    assert (result["synced"], result["reason"], result["commit"]) == (
        True,
        None,
        TARGET,
    )
    assert not git.issued("checkout")
    assert not git.issued("merge")
    assert not git.issued("worktree", "add")
    assert not git.issued("for-each-ref")


def test_detached_at_the_target_re_attaches_to_the_branch(bare, developer, monkeypatch):
    gitdir = bare / "worktrees/linux"
    _occupy(developer, gitdir)
    git = _git(
        monkeypatch,
        *_resolves("wip", as_tag=False),
        (("--git-common-dir",), str(bare)),
        (("--git-dir",), str(gitdir)),
        (("--symbolic-full-name",), "HEAD\n"),
        (("rev-parse", "HEAD"), TARGET),
        (("for-each-ref",), f"refs/heads/wip|{TARGET}|\n"),
    )
    result = worktree.prepare_developer(project="linux", ref="wip")
    assert (result["synced"], result["reason"]) == (True, None)
    assert git.issued("checkout") == [("-C", str(developer), "checkout", "wip")]


def test_a_failed_re_attach_is_a_note_and_not_a_decline(bare, developer, monkeypatch):
    """The sync's contract is the commit, not the attachment.

    A tree left detached at the built commit still indexes, so losing the race for
    the branch must not cost the developer their index. It must also never raise:
    this step is the tail of a build that already published.
    """
    gitdir = bare / "worktrees/linux"
    _occupy(developer, gitdir)
    git = _git(
        monkeypatch,
        *_resolves("wip", as_tag=False),
        (("--git-common-dir",), str(bare)),
        (("--git-dir",), str(gitdir)),
        (("--symbolic-full-name",), "HEAD\n"),
        (("rev-parse", "HEAD"), TARGET),
        (("for-each-ref",), f"refs/heads/wip|{TARGET}|\n"),
        refuse=("checkout",),
    )
    result = worktree.prepare_developer(project="linux", ref="wip")
    assert (result["synced"], result["reason"]) == (True, None)
    assert result["commit"] == TARGET
    assert git.issued("checkout") == [("-C", str(developer), "checkout", "wip")]


def test_a_failed_prune_does_not_fail_the_flow(bare, developer, monkeypatch):
    """No git call on the developer path may raise, whatever git says about it."""
    gitdir = bare / "worktrees/linux"
    _occupy(developer, gitdir)
    git = _git(
        monkeypatch,
        *_resolves("wip", as_tag=False),
        (("--git-common-dir",), str(bare)),
        (("--git-dir",), str(gitdir)),
        (("--symbolic-full-name",), "refs/heads/wip\n"),
        (("rev-parse", "HEAD"), TARGET),
        (("for-each-ref",), f"refs/heads/wip|{TARGET}|\n"),
        refuse=("prune",),
    )
    result = worktree.prepare_developer(project="linux", ref="wip")
    assert (result["synced"], result["commit"]) == (True, TARGET)
    assert git.issued("prune")


def test_detached_at_the_target_with_no_branch_does_nothing(
    bare, developer, monkeypatch
):
    gitdir = bare / "worktrees/linux"
    _occupy(developer, gitdir)
    git = _git(
        monkeypatch,
        *_resolves("v6.9"),
        (("--git-common-dir",), str(bare)),
        (("--git-dir",), str(gitdir)),
        (("--symbolic-full-name",), "HEAD\n"),
        (("rev-parse", "HEAD"), TARGET),
    )
    result = worktree.prepare_developer(project="linux", ref="v6.9")
    assert (result["synced"], result["reason"], result["commit"]) == (
        True,
        None,
        TARGET,
    )
    assert not git.issued("checkout")
    assert not git.issued("merge")


def test_a_branch_another_worktree_holds_is_not_attachable(
    build_area, bare, developer, monkeypatch
):
    gitdir = bare / "worktrees/linux"
    _occupy(developer, gitdir)
    git = _git(
        monkeypatch,
        *_resolves("wip", as_tag=False),
        (("--git-common-dir",), str(bare)),
        (("--git-dir",), str(gitdir)),
        (("--symbolic-full-name",), "HEAD\n"),
        (("rev-parse", "HEAD"), TARGET),
        (("for-each-ref",), f"refs/heads/wip|{TARGET}|{build_area / 'other'}\n"),
    )
    result = worktree.prepare_developer(project="linux", ref="wip")
    assert (result["synced"], result["reason"]) == (True, None)
    assert not git.issued("checkout")


def test_the_bare_itself_is_not_counted_as_a_holder(bare, developer, monkeypatch):
    """A bare repository reports itself as its own HEAD branch's worktreepath, and
    git still lets a worktree check that branch out."""
    gitdir = bare / "worktrees/linux"
    _occupy(developer, gitdir)
    git = _git(
        monkeypatch,
        *_resolves("wip", as_tag=False),
        (("--git-common-dir",), str(bare)),
        (("--git-dir",), str(gitdir)),
        (("--symbolic-full-name",), "HEAD\n"),
        (("rev-parse", "HEAD"), TARGET),
        (("for-each-ref",), f"refs/heads/wip|{TARGET}|{bare}\n"),
    )
    worktree.prepare_developer(project="linux", ref="wip")
    assert git.issued("checkout") == [("-C", str(developer), "checkout", "wip")]


def test_a_prefix_match_is_not_mistaken_for_the_branch(bare, developer, monkeypatch):
    """`for-each-ref refs/heads/b4` also lists `refs/heads/b4/x`, so the refname is
    compared rather than trusted."""
    gitdir = bare / "worktrees/linux"
    _occupy(developer, gitdir)
    git = _git(
        monkeypatch,
        *_resolves("b4", as_tag=False),
        (("--git-common-dir",), str(bare)),
        (("--git-dir",), str(gitdir)),
        (("--symbolic-full-name",), "HEAD\n"),
        (("rev-parse", "HEAD"), TARGET),
        (("for-each-ref",), f"refs/heads/b4/series|{TARGET}|\n"),
    )
    result = worktree.prepare_developer(project="linux", ref="b4")
    assert result["synced"] is True
    assert not git.issued("checkout")


def test_behind_the_target_on_a_branch_is_declined(bare, developer, monkeypatch):
    gitdir = bare / "worktrees/linux"
    _occupy(developer, gitdir)
    git = _git(
        monkeypatch,
        *_resolves("v6.9"),
        (("--git-common-dir",), str(bare)),
        (("--git-dir",), str(gitdir)),
        (("--symbolic-full-name",), "refs/heads/rxarray-wip\n"),
        (("rev-parse", "HEAD"), OTHER),
    )
    result = worktree.prepare_developer(project="linux", ref="v6.9")
    assert result["synced"] is False
    assert result["reason"] == (
        f"{developer} is on branch rxarray-wip at {OTHER[:12]}, not {TARGET[:12]}; "
        "a developer's branch ref is never moved"
    )
    assert result["commit"] == OTHER
    assert not git.issued("checkout")
    assert not git.issued("merge")


def test_behind_the_target_detached_fast_forwards(bare, developer, monkeypatch):
    gitdir = bare / "worktrees/linux"
    _occupy(developer, gitdir)
    heads = iter([OTHER, TARGET])

    class _Moving(_GitLog):
        """HEAD moves once, the way a real fast-forward moves it."""

        def capture(self, *args, check=True):
            if args[-1] == "HEAD" and "--symbolic-full-name" not in args:
                self.argv.append(args)
                return next(heads)
            return super().capture(*args, check=check)

    git = _Moving(
        [
            *_resolves("v6.9"),
            (("--git-common-dir",), str(bare)),
            (("--git-dir",), str(gitdir)),
            (("--symbolic-full-name",), "HEAD\n"),
            (("--get-all",), "*\n"),
        ]
    )
    monkeypatch.setattr(worktree, "Git", git)
    result = worktree.prepare_developer(project="linux", ref="v6.9")
    assert (result["synced"], result["reason"], result["commit"]) == (
        True,
        None,
        TARGET,
    )
    assert git.issued("merge") == [("-C", str(developer), "merge", "--ff-only", TARGET)]


def test_a_fast_forward_that_does_not_move_head_is_declined(
    bare, developer, monkeypatch
):
    """`merge --ff-only` on to an ancestor prints `Already up to date` and exits 0, so
    HEAD itself is the verdict rather than the exit status."""
    gitdir = bare / "worktrees/linux"
    _occupy(developer, gitdir)
    git = _git(
        monkeypatch,
        *_resolves("v6.9"),
        (("--git-common-dir",), str(bare)),
        (("--git-dir",), str(gitdir)),
        (("--symbolic-full-name",), "HEAD\n"),
        (("rev-parse", "HEAD"), OTHER),
    )
    result = worktree.prepare_developer(project="linux", ref="v6.9")
    assert result["synced"] is False
    assert result["reason"] == (
        f"{developer} cannot fast-forward from {OTHER[:12]} to {TARGET[:12]}"
    )
    assert result["commit"] == OTHER
    assert git.issued("merge")


def test_an_absent_worktree_is_added_attached_when_a_branch_fits(
    bare, developer, monkeypatch
):
    git = _git(
        monkeypatch,
        *_resolves("b4/series", as_tag=False),
        (("for-each-ref",), f"refs/heads/b4/series|{TARGET}|\n"),
        (("rev-parse", "HEAD"), TARGET),
    )
    result = worktree.prepare_developer(project="linux", ref="b4/series")
    assert (result["synced"], result["commit"]) == (True, TARGET)
    assert git.issued("worktree", "add") == [
        ("-C", str(bare), "worktree", "add", str(developer), "b4/series")
    ]


def test_an_absent_worktree_is_added_detached_without_one(bare, developer, monkeypatch):
    git = _git(monkeypatch, *_resolves("v6.9"), (("rev-parse", "HEAD"), TARGET))
    worktree.prepare_developer(project="linux", ref="v6.9")
    assert git.issued("worktree", "add") == [
        ("-C", str(bare), "worktree", "add", "--detach", str(developer), TARGET)
    ]


def test_a_failed_worktree_add_is_declined_rather_than_raised(build_area, monkeypatch):
    git = _git(
        monkeypatch,
        *_resolves("v6.9"),
        (("rev-parse", "HEAD"), TARGET),
        refuse=("add",),
    )
    result = worktree.prepare_developer(project="linux", ref="v6.9")
    assert result["synced"] is False
    assert result["reason"].startswith("could not add ")
    assert result["commit"] is None
    assert git.issued("worktree", "add")


def test_recreate_removes_only_a_worktree_of_this_bare(bare, developer, monkeypatch):
    _occupy(developer, bare / "worktrees/linux")
    git = _git(
        monkeypatch,
        *_resolves("v6.9"),
        (("--git-common-dir",), str(bare)),
        (("rev-parse", "HEAD"), TARGET),
    )
    worktree.prepare_developer(project="linux", ref="v6.9", recreate_worktree=True)
    assert not (developer / ".keep").exists()
    assert git.issued("worktree", "add") == [
        ("-C", str(bare), "worktree", "add", "--detach", str(developer), TARGET)
    ]


def test_recreate_leaves_a_foreign_path_alone(build_area, developer, monkeypatch):
    _occupy(developer, build_area / "elsewhere.git")
    git = _git(
        monkeypatch,
        *_resolves("v6.9"),
        (("--git-common-dir",), str(build_area / "elsewhere.git")),
        (("rev-parse", "HEAD"), OTHER),
    )
    result = worktree.prepare_developer(
        project="linux", ref="v6.9", recreate_worktree=True
    )
    assert result["synced"] is False
    assert (developer / ".keep").is_file()
    assert not git.issued("worktree", "add")


def test_an_empty_directory_counts_as_absent(developer, monkeypatch):
    developer.mkdir(parents=True)
    git = _git(monkeypatch, *_resolves("v6.9"), (("rev-parse", "HEAD"), TARGET))
    result = worktree.prepare_developer(project="linux", ref="v6.9")
    assert result["synced"] is True
    assert git.issued("worktree", "add")


def test_a_declined_sync_does_not_apply_the_b4_series(bare, developer, monkeypatch):
    gitdir = bare / "worktrees/linux"
    _occupy(developer, gitdir)
    (gitdir / "MERGE_HEAD").write_text("")
    git = _git(
        monkeypatch,
        *_resolves("v6.9"),
        (("--git-common-dir",), str(bare)),
        (("--git-dir",), str(gitdir)),
        (("rev-parse", "HEAD"), OTHER),
    )
    result = worktree.prepare_developer(
        project="linux", ref="v6.9", b4_series="20260101.1@lore"
    )
    assert result["synced"] is False
    assert result["b4_branch"] is None
    assert not git.issued("am")
    assert not git.issued("update-ref")


# --- the b4 committer identity ---------------------------------------------------


class _MboxShell:
    """Stand-in for the b4 devShell: writes the mbox `b4 am` would have downloaded."""

    def run(self, *args, cwd=None, **kwargs):
        out = Path(args[args.index("--outdir") + 1])
        (out / "0001.mbx").write_text("Subject: [PATCH v2] xarray: fix the shift\n")
        return 0


def test_b4_passes_the_committer_identity_per_invocation(tmp_path, monkeypatch):
    """`git config user.name` inside a linked worktree writes the Bare's shared
    config, which every worktree of that Bare then inherits, so the identity rides on
    the `git am` itself and no config is written anywhere."""
    monkeypatch.setattr(worktree, "DevShell", lambda *a, **k: _MboxShell())
    monkeypatch.setattr(
        worktree, "_cover_subject", lambda *a: "[PATCH v2] xarray: fix the shift"
    )
    git = _GitLog()
    wt = tmp_path / "wt"
    label = worktree._apply_b4_series(
        cast("worktree.Git", git), tmp_path / "workers", wt, "20260101.1@lore"
    )
    assert label == "xarray-fix-the-shift-v2"
    assert len(git.issued("am")) == 1
    applied = git.issued("am")[0]
    assert applied[:7] == (
        "-c",
        "user.name=kdevops",
        "-c",
        "user.email=kdevops@kdevops",
        "-C",
        str(wt),
        "am",
    )
    assert applied[7].endswith(".mbx")
    assert not git.issued("config")


# --- the developer path's command vocabulary, asserted over its own source -------


def _module_functions():
    tree = ast.parse(inspect.getsource(worktree))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _reachable(entry):
    """Every module-level function `entry` can reach, transitively by name."""
    bodies = _module_functions()
    seen, stack = set(), [entry]
    while stack:
        name = stack.pop()
        if name in seen or name not in bodies:
            continue
        seen.add(name)
        stack.extend(
            node.id for node in ast.walk(bodies[name]) if isinstance(node, ast.Name)
        )
    return seen


def _literals(names):
    bodies = _module_functions()
    return {
        node.value
        for name in names
        for node in ast.walk(bodies[name])
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_the_developer_path_reaches_the_helpers_under_test():
    reached = _reachable("prepare_developer")
    assert {"_sync", "_attachable_branch", "_apply_b4_series"} <= reached
    # The worker's aborting sanitizer is deliberately off this path: aborting a
    # developer's in-flight rebase destroys work the same way a forced reset does.
    assert "_sanitize_worktree" not in reached


@pytest.mark.parametrize(
    "verb", ["--force", "-f", "--hard", "reset", "clean", "stash", "--discard-changes"]
)
def test_the_developer_path_cannot_discard_work(verb):
    """Structural, not a runtime check: no helper the developer path can reach may
    even name a verb or flag that reverts a tracked edit, drops a staged one, or
    removes an untracked file."""
    assert verb not in _literals(_reachable("prepare_developer"))


@pytest.mark.parametrize(
    "verb", ["--abort", "-B", "--ignore-other-worktrees", "--merge", "--theirs"]
)
def test_the_developer_path_cannot_override_gits_own_guards(verb):
    assert verb not in _literals(_reachable("prepare_developer"))


def test_the_developer_paths_vocabulary_is_what_it_claims():
    """The positive half, so the scans above cannot pass by reaching nothing."""
    literals = _literals(_reachable("prepare_developer"))
    assert {"worktree", "add", "checkout", "merge", "--ff-only", "am"} <= literals


def test_the_worker_path_keeps_its_forcing_verbs():
    """The split must not have quietly disarmed the worker's sandbox reset."""
    literals = _literals(_reachable("prepare"))
    assert {"--force", "--detach", "clean", "--abort"} <= literals


# --- group validation ------------------------------------------------------------


def test_validate_group_accepts_a_plain_component():
    worktree.validate_group("vanilla")
    worktree.validate_group("b4-series.v2")


@pytest.mark.parametrize("group", ["", ".", "..", "-g", "a b", "a/b", "a\tb"])
def test_validate_group_rejects_path_and_flag_shapes(group):
    with pytest.raises(ValueError, match="invalid worktree-group"):
        worktree.validate_group(group)


@pytest.mark.parametrize("group", ["system", "workers"])
def test_validate_group_rejects_reserved_siblings(group):
    with pytest.raises(ValueError, match="reserved"):
        worktree.validate_group(group)
