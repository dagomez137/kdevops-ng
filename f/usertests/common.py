# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Shared library for the f/usertests/* steps (host side of a userspace-harness
# run); imported as f.usertests.common. A run item is a HARNESS BINARY the
# kernel tree builds for userspace under tools/testing/ (radix-tree, vma,
# rbtree, memblock, scatterlist), named `<dir>/<binary>`; the guest executor is
# the `usertests@<instance>.service` template, the instance being the
# systemd-escaped item (`radix-tree/main` -> `radix\x2dtree-main`). The catalog
# below curates the harnesses and their deliberately inconsistent upstream
# verdict conventions, each verified against the v7.2-rc1 sources;
# f/usertests/collect encodes the verdict rules.
#
# The executor's load-bearing properties (the usertests@.service template):
#   * `ExecStart=stdbuf --output=line /var/lib/usertests/%v/tree/%I $ARGS`
#     with NO `-` prefix: exit codes are REAL, so a job outcome of `done`
#     proves the harness exited 0, and an assert/sanitizer abort surfaces as
#     `failed` with `EXIT_STATUS` populated (e.g. signal ABRT);
#   * `EnvironmentFile=-/var/lib/usertests/%v/env/%I.env`, one env file PER
#     INSTANCE (`%I` unescapes back to the item path), carrying `ARGS=`;
#   * `Environment=` pins ASAN_OPTIONS=abort_on_error=1,
#     UBSAN_OPTIONS=halt_on_error=1 and an explicit LSAN_OPTIONS, so a
#     sanitizer finding aborts the run instead of scrolling past.
#
# The contract with the guest side (host share == guest /var/lib/usertests):
#   $WORKERS_DIR/shared/usertests/<vm>/<kver>/tree/<dir>/<binary>      binaries
#   $WORKERS_DIR/shared/usertests/<vm>/<kver>/env/<dir>/<binary>.env   ARGS=
#   $WORKERS_DIR/shared/usertests/<vm>/<kver>/report.json              rollup
#   $WORKERS_DIR/shared/usertests/<vm>/harnesses.json                  picker
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from f.common.remote import list_vms as list_vms

# The guest mount point of the `usertests` share (the units' state dir).
GUEST_STATE_DIR = "/var/lib/usertests"
GUEST_TAG = "usertests"

