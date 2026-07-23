<!--
Status: PLANNED, not started (2026-07-23).
The sanitizer facts below are verified against the QEMU source at
~/wt/vanilla/qemu (v11.0.2, e545d8bb9d), the tree this project builds,
and by real compile-and-link probes run inside the build-qemu devShell;
do not re-derive them. An earlier revision verified them against
~/src/qemu (ffcf1a7981, 2026-02-28), which predates v11.0.2 and got the
LeakSanitizer suppression path wrong; see the note in that section.
Origin: a handoff from the QEMU NVMe MDTS series work in
~/src/linux-kdevops/refactor/kdevops/data/qemu, branch
align-nvme-mdts-with-linux-v2. The reproducer branch is ubsan-test in
~/.git-bare/qemu.git (remote old-local-bare).
Related: f/qemu/build.flow (the build), f/qemu/identity.py (the hash
this plan extends), f/qsu/qemu-system/render (the guest runtime),
docs/flows/qemu-build.rst (the page that documents it).
-->

# Build QEMU under sanitizers and verify a fix with them

## Context

The QEMU build flow produces a `qemu-system-<arch>` that each guest's
`qemu-system@<vm>.service` unit runs. It offers a toolchain choice (GCC
or Clang/LLVM), reproducibility, ccache and a free-form
`configure_args`, but nothing that builds the emulator under a
sanitizer, and nothing that captures a sanitizer's diagnostics from a
running guest.

The immediate driver is an upstream QEMU patch series that bounds the
MDTS and ZASL shifts in `hw/nvme` through a new `nvme_max_data_size()`
helper. The series claims the pre-fix `page_size << mdts` is undefined
behaviour per C11 6.5.7p3. That claim is reasoned about but not
empirically demonstrated. Demonstrating it needs a QEMU built with
UndefinedBehaviorSanitizer, a guest that drives the vulnerable path,
and a way to read the diagnostic back out. All three are missing.

The general capability is worth more than the one verification. A
sanitizer-built emulator turns a class of latent QEMU bugs into
observable ones for every flow that boots a guest, the same way the
five suite executors turned guest-side kernel failures into verdicts.
So the work is sequenced as infrastructure first, with the MDTS claim
as its first live-fire validation.

## Verified facts (do not re-derive)

### The configure options

From `./configure --help` in the source tree:

    --enable-asan            enable address sanitizer
    --enable-tsan            enable thread sanitizer
    --enable-ubsan           enable undefined behaviour sanitizer
    --enable-fuzzing         build fuzzing targets

`meson_options.txt` declares `asan`, `ubsan` and `tsan` as booleans
defaulting to false, alongside `safe_stack`, `cfi`, `cfi_debug` and
`fuzzing`. There is no `--enable-sanitizers` umbrella option. An
earlier session invented one; it does not exist.

### ThreadSanitizer excludes the other two

`meson.build` refuses the combination outright:

    if get_option('tsan')
      if get_option('asan') or get_option('ubsan')
        error('TSAN is not supported with other sanitizers')

This is the fact that shapes the form. The selection is one choice
from a set, not three independent switches, so the schema must be an
enum and the mutual exclusion must be unrepresentable rather than
validated after the fact.

### What each option adds

`--enable-asan` prepends `-fsanitize=address` to both the compile and
link flags.

`--enable-ubsan` first probes that the compiler can link
`-fsanitize=undefined` (guarding against a known GCC static-link bug),
then adds it to compile and link flags, and adds
`-fno-sanitize=function` when supported. The function-type-mismatch
check is suppressed because the TCG prologue emits no function type
prefix. Everything else in `-fsanitize=undefined` stays on, including
the shift-count, shift-base and shift-exponent checks the MDTS work
needs.

`--enable-tsan` additionally requires `__tsan_create_fiber` from
`<sanitizer/tsan_interface.h>` and adds `-Wno-tsan` where supported.

### Upstream's own drivers are the template

`tests/docker/test-debug` builds with Clang/LLVM, `--enable-debug
--enable-asan --enable-ubsan`, and exports
`ASAN_OPTIONS=detect_leaks=0` for the `make check` that follows.

