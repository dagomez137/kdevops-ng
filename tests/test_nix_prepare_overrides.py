# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the override resolver (`f.nix.prepare_overrides`)."""

from pathlib import Path

import pytest

from f.nix import prepare_overrides

ENV = ("SYSTEM_DIR", "MIRRORS_DIR")


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path / "system"))
    return tmp_path


@pytest.fixture
def prepared(monkeypatch):
    """Replace `prepare` with a recorder publishing a b4 branch."""
    calls = []

    def fake(*, project, ref, b4_series):
        calls.append({"project": project, "ref": ref, "b4_series": b4_series})
        return {"b4_branch": "b4/fix-foo", "label": "fix-foo-v2"}

    monkeypatch.setattr(prepare_overrides, "prepare", fake)
    return calls


def _seed_bare(system: Path, project: str, *refnames: str) -> Path:
    """A minimal Bare under `$SYSTEM_DIR/bare` packing full `refnames`."""
    bare = system / "bare" / f"{project}.git"
    (bare / "objects").mkdir(parents=True)
    (bare / "refs").mkdir()
    lines = [f"{'b' * 40} {name}" for name in refnames]
    (bare / "packed-refs").write_text("\n".join(lines) + "\n")
    return bare


def test_no_overrides_resolve_to_nothing(env):
    out = prepare_overrides.main()
    assert out == {"source_overrides": {}, "overrides": [], "worktree_group": ""}


def test_rows_come_in_curated_order_and_derive_one_group(env, capsys):
    # The deploy group is the first override's auto-derived name (a tag
    # derives vanilla, a branch its own name, a commit id its short sha);
    # differing derivations warn and keep the first.
    _seed_bare(env / "system", "xfsprogs-dev", "refs/remotes/mirror/for-next")
    _seed_bare(env / "system", "blktests", "refs/tags/v1.0")
    _seed_bare(env / "system", "fio", "refs/remotes/mirror/master")
    sha = "c" * 40
    out = prepare_overrides.main(
        source_overrides={
            "xfsprogs": {"ref": "mirror/for-next"},
            "blktests": {"ref": "v1.0"},
            "fio": {"ref": sha},
        },
    )
    assert out["overrides"] == [
        {"pkg": "fio", "project": "fio", "ref": sha},
        {"pkg": "xfsprogs", "project": "xfsprogs-dev", "ref": "mirror/for-next"},
        {"pkg": "blktests", "project": "blktests", "ref": "v1.0"},
    ]
    assert out["source_overrides"] == {
        "fio": {"ref": sha},
        "xfsprogs": {"ref": "mirror/for-next"},
        "blktests": {"ref": "v1.0"},
    }
    assert out["worktree_group"] == "c" * 12
    warn = capsys.readouterr().out
    assert "override worktree groups differ" in warn
    assert "mirror-for-next" in warn and "vanilla" in warn


def test_agreeing_groups_warn_nothing(env, capsys):
    _seed_bare(env / "system", "fio", "refs/remotes/mirror/master")
    _seed_bare(env / "system", "blktests", "refs/remotes/mirror/master")
    out = prepare_overrides.main(
        source_overrides={
            "fio": {"ref": "mirror/master"},
            "blktests": {"ref": "mirror/master"},
        },
    )
    assert out["worktree_group"] == "mirror-master"
    assert "differ" not in capsys.readouterr().out


def test_a_series_builds_from_the_published_b4_branch(env, prepared):
    _seed_bare(env / "system", "xfstests-dev", "refs/remotes/mirror/for-next")
    out = prepare_overrides.main(
        source_overrides={
            "xfstests": {"ref": "mirror/for-next", "b4_series": "<msgid@lore>"},
        },
    )
    assert prepared == [
        {
            "project": "xfstests-dev",
            "ref": "mirror/for-next",
            "b4_series": "<msgid@lore>",
        }
    ]
    assert out["source_overrides"] == {"xfstests": {"ref": "b4/fix-foo"}}
    assert out["overrides"][0]["ref"] == "b4/fix-foo"
    # The group takes the series label, the kernel-build naming rule.
    assert out["worktree_group"] == "fix-foo-v2"


def test_a_series_needs_a_ref(env, prepared):
    with pytest.raises(ValueError, match="needs a ref"):
        prepare_overrides.main(
            source_overrides={"xfstests": {"ref": "", "b4_series": "<msgid>"}},
        )
    assert prepared == []


def test_an_unpublished_b4_branch_fails_the_step(env, monkeypatch):
    _seed_bare(env / "system", "xfstests-dev", "refs/remotes/mirror/for-next")
    monkeypatch.setattr(
        prepare_overrides,
        "prepare",
        lambda **_: {"b4_branch": None, "label": "x"},
    )
    with pytest.raises(RuntimeError, match="not published"):
        prepare_overrides.main(
            source_overrides={
                "xfstests": {"ref": "mirror/for-next", "b4_series": "<msgid>"},
            },
        )


def test_an_unknown_ref_is_rejected(env):
    _seed_bare(env / "system", "fio", "refs/remotes/mirror/master")
    with pytest.raises(ValueError, match="not found in the fio Bare"):
        prepare_overrides.main(source_overrides={"fio": {"ref": "nope"}})


def test_unknown_keys_and_form_knobs_are_ignored(env):
    # The deploy toggles live beside the packages in the form object; they
    # and any unknown package key must never resolve as overrides.
    out = prepare_overrides.main(
        source_overrides={
            "deploy_developer_worktree": True,
            "custom_group": False,
            "worktree_group": "vanilla",
            "spdk": {"ref": "main"},
        },
    )
    assert out["overrides"] == []
