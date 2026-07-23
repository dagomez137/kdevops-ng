# SPDX-License-Identifier: copyleft-next-0.3.1
"""The sanitizer selections a QEMU build offers, and what each adds to configure.

Library module imported by `f/qemu/configure` and `f/qemu/identity`, so the build
flow, the configure argv and the build identity all read one table. Not a runnable
step.

QEMU's meson refuses ThreadSanitizer alongside either other sanitizer
(`error('TSAN is not supported with other sanitizers')`), so a selection is one
entry from this table rather than three independent switches: the combination that
cannot build cannot be named. `asan+ubsan` is the pairing upstream's own
`tests/docker/test-debug` builds.

Every selection takes `--disable-werror`, and ThreadSanitizer additionally takes
`-O0`, matching `tests/docker/test-tsan`. Upstream relaxes werror for
ThreadSanitizer alone, but its CI toolchain is older than this project's: with GCC
15.2 and glibc 2.42, `-fsanitize=undefined` at `-O2` perturbs inlining enough that
the fortified `memcpy` in `block/vhdx-log.c` trips `-Werror=array-bounds` on a
false positive, and the build dies at the second object file. Werror is a
compile-time policy and the sanitizer's signal is a run-time report, so relaxing it
costs no coverage. ThreadSanitizer also
wants a glib built with `-fsanitize=thread` to avoid false positives on GMutex
(`docs/devel/testing/main.rst`), which the build devShell does not carry, so the
build flow withholds that selection while this table keeps it for a direct run of
the configure step.

The selection names the install prefix and the store key through
`prefix_segment`, so a sanitizer build never collides with a stock build of the
same ref and reads as itself in the reuse picker.
"""

from __future__ import annotations

# Selection -> the configure arguments it adds, in the order configure receives them.
SANITIZERS: dict[str, tuple[str, ...]] = {
    "none": (),
    "ubsan": ("--enable-ubsan", "--disable-werror"),
    "asan": ("--enable-asan", "--disable-werror"),
    "asan+ubsan": ("--enable-asan", "--enable-ubsan", "--disable-werror"),
    "tsan": ("--enable-tsan", "--disable-werror", "--extra-cflags=-O0"),
}


def configure_args(sanitizer: str) -> list[str]:
    """The configure arguments a selection adds; empty for `none`."""
    return list(SANITIZERS[checked(sanitizer)])


def prefix_segment(sanitizer: str) -> str:
    """The install-prefix and store-key segment naming a selection; empty for `none`."""
    name = checked(sanitizer)
    return "" if name == "none" else name


def checked(sanitizer: str) -> str:
    """Normalize an empty selection to `none` and reject an unknown one."""
    name = sanitizer or "none"
    if name not in SANITIZERS:
        raise ValueError(
            f"sanitizer must be one of {', '.join(SANITIZERS)}, got {sanitizer!r}"
        )
    return name
