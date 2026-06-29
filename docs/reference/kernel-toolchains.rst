.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

=================
Kernel toolchains
=================

Building the kernel needs a toolchain that satisfies the kernel's own
requirements, and on Nix that takes more care than passing a single flag. Two
toolchains have non-obvious wiring: `Clang/LLVM`_, where the nixpkgs cc-wrapper
fights the kernel's include handling, and `Rust`_, where the supported version
window moves with the kernel version. This page records the durable recipe for
each. The Clang/LLVM recipe is implemented in the ``build-kernel`` devShell of
``vendor/nixos-flake`` and consumed by ``f/kernel/build_flags.py``; the Rust
requirements drive both that devShell and any per-kernel toolchain selection.

Clang and LLVM
==============

The kernel build selects an LLVM toolchain with ``make LLVM=1``, which puts
``clang`` and the ``llvm-*`` binutils on the command line. With the nixpkgs
clang toolchain, ``LLVM=1`` on its own does not build the kernel: the wrapper
that nixpkgs places around ``clang`` injects flags the kernel rejects, and the
unwrapped compiler underneath it loses the include paths the kernel needs. The
recipe below sets each tool explicitly so both target objects and host tools
build cleanly.

Why ``LLVM=1`` alone is not enough
----------------------------------

The nixpkgs cc-wrapper exists so that a bare ``clang`` redirects to Nix's own
libc headers. It does this by adding ``-nostdlibinc`` to every invocation. The
kernel, however, compiles with ``-nostdinc`` and
``-Werror=unused-command-line-argument``, so the wrapper's ``-nostdlibinc`` is
flagged as unused and becomes a hard error on the very first object
(``scripts/mod/empty.o``). This hits both the target compiler (``CC``) and the
host compiler (``HOSTCC``)::

   clang: error: argument unused during compilation: '-nostdlibinc'
          [-Werror,-Wunused-command-line-argument]

Switching to the unwrapped ``clang`` (``llvmPackages.clang-unwrapped``) removes
the injected ``-nostdlibinc``, so target objects compile, but it trades one
failure for two more:

- Host tools built with the unwrapped compiler link against the wrong libc and
  fail. For example ``tools/objtool`` links ``libelf`` and the link aborts with
  ``undefined reference to '__isoc23_strtol@GLIBC_2.38'``, because the unwrapped
  ``clang`` does not wire in Nix's glibc.
- The kernel's ``-nostdinc`` strips the unwrapped compiler's own resource
  directory, so the target build can no longer find ``stdarg.h``, ``stddef.h``,
  and the other builtin headers, which the wrapper would normally re-add.

Demoting the warning (for example with
``KCFLAGS=-Wno-unused-command-line-argument``) is whack-a-mole: silencing
``-nostdlibinc`` only surfaces the next unused argument
(``-Wa,--compress-debug-sections``), and never reaches a clean build. The fix is
to set each tool to the right variant rather than to suppress diagnostics.

The proven recipe
-----------------

This mirrors what the nixpkgs kernel build itself does in
``pkgs/os-specific/linux/kernel/common-flags.nix``, which notably does not use
``LLVM=1`` and instead sets each tool by hand:

- ``CC`` is the unwrapped ``clang``. Dropping the wrapper drops the
  ``-nostdlibinc`` the kernel rejects.
- ``HOSTCC`` and ``HOSTCXX`` are the wrapped ``clang`` (the ``LLVM=1`` default),
  so host tools still link against Nix's glibc.
- ``CFLAGS_KERNEL`` and ``CFLAGS_MODULE`` each carry
  ``-I$("$CC" -print-resource-dir)/include`` to restore the builtin headers that
  ``-nostdinc`` strips. On the unwrapped ``clang``, ``-print-resource-dir``
  already returns the populated ``lib`` output (containing ``stdarg.h`` and the
  rest), so there is no need to compute ``lib.getLib`` paths by hand. Using the
  compiler's own ``-print-resource-dir`` avoids the trap of pointing at the
  empty ``out`` resource directory.
- ``LD`` is the raw ``ld.lld``, and ``AR``, ``NM``, ``OBJCOPY`` and the rest of
  the binutils are the ``llvm-*`` tools. These come for free from ``LLVM=1``
  because the toolchain already puts the raw ``lld`` and ``llvm`` binaries on
  ``PATH``.

