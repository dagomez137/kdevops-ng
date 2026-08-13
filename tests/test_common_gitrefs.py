# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the ref picker's pure reads (`f.common.gitrefs`).

No test touches the network: an autouse fixture replaces the module's fetch
seam with one that refuses, and the korg tests swap in their own fakes.
"""

import json
import os
import time
from pathlib import Path

import pytest

from f.common import gitrefs

ENV = ("SYSTEM_DIR", "MIRRORS_DIR")

SHA = "a" * 40


def _clear_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse():
        raise RuntimeError("network fetch attempted in tests")

    monkeypatch.setattr(gitrefs, "_fetch_korg", refuse)


def _make_bare(root: Path, repo: str) -> Path:
    bare = root / f"{repo}.git"
    (bare / "refs").mkdir(parents=True)
    return bare


def _loose(bare: Path, name: str) -> None:
    """Create a loose ref; `name` is relative to `refs/`, like `heads/master`."""
    path = bare / "refs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SHA + "\n")


def _packed(bare: Path, *names: str) -> None:
    """Write packed-refs with full ref `names`, comment and peel lines included."""
    lines = ["# pack-refs with: peeled fully-peeled sorted"]
    for name in names:
        lines.append(f"{SHA} {name}")
        if name.startswith("refs/tags/"):
            lines.append(f"^{SHA}")
    (bare / "packed-refs").write_text("\n".join(lines) + "\n")


def _korg_payload() -> str:
    return json.dumps(
        {
            "releases": [
                {"version": "7.2-rc5", "moniker": "mainline", "iseol": False},
                {"version": "7.1", "moniker": "stable", "iseol": False},
                {"version": "6.1.100", "moniker": "longterm", "iseol": True},
                {"version": "next-20260801", "moniker": "linux-next", "iseol": False},
            ]
        }
    )


def _seed_stale_cache(system: Path, payload: str) -> Path:
    cache = system / "cache" / "korg-releases.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(payload)
    old = time.time() - 2 * gitrefs._KORG_CACHE_TTL
    os.utime(cache, (old, old))
    return cache


def test_no_repo_anywhere_returns_empty(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    assert gitrefs.list_refs("demo") == []
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    assert gitrefs.list_refs("demo") == []


def test_bare_is_preferred_over_the_mirror(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    monkeypatch.setenv("MIRRORS_DIR", str(tmp_path / "mirrors"))
    _loose(_make_bare(tmp_path / "bare", "demo"), "heads/from-bare")
    _loose(_make_bare(tmp_path / "mirrors", "demo"), "heads/from-mirror")
    assert [o["value"] for o in gitrefs.list_refs("demo")] == ["from-bare"]


def test_mirror_fallback_and_default_mirrors_dir(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    # MIRRORS_DIR unset: the default is $SYSTEM_DIR/mirror.
    _loose(_make_bare(tmp_path / "mirror", "demo"), "heads/from-mirror")
    assert [o["value"] for o in gitrefs.list_refs("demo")] == ["from-mirror"]


def test_packed_and_loose_merge_with_loose_winning(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    bare = _make_bare(tmp_path / "bare", "demo")
    _packed(bare, "refs/heads/main", "refs/tags/v1.0", "refs/tags/promoted")
    # The loose entry re-declares `promoted` as a branch; loose must win.
    _loose(bare, "heads/promoted")
    _loose(bare, "heads/feature/x")
    options = gitrefs.list_refs("demo")
    assert [o["value"] for o in options] == [
        "feature/x",
        "main",
        "promoted",
        "v1.0",
    ]
    assert all(o["label"] == o["value"] for o in options)


def test_tag_ordering_newest_release_first_rc_after_release(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    bare = _make_bare(tmp_path / "bare", "demo")
    _loose(bare, "heads/master")
    for tag in ("v6.1", "v6.2-rc1", "v6.2", "v6.10", "exp-tag", "v6.2-rc2", "v6.2.1"):
        _loose(bare, f"tags/{tag}")
    assert [o["value"] for o in gitrefs.list_refs("demo")] == [
        "master",
        "v6.10",
        "v6.2.1",
        "v6.2",
        "v6.2-rc2",
        "v6.2-rc1",
        "v6.1",
        "exp-tag",
    ]


def test_mirror_branches_list_between_heads_and_tags(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    bare = _make_bare(tmp_path / "bare", "demo")
    _packed(bare, "refs/remotes/mirror/master", "refs/tags/v1.0")
    _loose(bare, "remotes/mirror/for-next")
    _loose(bare, "heads/b4/series")
    # Other remote namespaces (peers) stay out of the picker.
    _loose(bare, "remotes/peer/dev")
    assert [o["value"] for o in gitrefs.list_refs("demo")] == [
        "b4/series",
        "mirror/for-next",
        "mirror/master",
        "v1.0",
    ]
    assert [o["value"] for o in gitrefs.list_refs("demo", "for-nex")] == [
        "mirror/for-next"
    ]


def test_qualify_ref_resolves_in_the_worktree_order(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    bare = _make_bare(tmp_path / "bare", "demo")
    _packed(bare, "refs/tags/v1.0", "refs/remotes/mirror/master")
    _loose(bare, "heads/b4/series")
    _loose(bare, "remotes/peer/dev")
    # A name in several namespaces resolves tag, then mirror, then head.
    _loose(bare, "tags/shared")
    _loose(bare, "remotes/mirror/shared")
    _loose(bare, "heads/shared")
    assert gitrefs.qualify_ref("demo", "v1.0") == "refs/tags/v1.0"
    assert gitrefs.qualify_ref("demo", "master") == "refs/remotes/mirror/master"
    assert gitrefs.qualify_ref("demo", "b4/series") == "refs/heads/b4/series"
    assert gitrefs.qualify_ref("demo", "mirror/master") == "refs/remotes/mirror/master"
    assert gitrefs.qualify_ref("demo", "peer/dev") == "refs/remotes/peer/dev"
    assert gitrefs.qualify_ref("demo", "shared") == "refs/tags/shared"
    # An already-qualified value passes through only when it exists.
    assert gitrefs.qualify_ref("demo", "refs/heads/b4/series") == "refs/heads/b4/series"
    assert gitrefs.qualify_ref("demo", "refs/heads/nope") is None
    assert gitrefs.qualify_ref("demo", "nope") is None


def test_qualify_ref_without_a_repo_returns_none(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    assert gitrefs.qualify_ref("demo", "master") is None


def test_filter_text_is_a_case_insensitive_substring(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    bare = _make_bare(tmp_path / "bare", "demo")
    _loose(bare, "heads/master")
    _loose(bare, "tags/v6.2-rc1")
    _loose(bare, "tags/v6.2")
    assert [o["value"] for o in gitrefs.list_refs("demo", "MAST")] == ["master"]
    assert [o["value"] for o in gitrefs.list_refs("demo", "rc")] == ["v6.2-rc1"]
    assert gitrefs.list_refs("demo", "nope") == []


def test_options_are_capped(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    bare = _make_bare(tmp_path / "bare", "demo")
    for n in range(gitrefs._MAX_OPTIONS + 50):
        _loose(bare, f"heads/b{n:03d}")
    assert len(gitrefs.list_refs("demo")) == gitrefs._MAX_OPTIONS


def test_non_linux_repo_never_consults_korg(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    _loose(_make_bare(tmp_path / "bare", "demo"), "heads/master")
    calls = []
    monkeypatch.setattr(gitrefs, "_fetch_korg_bounded", lambda: calls.append(1))
    gitrefs.list_refs("demo")
    assert calls == []


def test_korg_section_labels_dedup_and_ordering(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    bare = _make_bare(tmp_path / "bare", "linux")
    _loose(bare, "heads/master")
    _loose(bare, "tags/v7.1")
    _loose(bare, "tags/v7.0")
    monkeypatch.setattr(gitrefs, "_fetch_korg", _korg_payload)
    assert gitrefs.list_refs("linux") == [
        {"value": "v7.2-rc5", "label": "v7.2-rc5 (mainline, not mirrored)"},
        {"value": "v7.1", "label": "v7.1 (stable)"},
        {"value": "v6.1.100", "label": "v6.1.100 (longterm, EOL, not mirrored)"},
        {"value": "next-20260801", "label": "next-20260801 (linux-next, not mirrored)"},
        {"value": "master", "label": "master"},
        {"value": "v7.0", "label": "v7.0"},
    ]
    # The filter matches the label, and a matched korg tag stays deduplicated.
    assert gitrefs.list_refs("linux", "v7.1") == [
        {"value": "v7.1", "label": "v7.1 (stable)"}
    ]
    assert gitrefs.list_refs("linux", "mainline") == [
        {"value": "v7.2-rc5", "label": "v7.2-rc5 (mainline, not mirrored)"}
    ]


def test_fetch_failure_serves_stale_cache_and_throttles_retries(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    _loose(_make_bare(tmp_path / "bare", "linux"), "heads/master")
    cache = _seed_stale_cache(tmp_path, _korg_payload())
    marker = cache.with_name(cache.name + ".attempt")
    attempts = []

    def failing_fetch():
        attempts.append(1)
        raise OSError("resolver down")

    monkeypatch.setattr(gitrefs, "_fetch_korg", failing_fetch)

    first = gitrefs.list_refs("linux")
    assert first[0]["value"] == "v7.2-rc5"  # stale cache still served
    assert len(attempts) == 1
    assert marker.is_file()

    # Within the retry window the next keystroke pays no new network attempt.
    assert gitrefs.list_refs("linux") == first
    assert len(attempts) == 1

    # An aged marker allows a fresh attempt.
    old = time.time() - 2 * gitrefs._KORG_RETRY_WINDOW
    os.utime(marker, (old, old))
    assert gitrefs.list_refs("linux") == first
    assert len(attempts) == 2


def test_fetch_deadline_bounds_a_hung_resolver(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    _loose(_make_bare(tmp_path / "bare", "linux"), "heads/master")
    cache = _seed_stale_cache(tmp_path, _korg_payload())
    monkeypatch.setattr(gitrefs, "_KORG_FETCH_DEADLINE", 0.05)

    def hung_fetch():
        time.sleep(0.5)
        return _korg_payload()

    monkeypatch.setattr(gitrefs, "_fetch_korg", hung_fetch)
    start = time.monotonic()
    options = gitrefs.list_refs("linux")
    assert time.monotonic() - start < 0.4
    assert options[0]["value"] == "v7.2-rc5"  # stale cache, not the hung fetch
    assert cache.with_name(cache.name + ".attempt").is_file()


def test_successful_fetch_publishes_the_cache_atomically(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    _loose(_make_bare(tmp_path / "bare", "linux"), "heads/master")
    monkeypatch.setattr(gitrefs, "_fetch_korg", _korg_payload)
    replaced = []
    real_replace = os.replace

    def spy(src, dst, *args, **kwargs):
        replaced.append((str(src), str(dst)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(gitrefs.os, "replace", spy)
    gitrefs.list_refs("linux")

    cache = tmp_path / "cache" / "korg-releases.json"
    assert cache.read_text() == _korg_payload()
    ((src, dst),) = replaced
    assert dst == str(cache) and src != str(cache)
    assert Path(src).parent == cache.parent
    # No temp litter and no failure marker after a clean publish.
    assert [p.name for p in cache.parent.iterdir()] == [cache.name]


def test_failed_publish_never_tears_the_visible_cache(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    _loose(_make_bare(tmp_path / "bare", "linux"), "heads/master")
    old_payload = json.dumps(
        {"releases": [{"version": "7.0", "moniker": "stable", "iseol": False}]}
    )
    cache = _seed_stale_cache(tmp_path, old_payload)
    monkeypatch.setattr(gitrefs, "_fetch_korg", _korg_payload)

    def broken_replace(src, dst, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(gitrefs.os, "replace", broken_replace)
    options = gitrefs.list_refs("linux")
    # The fresh fetch is served from memory even though the publish failed.
    assert options[0]["value"] == "v7.2-rc5"
    # The visible cache is the old file, byte for byte: never a torn write.
    assert cache.read_text() == old_payload
    assert [p.name for p in cache.parent.iterdir()] == [cache.name]


def test_unparseable_fetch_counts_as_failure_and_keeps_the_cache(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    _loose(_make_bare(tmp_path / "bare", "linux"), "heads/master")
    cache = _seed_stale_cache(tmp_path, _korg_payload())
    monkeypatch.setattr(gitrefs, "_fetch_korg", lambda: "<html>rate limited</html>")
    options = gitrefs.list_refs("linux")
    assert options[0]["value"] == "v7.2-rc5"  # stale cache, not the junk payload
    assert cache.read_text() == _korg_payload()
    assert cache.with_name(cache.name + ".attempt").is_file()


def test_refs_read_races_degrade_to_partial_data(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    bare = _make_bare(tmp_path / "bare", "demo")
    _packed(bare, "refs/heads/from-packed")
    _loose(bare, "heads/kept")
    _loose(bare, "heads/dropped")

    real_read_text = Path.read_text

    def racy_read_text(self, *args, **kwargs):
        if self.name == "packed-refs":
            raise OSError("repacked underneath us")
        return real_read_text(self, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(gitrefs.Path, "read_text", racy_read_text)
        # packed-refs is skipped; the loose refs still answer.
        assert [o["value"] for o in gitrefs.list_refs("demo")] == ["dropped", "kept"]

    def racy_rglob(self, pattern):
        def walk():
            if self.name == "heads":
                yield bare / "refs/heads/kept"
            raise OSError("pruned mid-walk")

        return walk()

    with monkeypatch.context() as patched:
        patched.setattr(gitrefs.Path, "rglob", racy_rglob)
        # The walk dies mid-iteration; packed refs plus the yielded entry survive.
        assert [o["value"] for o in gitrefs.list_refs("demo")] == [
            "from-packed",
            "kept",
        ]


def test_main_is_a_library_stub():
    assert "gitrefs" in gitrefs.main()