`tests/docker/test-tsan` builds with Clang/LLVM, `--enable-tsan
--disable-werror --extra-cflags=-O0`, and exports `TSAN_OPTIONS` with
`suppressions=`, `detect_deadlocks=false`, `history_size=7`,
`halt_on_error=0`, `exitcode=0`, `verbose=5` and a `log_path=`. The
comment there records that `exitcode=66` is the variant that makes
ThreadSanitizer fail the run instead of continuing.

Note what upstream does not do: it passes `--disable-werror` and
`-O0` for ThreadSanitizer only, not for the other two. Do not
generalise that to every sanitizer without evidence.

### The suppression files

`tests/tsan/suppressions.tsan` and `tests/tsan/ignore.tsan` exist and
are byte-identical across both trees checked.

The LeakSanitizer suppression file moved. At v11.0.2 it is at
`scripts/lsan_suppressions.txt`; before commit e9f55f543f ("scripts:
Move lsan_suppressions.txt out of oss-fuzz subdir", 2026-03-06) it was
at `scripts/oss-fuzz/lsan_suppressions.txt`. An earlier revision of
this plan asserted the oss-fuzz path as a correction to the source
handoff. That was wrong for the tree being built: the handoff had it
right for v11.0.2, and the check behind the correction ran against a
tree that predates the move. The lesson is not that one path is
correct but that this path is version-dependent, which phase 2 has to
handle.

There is no UndefinedBehaviorSanitizer suppression file, so shift
diagnostics surface unfiltered, which is what the MDTS verification
wants.

### ThreadSanitizer needs an instrumented glib

`docs/devel/testing/main.rst` states that all code including shared
library dependencies must be built with the sanitizer, and that glib's
synchronisation primitives are otherwise unrecognised and produce false
positives. Upstream's remedy is to build glib with
`-fsanitize=thread` and point `LD_LIBRARY_PATH` at it. In this project
that means a nixpkgs glib override, which is a distinct piece of work.
ThreadSanitizer is therefore out of scope for the first phases and the
enum should carry it only once that override exists.

### The devShell toolchain carries all three runtimes

Probed by compiling and linking a trivial program inside the
`build-qemu` devShell, plus the `__tsan_create_fiber` gate that the
ThreadSanitizer meson check performs:

    gcc-wrapper-15.2.0    -fsanitize=undefined  link OK
    gcc-wrapper-15.2.0    -fsanitize=address    link OK
    gcc-wrapper-15.2.0    -fsanitize=thread     link OK
    clang-wrapper-21.1.8  -fsanitize=undefined  link OK
    clang-wrapper-21.1.8  -fsanitize=address    link OK
    clang-wrapper-21.1.8  -fsanitize=thread     link OK
    gcc, clang            __tsan_create_fiber   present

Both toolchains in the existing devShell are ready. No packaging work
is needed to start.

## Where this plugs in

### The build identity is the load-bearing point

`f/qemu/identity.py` hashes the target list, the configure arguments,
the compiler, the toolchain derivation path and the source tree into a
12-hex identity, and that identity names the install prefix. Both
`f/qemu/publish.py` and `f/qemu/reuse_check.py` derive the store key
from the prefix basename, and `f/qsu/resolve.py` picks a QEMU for a
bringup out of that same store index.

If the sanitizer selection does not enter the hash, a build with
UndefinedBehaviorSanitizer and a stock build of the same ref collide on
one prefix, and the reuse check returns whichever landed first. The
failure is silent and it would poison exactly the verification this
plan exists to perform. The selection must be hashed.

Because the prefix basename flows through to the store key and the
bringup picker, appending the selection as its own segment makes the
whole chain self-labelling for free: a build reads
`qemu-11.0.0-vanilla-ubsan-<identity>` in the picker rather than a bare
hash that a reader cannot tell apart from a stock build.

### The runtime environment is already wired

The `qemu-system@.service` template carries
`EnvironmentFile=%E/systemd/qemu-system/%i.env`, and
`f/qsu/qemu-system/render.py` renders that file from
`vendor/qemu-system-units/templates/vm.env.j2`. The template currently
emits `QEMU_BINARY`, `QEMU_ARGS`, `VSOCK_CID`, `SSH_KEY_PATH` and
`KERNEL_ARGS`. The `UBSAN_OPTIONS` and `ASAN_OPTIONS` variables slot in
beside them with no new mechanism.

`vendor/qemu-system-units` is a vendored subproject with its own
conventions and commit rules, so that template change is made upstream
in the subproject and then re-vendored, not edited in place as a
kdevops-ng change.

The unit sets `SyslogIdentifier=qemu-system@%i`, so the emulator's
standard error reaches the journal under a known identifier. Reading
diagnostics back is the same journal read the five suite executors
already perform, which means the collection step has a working pattern
to follow rather than a new one to invent.

### The NVMe knobs already exist

`f/qsu/qemu-system/render` exposes `mdts`, `cmb_size_mb`, `pmr_size`,
`pmr_pmem`, `legacy_cmb` and the atomic write parameters as form
fields. The MDTS reproducer needs no new plumbing to reach the
vulnerable path, only a value in a field that is already there.

## The plan

### Phase 1: build-side selection

Add a `sanitizer` enum to the `configuration` group of
`f/qemu/build.flow`, defaulting to `none`, with `ubsan`, `asan` and
`asan+ubsan` as the initial members and `tsan` withheld until the glib
override exists. The enum encodes the mutual exclusion in the type, so
an unsupported combination cannot be expressed.

`f/qemu/configure` derives the `--enable-*` arguments from the
selection and appends them to the argv it already composes. It derives
`--disable-werror` and `--extra-cflags=-O0` for ThreadSanitizer only,
matching upstream's driver; the other selections keep the project's
default warning posture until evidence says otherwise.

`f/qemu/identity` hashes the selection, and `_prefix_basename` appends
it as its own segment after the label.

Fixture tests extend `tests/test_qemu_identity.py` (a distinct identity
per selection, and the segment landing in the prefix basename) and
`tests/test_qemu_configure.py` (the derived argv, and the
ThreadSanitizer extras appearing only for that selection).

### Phase 2: self-contained artifacts

`f/qemu/install` copies `tests/tsan/suppressions.tsan`,
`tests/tsan/ignore.tsan` and the LeakSanitizer suppression file into
`<prefix>/share/qemu-sanitizers/`.

The LeakSanitizer file is resolved by probing `scripts/` first and
falling back to `scripts/oss-fuzz/`, because it moved between the two
locations and the flow builds arbitrary refs on both sides of that
move. Copying whichever exists, and naming the resolved source in the
job log, keeps an older ref buildable without a hardcoded path that is
wrong half the time.

Without this step a `suppressions=` path points into a build worktree,
which breaks the moment the artifact is moved to a peer with
`nix copy`. The store artifact must describe itself.

### Phase 3: runtime options

The vendored `vm.env.j2` emits the `UBSAN_OPTIONS` and `ASAN_OPTIONS`
variables, fed by the render step from the selection and the prefix.

The defaults are `halt_on_error=0` and `print_stacktrace=1`, so a
diagnostic records itself without killing a guest in the middle of a
workload, and the verdict is deferred to the collection step. This is
the same honest-red discipline the suite executors use: observe
everything, then judge once, rather than failing at the first sign of
trouble and losing the rest of the evidence.

### Phase 4: the test loop

A thin `f/qemu/check.flow` composing `f/qemu/build` and `f/qsu/boot` as
subflows, then a guest workload, then `collect_diagnostics` (journal
read and parse), `judge` and `report`.

Structurally this is the suite-executor pattern with one inversion: the
evidence is host-side, produced by the QEMU process itself, rather than
guest-side. That difference is confined to the collection step; the
verdict and report steps follow the existing shape.

### Phase 5: live fire on the MDTS claim

The reproducer branch is `ubsan-test`: v11.0.0 (98b060da3a) plus the
first three commits of the series, so the `nvme_max_data_size()` helper
is defined but not yet wired into `nvme_check_mdts()`, and the
realize-time cap is still the original one. Build it with
`sanitizer: ubsan`, boot a guest with an NVMe drive at `mdts=32`, and
drive any I/O.

Two distinct undefined shifts are reachable on that branch with that
one value, and both are shift-exponent violations rather than value
overflows:

`ctrl.c:8641`, the realize-time check `(1 << n->params.mdts) + 1 >
IOV_MAX`, shifts an `int` by 32. This one fires while the device
realizes, so it needs no guest workload at all.

`ctrl.c:1699`, `len > n->page_size << mdts`, shifts a `uint32_t` by 32.
It fires on every command that reaches the check.

The second is reachable only because the first is broken. On x86 the
shift count is masked, so `1 << 32` yields 1, the condition evaluates
false, and the realize check passes a value it was written to reject.
Verified by compiling the two expressions standalone with the same
devShell GCC: at `mdts=32` and `mdts=40` both emit `shift exponent N is
too large for 32-bit type`, and the realize condition evaluates false
in both cases. `IOV_MAX` is 1024 in the devShell.

Two numbers matter and neither is obvious. Use `mdts=32`, not the 25
the origin handoff suggests: at 25 the realize check correctly rejects
the device (`(1 << 25) + 1 > 1024`), the guest never starts, and
nothing is sanitized. And `mdts=31` produces no diagnostic either,
because GCC does not treat `1 << 31` on `int` as an error and, more
importantly, `page_size` is a `uint32_t`, so a shift whose result
overflows is defined modular arithmetic. That last point sharpens what
the series should claim: below 32 the expression is well defined but
silently wraps, at `mdts=25` all the way to zero, which rejects every
command; at and above 32 it is undefined. Both are bugs and the helper
fixes both, since casting to `uint64_t` moves the width to 64 and the
`exp >= 64 - page_bits` guard stops the value overflowing too. Only the
second is undefined behaviour, and only the second is what
UndefinedBehaviorSanitizer will show.

Then confirm the fix silences it: the same guest against a build that
includes the wiring commit should produce neither diagnostic.

This target is a better first live fire than a synthetic workload
because the realize-time site needs nothing but a boot, which exercises
the whole chain (build, publish, resolve, boot, journal, collect,
report) before any workload is involved. The `mdts` field already
exists in `f/qsu/qemu-system/render`, so no new plumbing is needed to
set it.

The reproducer is then written up so the fix can be regressed later.

### Deployment

Every phase lands staged. Deploy with `nix run .#deploy-staging`, add
the new paths to the `stagingOnlyPrune` block in
`nix/apps/default.nix`, and document the flow on an `:orphan:` page
listed in `docs/staging.rst`. Promotion to the `kdevops` workspace is
dropping the prune entry once the work is exercised and blessed.

## Decisions taken

The sanitizer selection is hashed as an unconditional sixth element of
the identity blob, not conditionally on being set. The alternative,
hashing it only when it is not `none` so that stock builds keep their
current digest, was considered and rejected in favour of the simpler
and more uniform rule. The consequence is accepted: every existing
QEMU build identity changes, so previously published `qemu-*` store
entries stop matching and each artifact that is still wanted recompiles
once.

The infrastructure is built before the MDTS verification, rather than
hacking the reproducer together first and generalising afterwards.

## Open risks (verify, do not assume)

Whether a QEMU built with AddressSanitizer runs under `-accel kvm` is
unverified. It gates phases 3 through 5 for that selection.
UndefinedBehaviorSanitizer carries much less risk here, which is
another reason to lead with it.

Whether AddressSanitizer and UndefinedBehaviorSanitizer builds need
`--disable-werror` against the nixpkgs toolchain is unverified.
Upstream disables it for ThreadSanitizer only, so it is not added
preemptively; the qemu-build page already documents `--disable-werror`
as the remedy when a build hits a warning.

Whether the diagnostics are better captured from the journal or from a
sanitizer `log_path=` is undecided. The journal matches the existing
collection pattern and is the starting assumption; ThreadSanitizer's
volume may later argue for files.

Each of these is a cheap experiment. Run it rather than assuming, and
record the result here. The origin handoff was written after a session
that invented a configure flag, and its standing rule applies to this
plan too: confirm every flag by running the tool or reading the build
system, and label any claim that has not been confirmed.

Add one rule this revision earned: pin the tree a fact was checked
against. The whole sanitizer surface is identical between v11.0.2 and
a tree from two months earlier, apart from one file that moved, and
that single difference was enough to put a wrong path into the plan.
Facts about a moving source have a version attached whether or not it
is written down.