# Curated tools/testing/ harnesses, featured order: tests first. Every
# entry is verified against the kernel worktree (v7.2-rc1); the per-entry
# comment cites the output format and exit behavior.
#   kind         "test" | "bench"
#   summary_re   run-scoped output regex, named groups passed/total (equal
#                required) or run/passed/failed (failed must be 0) for vma;
#                None when no counts are printed
#   sentinel_re  output regex proving the suite actually ran, for entries with
#                no summary_re; its absence fails the item
#   silent_ok    True for a harness silent on pass: a clean exit alone (plus
#                the global sanitizer/assertion scans) is the verdict
#   count_lines  True for memblock's kselftest-style per-test lines: passed =
#                sentinel_re matches (`: passed$`), failed = fail_line_re
#                matches (`: failed$`, must be 0), at least one pass required
#   fail_line_re per-line failure regex for the count_lines policy
#   args         static ARGS for the item's env file; radix-tree/main composes
#                its ARGS from the run form's seed/long_run knobs instead
CATALOG: dict[str, dict] = {
    # tools/testing/vma/vma.c main(): prints per-failure "Test %s FAILED"
    # lines, closes with "%d tests run, %d passed, %d failed.", and returns
    # EXIT_FAILURE iff any test failed: real exit semantics AND a summary.
    # Sub-second.
    "vma/vma": {
        "label": "VMA userland tests (vma/vma)",
        "kind": "test",
        "summary_re": (
            r"(?P<run>\d+) tests run, (?P<passed>\d+) passed, "
            r"(?P<failed>\d+) failed\."
        ),
    },
    # tools/testing/radix-tree/xarray.c: the userspace build of
    # lib/test_xarray.c; closes with "XArray: %u of %u tests passed"
    # (equal counts required). Minutes.
    "radix-tree/xarray": {
        "label": "XArray (radix-tree/xarray)",
        "kind": "test",
        "summary_re": r"XArray: (?P<passed>\d+) of (?P<total>\d+) tests passed",
    },
    # tools/testing/radix-tree/maple.c: the userspace build of
    # lib/test_maple_tree.c; closes with "maple_tree: %u of %u tests passed".
    # Minutes; the longest harness here.
    "radix-tree/maple": {
        "label": "Maple tree (radix-tree/maple)",
        "kind": "test",
        "summary_re": r"maple_tree: (?P<passed>\d+) of (?P<total>\d+) tests passed",
    },
    # tools/testing/radix-tree/idr-test.c: closes with "IDA: %u of %u tests
    # passed". ida_check_bad_free() deliberately frees unallocated ids between
    # its 'vvv Ignore "not allocated" warnings' and '^^^ "not allocated"
    # warnings over' marker lines; the `assertion failed at` lines are the
    # deliberate ida_free WARNs from lib/idr.c. The markers travel on stdout
    # while the WARN lines are unbuffered stderr, so under journald they can
    # land outside the marker window: expected_assert_re whitelists the idr.c
    # signature itself. Safe because a REAL assert() aborts the process (the
    # plumbing catches it); a surviving assertion line can only be WARN-class,
    # and lib/idr.c's deliberate noise is the only WARN source here. main
    # embeds the same IDA suite and carries the same whitelist. Seconds.
    "radix-tree/idr-test": {
        "label": "IDR + IDA (radix-tree/idr-test)",
        "kind": "test",
        "summary_re": r"IDA: (?P<passed>\d+) of (?P<total>\d+) tests passed",
        "expected_assert_re": r"assertion failed at .*idr\.c:\d+",
    },
    # tools/testing/radix-tree/multiorder.c: silent on pass; every failure
    # macro aborts, so the exit code is the whole verdict. Seconds to a
    # minute.
    "radix-tree/multiorder": {
        "label": "Multi-order radix tree (radix-tree/multiorder)",
        "kind": "test",
        "silent_ok": True,
    },
    # tools/testing/radix-tree/main.c: the randomized omnibus. Logs
    # "random seed %u" (seeded or not), prints the XArray and IDA summaries
    # along the way, and closes with the "tests completed" sentinel; exits 0
    # unconditionally BUT every failure macro aborts (assert), so a `done`
    # outcome IS meaningful. Honors `-s <seed>` and `-l` (long run) via ARGS.
    # At least 30 s (a built-in sleep floor).
    "radix-tree/main": {
        "label": "Radix tree omnibus (radix-tree/main)",
        "kind": "test",
        "sentinel_re": r"^tests completed$",
        "expected_assert_re": r"assertion failed at .*idr\.c:\d+",
    },
    # tools/testing/memblock/main.c, run with ARGS=--verbose: kselftest-style
    # per-test lines ending ": passed" / ": failed"; assertions abort on
    # failure too, so collect counts the lines (zero failed, at least one
    # passed) on top of the clean exit. Seconds.
    "memblock/main": {
        "label": "Memblock simulator (memblock/main)",
        "kind": "test",
        "sentinel_re": r": passed$",
        "fail_line_re": r": failed$",
        "count_lines": True,
        "args": "--verbose",
    },
    # tools/testing/scatterlist/main.c: silent on pass; a failure prints
    # "Failed on '...'!" and exits 1, so the real exit code carries the
    # verdict. Sub-second.
    "scatterlist/main": {
        "label": "Scatterlist chaining (scatterlist/main)",
        "kind": "test",
        "silent_ok": True,
    },
    # tools/testing/rbtree/{rbtree_test,interval_tree_test}.c are deliberately
    # absent: at v7.2-rc1 lib/rbtree_test.c and lib/interval_tree_test.c call
    # the new kmalloc_objs() (include/linux/slab.h), which the shared shim's
    # slab.h does not provide, so the harness does not compile (upstream fix
    # candidate). Their WARN-only failure model (WARN_ON_ONCE prints
    # "assertion failed at <file>:<line>" and continues; verdict = clean exit
    # plus zero assertion lines) is already what the global scan implements,
    # so they slot back in as kind=bench entries once buildable.
}

_DEFAULTS = {
    "summary_re": None,
    "sentinel_re": None,
    "silent_ok": False,
    "count_lines": False,
    "fail_line_re": None,
    "expected_assert_re": None,
    "args": "",
}


def catalog_entry(item: str) -> dict:
    """The item's catalog entry with defaults filled; an uncataloged item (a
    hand-typed advanced pick) gets the strictest defaults: a test judged by its
    real exit code and the global sanitizer/assertion scans alone."""
    entry = CATALOG.get(item, {"label": item, "kind": "test", "silent_ok": True})
    return {**_DEFAULTS, **entry}


def item_args(item: str, seed: int = 0, long_run: bool = False) -> str:
    """The item's ARGS line content for its per-instance env file.

    radix-tree/main composes its flags from the run form's knobs: `-s <seed>`
    when seed > 0 (it logs `random seed %u` either way, so an unseeded run's
    seed is still archived by collect), `-l` for the long run. Every other
    item's ARGS is its catalog constant (memblock's `--verbose`, else empty).
    """
    if item == "radix-tree/main":
        parts: list[str] = []
        if int(seed) > 0:
            parts += ["-s", str(int(seed))]
        if long_run:
            parts.append("-l")
        return " ".join(parts)
    return catalog_entry(item)["args"]


