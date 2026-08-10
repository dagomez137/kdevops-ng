# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the mirror/Bare config composition (`f.workbench.fetch`)."""

import os
from pathlib import Path

import pytest

from f.common import store
from f.workbench import fetch


def test_remote_url_reads_the_composed_url():
    assert fetch.remote_url({"name": "origin", "url": "https://x/y.git"}) == (
        "https://x/y.git"
    )


def test_qemu_source_options_carry_canonical_labels():
    assert fetch.qemu_source_options() == [
        {"label": "GitLab", "value": "gitlab"},
        {"label": "GitHub", "value": "github"},
    ]


def test_qemu_source_options_filter_matches_the_label_case_insensitively():
    assert fetch.qemu_source_options("hub") == [{"label": "GitHub", "value": "github"}]


def test_list_qemu_sources_is_the_dynselect_entrypoint():
    assert fetch.list_qemu_sources("lab") == [{"label": "GitLab", "value": "gitlab"}]


def test_effective_protocol_keeps_an_offered_transport():
    assert fetch._effective_protocol(fetch.LINUX_SOURCES, "kernel.org", "git") == "git"


def test_effective_protocol_degrades_to_the_preferred_transport(capsys):
    proto = fetch._effective_protocol(fetch.LINUX_SOURCES, "googlesource", "git")
    assert proto == "https"
    assert "no 'git' transport" in capsys.readouterr().out


def test_effective_protocol_rejects_an_unknown_source():
    with pytest.raises(ValueError, match="unknown source"):
        fetch._effective_protocol(fetch.LINUX_SOURCES, "sourceforge", "https")


def test_linux_mirror_defaults_to_the_curated_core(tmp_path):
    entry = fetch._linux_mirror({}, tmp_path)
    assert entry["name"] == "linux"
    assert entry["project"] == "linux"
    assert entry["mirror"] == str(tmp_path / "linux.git")
    names = [r["name"] for r in entry["remotes"]]
    assert names == list(dict.fromkeys(["torvalds", *fetch.DEFAULT_KERNEL_TREES]))
    assert entry["remotes"][0] == {
        "name": "torvalds",
        "url": "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git",
        "primary": True,
    }
    assert all(not r["primary"] for r in entry["remotes"][1:])


def test_linux_mirror_torvalds_leads_and_never_duplicates(tmp_path):
    entry = fetch._linux_mirror({"trees": ["axboe", "torvalds", "xfs"]}, tmp_path)
    assert [r["name"] for r in entry["remotes"]] == ["torvalds", "axboe", "xfs"]


def test_linux_mirror_git_transport_composes_git_urls(tmp_path):
    entry = fetch._linux_mirror({"trees": ["xfs"], "protocol": "git"}, tmp_path)
    urls = {r["name"]: r["url"] for r in entry["remotes"]}
    assert urls["xfs"] == "git://git.kernel.org/pub/scm/fs/xfs/xfs-linux.git"


def test_linux_mirror_composes_the_maintainer_tree_urls(tmp_path):
    entry = fetch._linux_mirror({"trees": ["da.gomez", "djwong"]}, tmp_path)
    urls = {r["name"]: r["url"] for r in entry["remotes"]}
    assert urls["da.gomez"] == (
        "https://git.kernel.org/pub/scm/linux/kernel/git/da.gomez/linux.git"
    )
    assert urls["djwong"] == (
        "https://git.kernel.org/pub/scm/linux/kernel/git/djwong/xfs-linux.git"
    )


def test_linux_mirror_rejects_an_uncurated_tree(tmp_path):
    with pytest.raises(ValueError, match="unknown kernel tree"):
        fetch._linux_mirror({"trees": ["evil"]}, tmp_path)


def test_qemu_mirror_defaults_to_gitlab_https(tmp_path):
    entry = fetch._qemu_mirror({}, tmp_path)
    assert entry["mirror"] == str(tmp_path / "qemu.git")
    assert entry["remotes"] == [
        {
            "name": "origin",
            "url": "https://gitlab.com/qemu-project/qemu.git",
            "primary": True,
        }
    ]


def test_qemu_mirror_github_only_offers_https(tmp_path):
    entry = fetch._qemu_mirror({"source": "github", "protocol": "git"}, tmp_path)
    assert entry["remotes"][0]["url"] == "https://github.com/qemu/qemu.git"


def test_build_mirrors_selects_only_the_requested_projects(tmp_path):
    entries = fetch.build_mirrors(["qemu"], None, None, tmp_path)
    assert [e["name"] for e in entries] == ["qemu"]


def test_build_mirrors_rejects_an_uncurated_project(tmp_path):
    with pytest.raises(ValueError, match="unknown mirror project"):
        fetch.build_mirrors(["linux", "xfsprogs"], None, None, tmp_path)


