.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

===================================
The kernel's pre-submission checks
===================================

What the kernel's own documentation asks a contributor to do before sending a
patch, and a maintainer before applying one, and which step of
:doc:`/flows/kernel-build` does it, under its Checks group. The sources are
``Documentation/process/submit-checklist.rst`` and
``Documentation/process/submitting-patches.rst``, with the commands from
``Documentation/dev-tools/``, ``Documentation/doc-guide/`` and
``Documentation/rust/``. Every row quotes or names those files.

C
=

.. list-table::
   :header-rows: 1
   :widths: 36 32 32

   * - The documentation asks
     - The command
     - The step or knob
   * - Build with ``O=builddir``, no warnings or errors
     - ``make O=build``
     - configure, ``compile``
   * - Pass ``allnoconfig`` and ``allmodconfig``
     - ``make allnoconfig``, ``make allmodconfig``
     - **Configuration**; one per run
   * - New code compiled with extra warnings
     - ``make W=1``, ``make KCFLAGS=-W``
     - ``check_warnings`` for ``W=1``; ``KCFLAGS=-W`` via ``make_flags``
   * - "Check for trivial violations with the patch style checker"
     - ``scripts/checkpatch.pl [--strict] *.patch``
     - ``check_style``
   * - "Check cleanly with sparse"
     - ``make C=2 <object>``
     - ``check_sparse``
   * - "Use ``make checkstack`` and fix any problems that it finds"
     - ``make checkstack``
     - ``check_stack``
   * - "Include kernel-doc to document global kernel APIs"
     - ``scripts/kernel-doc --none -Wall <files>``
     - ``check_kerneldoc``
   * - UAPI headers stay backwards compatible
     - ``scripts/check-uapi.sh -p <base>``
     - ``check_uapi``
   * - Semantic patches (``Documentation/dev-tools/coccinelle.rst``)
     - ``make C=2 CHECK=scripts/coccicheck <object>``
     - ``check_cocci``, opt-in
   * - clang static analysis (``make help``)
     - ``make clang-tidy``, ``make clang-analyzer``
     - ``check_clang``, opt-in, Clang Builds
   * - "Any Documentation/ changes build successfully"
     - ``make htmldocs``
     - ``build_htmldocs``, opt-in
   * - Send patches, with their base
     - ``git format-patch --base=<commit> --cover-letter``
     - ``format_patch``
   * - Select the recipients from the patches
     - ``scripts/get_maintainer.pl *.patch``
     - ``find_maintainers``

The ``W=`` levels are :cmd:`make`'s own: ``1`` for warnings that "may be
relevant and do not occur too often", ``2`` for ones that "occur quite often",
``3`` for "more obscure" ones, ``e`` to make them errors; they combine as
``W=12``. ``W=1`` also runs kernel-doc over each compiled C file, but not over
headers, which is why ``check_kerneldoc`` exists. ``C=1`` runs sparse on the
files being recompiled and ``C=2`` on the named objects whether or not they need
it, which is why the step uses ``C=2`` after a full build.

Rust
====

.. list-table::
   :header-rows: 1
   :widths: 36 32 32

   * - The documentation asks
     - The command
     - The step
   * - Whether the toolchain is usable (``rust/quick-start.rst``)
     - ``make LLVM=1 rustavailable``
     - ``check_rustavailable``
   * - "The code should be formatted using ``rustfmt``"
     - ``make LLVM=1 rustfmtcheck``
     - ``check_rustfmt``
   * - `Clippy`_'s extra lints (``rust/general-information.rst``)
     - ``make LLVM=1 CLIPPY=1``
     - ``check_clippy``
   * - The code documentation builds
     - ``make LLVM=1 rustdoc``
     - ``build_rustdoc``
   * - "The ``rusttest`` host tests"
     - ``make LLVM=1 rusttest``
     - ``run_rusttest``
   * - The doctests in a running kernel (``rust/testing.rst``)
     - `KUnit`_'s ``rust_doctests_kernel``
     - :doc:`/flows/kunit`, on a booted guest
   * - The C side of ``rust/helpers/``
     - the C checks above
     - as C

The flow passes the Build's own ``make`` flags, so a GCC build runs these
without ``LLVM=1``; the documentation's ``LLVM=1`` is the invocation used for
compilation.

Maintainer
==========

A maintainer applies the series with ``b4 am`` (``b4 Series`` on the form,
``prepare_worktree``) and runs the same checks on it. ``scope`` ``worktree``
checks every file, as before pushing to a public branch.

Not covered
===========

These items need a person, a second machine, or a booted kernel, and the flow
reports nothing about them:

* Building on several architectures, 32 and 64 bit, big and little endian.
  The toolchain builds for the host architecture.
* Building with each modified Kconfig option ``=y``, ``=m`` and ``=n``, and the
  random combinations of ``SMP``, ``PREEMPT`` and the rest; one configuration
  per run.
* Every runtime item: the debug configs of "Test your code" item 1, lockdep,
  fault injection, ``kselftest`` and KUnit, testing against ``linux-next``.
  The suite flows run these on a booted guest.
* Review items with no command: headers included directly, memory barriers
  commented, Kconfig help text, new ``/proc`` entries and boot parameters
  documented.
* The commit message itself: the ``Signed-off-by`` (a person's certification),
  ``Fixes:`` with a 12-character SHA and the subject, ``Link:`` and
  ``Closes:``, ``Cc: stable@vger.kernel.org``, ``Assisted-by:`` for
  AI-assisted work. ``checkpatch.pl`` catches some of their form.
* Sending: :cmd:`git send-email` to the addresses ``find_maintainers`` listed,
  or :cmd:`b4` ``send``.

.. _Clippy: https://doc.rust-lang.org/clippy/
.. _KUnit: https://docs.kernel.org/dev-tools/kunit/