def run_status(per_item: list[dict]) -> str:
    """The run verdict from the per-item collect results, the one rule
    `f/usertests/report` and `f/usertests/judge` share: `passed` only when
    every item passed and there was at least one (a skip_failures error object
    from a hard step failure is not a pass); aggregating nothing must never
    read as a pass."""
    ok = bool(per_item) and all(
        isinstance(s, dict) and s.get("status") == "passed" for s in per_item
    )
    return "passed" if ok else "failed"


def unit_escape(name: str) -> str:
    """A `<dir>/<binary>` item name as a systemd unit instance string.

    Implements systemd's `unit_name_escape` (src/basic/unit-name.c, do_escape):
    `/` becomes `-`; ASCII alphanumerics, `:`, `_` and `.` stay; everything else
    (including `-` and `\\`) becomes `\\x<2-hex-lowercase>` of the byte, and a
    leading `.` is escaped too. The guest unit's `%I` specifier unescapes it
    back, so `usertests@radix\\x2dtree-main.service` runs `radix-tree/main`.
    """
    out: list[str] = []
    for i, b in enumerate(name.encode()):
        c = chr(b)
        if c == "/":
            out.append("-")
        elif (c.isascii() and c.isalnum() or c in ":_.") and not (c == "." and i == 0):
            out.append(c)
        else:
            out.append(f"\\x{b:02x}")
    return "".join(out)


# The one guest template every run item maps onto.
UNIT_TEMPLATE = "usertests"


def item_unit(item: str) -> str:
    """The guest unit a run item starts: `usertests@<esc>.service`, the
    instance being the systemd-escaped `<dir>/<binary>` item name."""
    return f"{UNIT_TEMPLATE}@{unit_escape(item)}.service"


def _workers() -> Path:
    return Path(os.environ["WORKERS_DIR"])


def share_dir(vm_name: str, workers: Path | None = None) -> Path:
    """Host path of the VM's `usertests` virtiofs share, name-escape hardened.

    `$WORKERS_DIR/shared/usertests/<vm_name>`, mounted in the guest at
    `/var/lib/usertests`. Lives under `shared/` so every worker sees the same
    bytes the guest's virtiofsd serves; `vm_name` is resolved and checked to
    sit directly under the share root, so a crafted name (`../x`) can never
    write outside it.
    """
    root = (workers or _workers()) / "shared/usertests"
    path = (root / vm_name).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"vm_name {vm_name!r} resolves outside {root}")
    return path


def harnesses_cache(vm_name: str, workers: Path | None = None) -> Path:
    """Per-VM cache of the built kernel's usertests harness items.

    `f/usertests/discover` writes the store artifact's MANIFEST items here;
    the run form's `list_harnesses` picker reads them, since a form dynselect
    cannot reach the guest over vsock.
    """
    return share_dir(vm_name, workers) / "harnesses.json"


def _atomic_write(path: Path, data: str, mode: int = 0o644) -> None:
    """Write via a hidden temp file + rename so a concurrent reader on the
    shared dir never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        # fdopen owns the fd from here; fchmod inside the with block so the raw
        # fd can never leak on a chmod failure.
        with os.fdopen(fd, "w") as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def list_harnesses(vm_name: str = "", filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_harnesses` entrypoint: the built harnesses, named.

    Reads the per-VM cache `f/usertests/discover` writes from the store
    artifact's MANIFEST (a form dynselect cannot reach the guest over vsock),
    human-labeled from the curated catalog and featured in catalog order
    (tests first, benchmarks last). Falls back to the catalog
    before the first discovery, so it is never an empty box.
    """
    cached: list[str] = []
    vm = (vm_name or "").strip()
    if vm:
        try:
            data = json.loads(harnesses_cache(vm).read_text())
            cached = [h for h in data if isinstance(h, str) and h]
        except Exception:
            cached = []
    names = cached or list(CATALOG)
    ordered = [h for h in CATALOG if h in names] + [
        h for h in names if h not in CATALOG
    ]
    needle = (filterText or "").lower()
    return [
        {"value": h, "label": catalog_entry(h)["label"]}
        for h in ordered
        if needle in h.lower() or needle in catalog_entry(h)["label"].lower()
    ]


def main():
    """Library module imported by the f/usertests/* steps; not a runnable step."""
    return "f/usertests/common: harness catalog, verdict rules + harness listing"