def test_normalize_peers_accepts_strings_and_objects():
    peers = fetch._normalize_peers(
        [
            " hostb ",
            {"host": "hostc", "store_index": " /custom/index "},
            {"host": "hostd", "store_index": ""},
            {"host": "  "},
            "",
            42,
        ]
    )
    assert peers == [
        {"host": "hostb", "store_index": store.DEFAULT_PEER_INDEX},
        {"host": "hostc", "store_index": "/custom/index"},
        {"host": "hostd", "store_index": store.DEFAULT_PEER_INDEX},
    ]


def test_normalize_peers_none_is_empty():
    assert fetch._normalize_peers(None) == []


def test_require_str_returns_the_value():
    assert fetch._require_str({"name": "linux"}, "name") == "linux"


@pytest.mark.parametrize("value", [None, "", 7])
def test_require_str_rejects_missing_or_non_string(value):
    with pytest.raises(ValueError, match="non-empty string"):
        fetch._require_str({"name": value}, "name")


def _entry(**over):
    entry = {
        "name": "linux",
        "mirror": "/m/linux.git",
        "project": "linux",
        "remotes": [{"name": "torvalds", "url": "https://x", "primary": True}],
    }
    entry.update(over)
    return entry


def test_validate_returns_the_identity_triple():
    assert fetch._validate(_entry()) == ("linux", "/m/linux.git", "linux")


def test_validate_needs_at_least_one_remote():
    with pytest.raises(ValueError, match="at least one remote"):
        fetch._validate(_entry(remotes=[]))


def test_validate_rejects_a_flag_like_mirror():
    with pytest.raises(ValueError, match="invalid mirror"):
        fetch._validate(_entry(mirror="--upload-pack=evil"))


@pytest.mark.parametrize("project", ["../escape", "-flag", "/abs"])
def test_validate_rejects_a_traversing_project(project):
    with pytest.raises(ValueError, match="invalid project"):
        fetch._validate(_entry(project=project))


def test_primary_picks_the_flagged_remote():
    remotes = [
        {"name": "a", "url": "u1"},
        {"name": "b", "url": "u2", "primary": True},
    ]
    assert fetch._primary({"remotes": remotes})["name"] == "b"


def test_primary_falls_back_to_the_first_remote():
    remotes = [{"name": "a", "url": "u1"}, {"name": "b", "url": "u2"}]
    assert fetch._primary({"remotes": remotes})["name"] == "a"


def test_validate_peer_accepts_a_plain_alias():
    fetch._validate_peer("hetzie")


@pytest.mark.parametrize("peer", ["", None, "a/b", "..", "-oProxyCommand=x"])
def test_validate_peer_rejects_path_and_flag_shapes(peer):
    with pytest.raises(ValueError):
        fetch._validate_peer(peer)


def test_reconcile_alternates_writes_the_objects_lines(tmp_path):
    bare = tmp_path / "bare/linux.git"
    fetch._reconcile_alternates(bare, ["/m/linux.git"])
    alternates = bare / "objects/info/alternates"
    assert alternates.read_text() == "/m/linux.git/objects\n"


def test_reconcile_alternates_deduplicates(tmp_path):
    bare = tmp_path / "bare"
    fetch._reconcile_alternates(bare, ["/m/a.git", "/m/a.git", "/m/b.git"])
    assert (bare / "objects/info/alternates").read_text() == (
        "/m/a.git/objects\n/m/b.git/objects\n"
    )


def test_reconcile_alternates_is_idempotent(tmp_path):
    bare = tmp_path / "bare"
    fetch._reconcile_alternates(bare, ["/m/linux.git"])
    alternates = bare / "objects/info/alternates"
    os.utime(alternates, (1, 1))
    fetch._reconcile_alternates(bare, ["/m/linux.git"])
    assert alternates.stat().st_mtime == 1


def test_reconcile_alternates_drops_stale_entries(tmp_path):
    bare = tmp_path / "bare"
    info = bare / "objects/info"
    info.mkdir(parents=True)
    (info / "alternates").write_text("/old/mirror.git/objects\n")
    fetch._reconcile_alternates(bare, ["/m/linux.git"])
    assert (info / "alternates").read_text() == "/m/linux.git/objects\n"


def test_progress_phrases():
    assert fetch._progress("present", False) == "present (skip refresh)"
    assert fetch._progress("created", True) == "created"
    assert fetch._progress("refreshed", True) == "refreshed"


def test_kernel_trees_paths_live_under_kernel_org_scm():
    assert all(path.startswith("pub/scm/") for path in fetch.KERNEL_TREES.values())
    assert set(fetch.DEFAULT_KERNEL_TREES) <= set(fetch.KERNEL_TREES)
    assert Path(fetch.KERNEL_TREES["torvalds"]).name == "linux"