The resulting target invocation is::

   make ... LLVM=1 CC=<unwrapped-clang> \
       CFLAGS_KERNEL=-I<resource>/include \
       CFLAGS_MODULE=-I<resource>/include

A full ``defconfig`` builds clean under this recipe, and ``make rustavailable``
reports that Rust is available (see `Rust`_). Building Rust under Clang needs
one extra flag::

   BINDGEN_EXTRA_CLANG_ARGS=-Wno-unused-command-line-argument

bindgen probes the wrapped ``clang`` on ``PATH`` for its default include paths
and captures its ``-nostdlibinc``; libclang then rejects that as an unused
argument, and because bindgen treats any clang diagnostic as fatal the build
dies at the ``RUSTC``/bindgen step. Silencing that one warning for bindgen alone
fixes it.

How the devShell exports it
---------------------------

The store paths above are Nix-internal, so the unwrapped compiler and its
resource directory cannot be computed by code that runs outside the devShell.
The ``build-kernel`` devShell in ``vendor/nixos-flake`` therefore exports them
as environment variables (``KERNEL_CLANG_CC`` for the unwrapped ``clang`` and
``KERNEL_CLANG_RESOURCE`` for its resource-include directory), computed in Nix
from ``llvmPackages.clang-unwrapped``.

The ``f/kernel/build_flags.py`` step reads those two variables through the
devShell when ``compiler=clang`` and splices ``LLVM=1`` together with the
unwrapped ``CC`` and the ``CFLAGS_KERNEL``/``CFLAGS_MODULE`` resource includes
into the single make-flags string that every make step (configure, compile,
devtools, install) consumes. The kernel requires the same ``LLVM=`` value on
each make invocation when configuring and building through separate commands
(``Documentation/kbuild/llvm.rst``), so producing the flags once keeps the
toolchain consistent across steps. ccache composes naturally:
``CC="ccache <unwrapped-clang>"`` for the target, leaving ``HOSTCC`` wrapped.

QEMU under Clang
----------------

QEMU is the easy case and needs no special recipe. It is ordinary userspace and
does not use ``-nostdinc``, so the wrapped ``clang`` is the correct choice: it
redirects to Nix's libc, which QEMU links against, and none of the kernel's
three blockers apply. ``f/qemu/configure.py`` pins ``--cc=clang --cxx=clang++``
with ``-Qunused-arguments`` to drop the GCC-only
``-Wa,--compress-debug-sections`` that ``clang`` sees as unused on link.

Rust
====

Building a kernel with ``CONFIG_RUST=y`` needs a Rust toolchain inside the
kernel's supported version window, and that window moves with the kernel
version. This chapter records where the requirements are stated, how to
cross-match a kernel to a toolchain, what the ``build-kernel`` devShell
provides, and how to pin an older toolchain, so that a "bump the kernel" or
"bump the toolchain" task does not have to re-derive any of it.

Where the requirements are stated
---------------------------------

Check these for the exact kernel ref you intend to build; they differ between
versions. All but the last are in the kernel source tree, in rough priority
order:

``scripts/min-tool-version.sh``
   The canonical, machine-readable source. It echoes the enforced minimum
   ``rustc``, ``bindgen``, ``llvm`` (and GCC) versions per tool, and is what the
   build actually checks.

``scripts/rust_is_available.sh``
   The gate behind ``make rustavailable``. It reads ``min-tool-version.sh``,
   probes the toolchain, and exits non-zero (``*** Rust compiler '...' is too
   old``) if a tool is below the minimum, which makes Kconfig set
   ``RUST_IS_AVAILABLE=n``.

``Documentation/process/changes.rst``
   The human-readable "Minimal requirements" table (Rust, bindgen, GNU C,
   Clang/LLVM), a mirror of the enforced minimums.

``Documentation/rust/quick-start.rst``
   The setup guide: install instructions plus the recommended toolchain and the
   ``RUST_LIB_SRC`` wiring.

The Rust for Linux `rust-version-policy`_ is the why behind the moving window:
the minimum tracks Debian Stable's ``rustc`` and advances roughly per Debian
release, while there is no hard maximum, since new releases are CI-tested and
have worked with every version since the minimum. bindgen is CI-tested with no
separate written policy; ``rustfmt`` is not version-gated, as it is optional
formatting and effectively tracks the ``rustc`` version.

