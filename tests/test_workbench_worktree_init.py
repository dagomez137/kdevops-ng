# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the worktree-group init step (`f.workbench.worktree.init`)."""

import pytest

from f.workbench.worktree import init


@pytest.fixture
def prepared(monkeypatch):
    """Replace `prepare_developer` with a recorder returning a synced row."""
    calls = []

    def fake(*, project, ref, worktree_group, b4_series, recreate_worktree):
        calls.append(
            {
                "project": project,
                "ref": ref,
                "worktree_group": worktree_group,
                "b4_series": b4_series,
                "recreate_worktree": recreate_worktree,
            }
        )
        return {
            "project": project,
            "ref": ref,
            "commit": "c" * 40,
            "worktree": f"/wt/{worktree_group}/{project}",
            "b4_branch": None,
            "synced": True,
            "reason": None,
        }

    monkeypatch.setattr(init, "prepare_developer", fake)
    return calls


def test_a_shared_group_lays_every_project(prepared):
    out = init.main(
        worktree_group="largeio",
        projects=[
            {"project": "linux", "git_ref": "v6.12"},
            {"project": "fio", "git_ref": "mirror/master"},
        ],
    )
    assert [c["worktree_group"] for c in prepared] == ["largeio", "largeio"]
    assert [w["worktree_group"] for w in out["worktrees"]] == ["largeio", "largeio"]
    assert out["worktree_group"] == "largeio"


def test_an_entry_group_overrides_the_shared_one(prepared):
    out = init.main(
        worktree_group="largeio",
        projects=[
            {"project": "linux", "git_ref": "v6.12"},
            {"project": "fio", "git_ref": "mirror/master", "worktree_group": "perf"},
        ],
    )
    assert [c["worktree_group"] for c in prepared] == ["largeio", "perf"]
    assert [w["worktree_group"] for w in out["worktrees"]] == ["largeio", "perf"]


def test_per_entry_groups_need_no_shared_group(prepared):
    init.main(
        projects=[
            {"project": "fio", "git_ref": "mirror/master", "worktree_group": "perf"},
        ],
    )
    assert prepared[0]["worktree_group"] == "perf"


def test_an_entry_without_any_group_is_rejected(prepared):
    with pytest.raises(ValueError, match="no worktree_group"):
        init.main(projects=[{"project": "fio", "git_ref": "mirror/master"}])
    assert prepared == []


def test_a_reserved_entry_group_fails_before_any_worktree(prepared):
    with pytest.raises(ValueError):
        init.main(
            worktree_group="largeio",
            projects=[
                {"project": "linux", "git_ref": "v6.12"},
                {"project": "fio", "git_ref": "master", "worktree_group": "system"},
            ],
        )
    assert prepared == []


def test_a_missing_ref_is_rejected(prepared):
    with pytest.raises(ValueError, match="git_ref is required"):
        init.main(worktree_group="largeio", projects=[{"project": "fio"}])


def test_no_projects_is_rejected(prepared):
    with pytest.raises(ValueError, match="at least one"):
        init.main(worktree_group="largeio", projects=[])
