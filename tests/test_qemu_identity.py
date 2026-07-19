# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the QEMU build-identity prefix helpers (`f.qemu.identity`)."""

from f.qemu.identity import _prefix_basename, _read_version

IDENTITY = "abc123def456"


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