.. _rust-version-policy: https://rust-for-linux.com/rust-version-policy

Reading it for a given ref, in a kernel checkout::

   scripts/min-tool-version.sh rustc      # -> e.g. 1.85.0
   scripts/min-tool-version.sh bindgen    # -> e.g. 0.71.1
   scripts/min-tool-version.sh llvm       # -> e.g. 15.0.0

That minimum, plus "no hard maximum but newer is CI-tested", is the whole
requirement.

Cross-matching a kernel to a toolchain
--------------------------------------

The minimums below come from ``scripts/min-tool-version.sh`` at each ref. The
"custom-target gate" is whether the core Rust build path passes
``-Zunstable-options``, which ``rustc`` 1.85 and newer need to load the kernel's
custom target JSON (older ``rustc`` loaded it under ``RUSTC_BOOTSTRAP=1``
alone). That single fact decides whether a newer ``rustc`` works.

============= ========= =========== =========== ================
Kernel        min rustc min bindgen -Zunstable? works with 1.95?
============= ========= =========== =========== ================
v6.11 - v6.18 1.78.0    0.65.1      no          no
v7.1+         1.85.0    0.71.1      yes         yes
============= ========= =========== =========== ================

The consequences are that no single ``rustc`` serves both ends: v6.18 needs
``rustc`` in roughly the [1.78, 1.84] range, before ``rustc`` tightened
custom-target loading, while v7.1 and newer need 1.85 or later and tolerate
1.95. A toolchain good for one silently fails the other. The minimum bump is the
trigger: ``rustc`` minimum went 1.78 to 1.85 at v7.1 (the Debian Stable cadence)
and bindgen minimum went 0.65.1 to 0.71.1, so always re-read
``min-tool-version.sh`` at the new ref before bumping the kernel.

To extend the table for a new ref, read its ``min-tool-version.sh`` (``rustc``
plus ``bindgen``) and check ``rust/Makefile`` and ``scripts/Makefile.build``
``cmd_rustc_library`` for ``-Zunstable-options`` on the core path.

A too-old toolchain builds without Rust, silently
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If ``rustc`` is below the kernel's minimum, ``rust_is_available.sh`` fails,
Kconfig sets ``RUST_IS_AVAILABLE=n``, and any preset with ``CONFIG_RUST=y`` is
silently downgraded: ``alldefconfig`` and ``olddefconfig`` drop it and the build
succeeds without Rust. Confirm that Rust actually built by checking that the
resulting config still carries ``CONFIG_RUST=y``::

   grep -E '^CONFIG_RUST(_IS_AVAILABLE)?=' <build_dir>/.config

What the devShell provides
--------------------------

The ``build-kernel`` devShell in ``vendor/nixos-flake`` (its ``matrixExtras``)
ships nixpkgs' own ``rustc``, ``rust-bindgen``, and ``rustfmt``, plus
``rustPlatform.rustLibSrc`` exported as ``RUST_LIB_SRC`` so the kernel builds
``core`` and ``alloc`` from source. On nixos-26.05 that is ``rustc`` 1.95.0 with
bindgen 0.72.1, which is inside the v7.1 and newer window, so it suits modern
kernels (v7.1 and later).

Pinning an older toolchain
--------------------------

To build a pre-v7.1 kernel with Rust, pin an older ``rustc`` (with matching
rust-src and a compatible bindgen) into the ``build-kernel`` devShell. The
proven approach is a ``rust-overlay`` input:

.. code-block:: nix

   # flake.nix
   inputs.rust-overlay.url = "github:oxalica/rust-overlay";
   inputs.rust-overlay.inputs.nixpkgs.follows = "nixpkgs";
   # apply rust-overlay.overlays.default in the pkgs import, then:
   # lib/toolchain.nix
   rustForKernel = pkgs.rust-bin.stable."1.80.0".default.override {
     extensions = [ "rust-src" ];
   };
   # use rustForKernel in matrixExtras; RUST_LIB_SRC =
   #   "${rustForKernel}/lib/rustlib/src/rust/library";

This was in place briefly for kernel 6.18 before the project moved to modern
kernels. Selecting the pin automatically from the kernel version range, rather
than editing the flake by hand, is the goal of the planned per-kernel Rust
requirements work.

.. _Clang/LLVM: https://llvm.org/
.. _Rust: https://www.rust-lang.org/
