# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the QEMU sanitizer selection table (`f.qemu.sanitizers`)."""

import pytest

from f.qemu import sanitizers


def test_none_adds_no_configure_flags():
    assert sanitizers.configure_args("none") == []


def test_each_selection_maps_to_its_upstream_flags():
    assert sanitizers.configure_args("ubsan") == ["--enable-ubsan"]
    assert sanitizers.configure_args("asan") == ["--enable-asan"]
    assert sanitizers.configure_args("asan+ubsan") == [
        "--enable-asan",
        "--enable-ubsan",
    ]


def test_tsan_carries_the_relaxations_upstream_uses():
    assert sanitizers.configure_args("tsan") == [
        "--enable-tsan",
        "--disable-werror",
        "--extra-cflags=-O0",
    ]


def test_no_selection_combines_tsan_with_another_sanitizer():
    """QEMU's meson errors on the combination, so it must not be nameable."""
    for name, args in sanitizers.SANITIZERS.items():
        if "--enable-tsan" in args:
            assert "--enable-asan" not in args, name
            assert "--enable-ubsan" not in args, name


def test_werror_is_relaxed_for_tsan_only():
    """Upstream disables it for the thread sanitizer alone; do not generalise."""
    relaxed = {n for n, a in sanitizers.SANITIZERS.items() if "--disable-werror" in a}
    assert relaxed == {"tsan"}


def test_empty_selection_normalizes_to_none():
    assert sanitizers.checked("") == "none"
    assert sanitizers.configure_args("") == []
    assert sanitizers.prefix_segment("") == ""


def test_unknown_selection_is_rejected():
    with pytest.raises(ValueError, match="sanitizer must be one of"):
        sanitizers.checked("msan")


def test_none_contributes_no_prefix_segment():
    assert sanitizers.prefix_segment("none") == ""


def test_a_selection_names_itself_in_the_prefix():
    assert sanitizers.prefix_segment("ubsan") == "ubsan"
    assert sanitizers.prefix_segment("asan+ubsan") == "asan+ubsan"
