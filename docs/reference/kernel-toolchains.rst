.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

=================
Kernel toolchains
=================

Building the kernel needs a toolchain that satisfies the kernel's own
requirements, and on Nix that takes more care than passing a single flag. The
first toolchain with non-obvious wiring is Clang/LLVM, where the nixpkgs
cc-wrapper fights the kernel's include handling. This page records the durable
recipe. The Clang/LLVM recipe is implemented in the ``build-kernel`` devShell of
``vendor/nixos-flake`` and consumed by ``f/kernel/build_flags.py``.

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
reports that Rust is available. Building Rust under Clang needs one extra flag::

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
