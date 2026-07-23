# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the QEMU build-identity prefix helpers (`f.qemu.identity`)."""

from pathlib import Path

from f.qemu import identity as identity_step
from f.qemu.identity import _prefix_basename, _read_version
from f.qemu.sanitizers import SANITIZERS

IDENTITY = "abc123def456"
TREE = "2222222222222222222222222222222222222222"
DRV = "/nix/store/bbbb-build-qemu.drv"


class _TreeGit:
    def capture(self, *args, **kwargs):
        return TREE + "\n"


def _pin(monkeypatch):
    monkeypatch.setattr(identity_step, "Git", lambda: _TreeGit())
    monkeypatch.setattr(identity_step, "_toolchain", lambda: DRV)


def _identity(tmp_path, **kwargs):
    return identity_step.main(worktree=str(tmp_path), destdir=str(tmp_path), **kwargs)


def test_read_version_strips_the_version_file(tmp_path):
    (tmp_path / "VERSION").write_text("11.0.0\n")
    assert _read_version(str(tmp_path)) == "11.0.0"


def test_read_version_is_empty_without_the_file(tmp_path):
    assert _read_version(str(tmp_path)) == ""


def test_prefix_leads_with_the_version_and_carries_the_label():
    assert (
        _prefix_basename("11.0.0", "vanilla", IDENTITY) == f"11.0.0-vanilla-{IDENTITY}"
    )


def test_prefix_without_a_label_drops_the_slug():
    assert _prefix_basename("11.0.0", "", IDENTITY) == f"11.0.0-{IDENTITY}"


def test_prefix_without_a_version_falls_back_to_the_label():
    assert _prefix_basename("", "My Series", IDENTITY) == f"my-series-{IDENTITY}"


def test_prefix_with_neither_is_the_bare_identity():
    assert _prefix_basename("", "", IDENTITY) == IDENTITY


def test_label_slug_is_lowercased_and_collapsed():
    assert (
        _prefix_basename("11.0.0", "Fix NVMe (RFC)", IDENTITY)
        == f"11.0.0-fix-nvme-rfc-{IDENTITY}"
    )


def test_long_label_caps_at_64_and_keeps_the_revision_suffix():
    label = "x" * 80 + " v12"
    out = _prefix_basename("", label, IDENTITY)
    slug = out[: -len(IDENTITY) - 1]
    assert len(slug) == 64
    assert slug == "x" * 60 + "-v12"


def test_cap_cut_at_a_dash_is_rstripped_before_the_suffix():
    label = "a" * 60 + "-bcd-v2"
    assert _prefix_basename("", label, IDENTITY) == "a" * 60 + f"-v2-{IDENTITY}"


def test_sanitizer_segment_follows_the_label():
    assert (
        _prefix_basename("11.0.2", "vanilla", IDENTITY, "ubsan")
        == f"11.0.2-vanilla-ubsan-{IDENTITY}"
    )


def test_sanitizer_segment_stands_alone_without_a_label():
    assert _prefix_basename("11.0.2", "", IDENTITY, "asan") == f"11.0.2-asan-{IDENTITY}"


def test_no_sanitizer_segment_leaves_the_prefix_unchanged():
    assert _prefix_basename("11.0.2", "vanilla", IDENTITY, "") == _prefix_basename(
        "11.0.2", "vanilla", IDENTITY
    )


def test_a_sanitizer_build_cannot_share_an_identity_with_a_stock_one(
    tmp_path, monkeypatch
):
    """The whole point of hashing it: reuse_check must not confuse the two."""
    _pin(monkeypatch)
    (tmp_path / "VERSION").write_text("11.0.2\n")
    stock = _identity(tmp_path)
    ubsan = _identity(tmp_path, sanitizer="ubsan")
    assert stock["identity"] != ubsan["identity"]
    assert stock["prefix"] != ubsan["prefix"]


def test_every_selection_gets_its_own_identity(tmp_path, monkeypatch):
    _pin(monkeypatch)
    (tmp_path / "VERSION").write_text("11.0.2\n")
    digests = {
        name: _identity(tmp_path, sanitizer=name)["identity"] for name in SANITIZERS
    }
    assert len(set(digests.values())) == len(SANITIZERS)


def test_the_selection_names_the_prefix_and_stock_stays_bare(tmp_path, monkeypatch):
    _pin(monkeypatch)
    (tmp_path / "VERSION").write_text("11.0.2\n")
    ubsan = _identity(tmp_path, label="vanilla", sanitizer="ubsan")
    stock = _identity(tmp_path, label="vanilla")
    assert Path(ubsan["prefix"]).name == f"11.0.2-vanilla-ubsan-{ubsan['identity']}"
    assert Path(stock["prefix"]).name == f"11.0.2-vanilla-{stock['identity']}"


def test_an_empty_selection_is_the_same_build_as_none(tmp_path, monkeypatch):
    _pin(monkeypatch)
    (tmp_path / "VERSION").write_text("11.0.2\n")
    assert (
        _identity(tmp_path, sanitizer="")["identity"] == _identity(tmp_path)["identity"]
    )
