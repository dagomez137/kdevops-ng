# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Shared library for the f/runtime_tests/* steps (host side of a runtime-tests
# run); imported as f.runtime_tests.common. A run item is a kernel test MODULE
# from lib/Kconfig.debug's RUNTIME_TESTING_MENU: loading it runs its whole
# suite in module_init, and the guest executor is upstream systemd's
# modprobe@<module>.service (already on every guest). The catalog below curates
# the modules and their deliberately inconsistent upstream exit conventions,
# each verified against the kernel sources; collect encodes the verdict rules.
#
# The executor's two load-bearing quirks (units/modprobe@.service.in):
#   * `ExecStart=-modprobe -abq %i`: the `-` prefix makes systemd treat ANY
#     modprobe exit as expected, so the start job finishes `done` for every
#     module, and an expected process exit is logged only at debug level,
#     which PID1 at its default log level never emits to the journal: the
#     init's exit status is structurally UNOBSERVABLE here, for both classes
#     (live-run confirmed: every module returns result=done with an empty
#     exec_status). The observable truths are the module's post-run load
#     state (`/sys/module/<module>`) and its kmsg output, so the verdict
#     reads exactly those;
#   * `ConditionKernelModuleLoaded=!%i`: an already-loaded module skips the
#     unit entirely (job outcome SD_MESSAGE_UNIT_SKIPPED), and a bare modprobe
#     of a loaded module would not re-run the tests either, so start/stop
#     defensively unload the stay-loaded modules.
#
# Host-side cache (a plain directory like kunit's, NOT a virtiofs share):
#   $WORKERS_DIR/shared/runtime-tests/<vm>/modules.json       picker cache
#   $WORKERS_DIR/shared/runtime-tests/<vm>/<kver>/report.json run rollup
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from f.common.remote import list_vms as list_vms

# systemd's stable journal MESSAGE_ID for a condition-skipped start job
# (sd-messages.h SD_MESSAGE_UNIT_SKIPPED); f/common/remote carries the
# started/failed/stopped/process-exit ids, this suite also needs the skip:
# modprobe@'s ConditionKernelModuleLoaded=!%i turns an already-loaded module
# into a skipped job, which must read as a failed run item, never a pass.
MSG_UNIT_SKIPPED = "0e4284a0caca4bfc81c0bb6786972673"

# Curated RUNTIME_TESTING_MENU modules, featured order: tests first, then
# stress, benchmarks last. Every entry is verified against the kernel worktree
# (v7.2-rc1); the per-entry comment cites the init return and output format.
#   loaded_on_pass  whether the module is loaded after a PASSING run: True
#            for the stay-loaded exit-honest class (init returns 0 on pass),
#            False for the auto-unload idiom (init returns an errno by
#            design) AND for test_ida, which auto-unloads on pass (-EINVAL)
#            and STAYS LOADED on failure (returns 0): with the exit status
#            unobservable, the load state is the surviving discriminator
#   kind     "test" | "stress" | "benchmark"
#   summary_re  cursor-scoped kmsg regex, named groups passed/total (a missing
#               group defaults to the other); None when no counts are printed
#   sentinel_re  kmsg regex proving the suite actually ran, for entries with
#                no summary_re; its absence fails a test module and marks a
#                stress/benchmark one notrun
#   unload   True when a run can leave the module loaded and start/stop must
#            `modprobe --remove` it so a re-run re-runs
#   scan_kmsg   False only for a module whose PASSING run emits real WARN
#               splats by design
#   fail_re  kmsg regex whose match alone fails the item (a failure channel
#            that is neither the load state nor a WARN/BUG splat)
CATALOG: dict[str, dict] = {
    # lib/test_xarray.c:2266-2267: printk "XArray: %u of %u tests passed",
    # returns 0 iff run==passed else -EINVAL; stays loaded on success.
    "test_xarray": {
        "label": "XArray (test_xarray)",
        "loaded_on_pass": True,
        "kind": "test",
        "summary_re": r"XArray: (?P<passed>\d+) of (?P<total>\d+) tests passed",
        "unload": True,
    },
    # lib/test_maple_tree.c:4007-4014: pr_info "maple_tree: %u of %u tests
    # passed", returns 0 iff run==passed else -EINVAL; stays loaded on success.
    "test_maple_tree": {
        "label": "Maple tree (test_maple_tree)",
        "loaded_on_pass": True,
        "kind": "test",
        "summary_re": r"maple_tree: (?P<passed>\d+) of (?P<total>\d+) tests passed",
        "unload": True,
    },
    # lib/test_rhashtable.c:820-880: basic-phase failures return -EINVAL, the
    # thread phase ends pr_info "Started %d threads, %d failed, rhltable test
    # returns %d" then returns 0, so the regex demands the all-clean line.
    "test_rhashtable": {
        "label": "Resizable hashtable (test_rhashtable)",
        "loaded_on_pass": True,
        "kind": "test",
        "summary_re": (
            r"Started (?P<total>[1-9]\d*) threads, 0 failed, "
            r"rhltable test returns 0"
        ),
        "unload": True,
    },
    # lib/test_hexdump.c:225-230 (pr_fmt "test_hexdump: "): pass prints "all
    # %u tests passed", fail pr_err "failed %u out of %u tests"; returns
    # failed_tests ? -EINVAL : 0; stays loaded on success.
    "test_hexdump": {
        "label": "Hexdump (test_hexdump)",
        "loaded_on_pass": True,
        "kind": "test",
        "summary_re": r"test_hexdump: all (?P<passed>\d+) tests passed",
        "unload": True,
    },
    # lib/test_bpf.c:15403-15406 (pr_fmt "test_bpf: "): pr_info "Summary: %d
    # PASSED, %d FAILED, [%d/%d JIT'ed]"; init returns -EINVAL on any failure,
    # 0 clean; stays loaded on success.
    "test_bpf": {
        "label": "BPF interpreter and JIT (test_bpf)",
        "loaded_on_pass": True,
        "kind": "test",
        "summary_re": r"test_bpf: Summary: (?P<passed>\d+) PASSED, 0 FAILED",
        "unload": True,
    },
    # lib/test_ida.c:276-277: INVERTED: printk "IDA: %u of %u tests passed",
    # then `return (tests_run != tests_passed) ? 0 : -EINVAL`, so -EINVAL is
    # the PASS (auto-unload) and 0 is the FAIL (stays loaded, hence unload).
    # A passing run fires real WARN splats by design (ida_check_bad_free frees
    # unallocated ids, lib/idr.c:594 WARN, bracketed by the module's own
    # "vvv Ignore ... warnings" markers), so the kmsg scan is off; failures
    # surface in the counts.
    "test_ida": {
        "label": "IDA allocator (test_ida)",
        "loaded_on_pass": False,
        "kind": "test",
        "summary_re": r"IDA: (?P<passed>\d+) of (?P<total>\d+) tests passed",
        "unload": True,
        "scan_kmsg": False,
    },
    # lib/rbtree_test.c: returns -EAGAIN unconditionally ("Fail will directly
    # unload the module"); failures are WARN_ON_ONCE only, no counts printed;
    # :249 printk KERN_ALERT "rbtree testing" opens the run (and :350
    # "augmented rbtree testing" matches too).
    "rbtree_test": {
        "label": "Red-black tree (rbtree_test)",
        "loaded_on_pass": False,
        "kind": "test",
        "summary_re": None,
        "sentinel_re": r"rbtree testing",
        "unload": False,
    },
    # lib/interval_tree_test.c:356: returns -EAGAIN (bad params return
    # -EINVAL, still an error exit); failures are WARN_ON_ONCE only; :71
    # printk KERN_ALERT "interval tree insert/remove" opens the run.
    "interval_tree_test": {
        "label": "Interval tree (interval_tree_test)",
        "loaded_on_pass": False,
        "kind": "test",
        "summary_re": None,
        "sentinel_re": r"interval tree insert/remove",
        "unload": False,
    },
    # lib/percpu_test.c:135-136: pr_info "percpu test done" then -EAGAIN;
    # failures are WARN via the CHECK macro, no counts; the "percpu test
    # done" line closes the run (:32 "percpu test start" opens it).
    "percpu_test": {
        "label": "Per-CPU operations (percpu_test)",
        "loaded_on_pass": False,
        "kind": "test",
        "summary_re": None,
        "sentinel_re": r"percpu test done",
        "unload": False,
    },
    # lib/atomic64_test.c:248-268 (pr_fmt "atomic64_test: "): BUG_ON on any
    # failure (crash-on-fail: the verdict is a clean load with the guest
    # alive), returns 0 and stays loaded on success; :254 prints "passed for
    # %s platform ..." on x86 and :265 "passed" elsewhere, so the sentinel
    # matches both.
    "atomic64_test": {
        "label": "atomic64_t self-test (atomic64_test)",
        "loaded_on_pass": True,
        "kind": "test",
        "summary_re": None,
        "sentinel_re": r"atomic64_test: passed",
        "unload": True,
    },
    # lib/test_vmalloc.c:691-695: returns -EAGAIN when built =m; per-worker
    # pr_info "Summary: <test> passed: %d failed: %d xfailed: ..." lines
    # (:674-675) are the only failure channel, so any nonzero failed count
    # fails the item and any Summary line proves the run happened.
    "test_vmalloc": {
        "label": "vmalloc stress (test_vmalloc)",
        "loaded_on_pass": False,
        "kind": "stress",
        "summary_re": None,
        "sentinel_re": r"Summary: \S+ passed: \d+ failed: \d+",
        "unload": False,
        "fail_re": r"Summary: \S+ passed: \d+ failed: [1-9]",
    },
    # lib/test_workqueue.c: a workqueue stress/performance benchmark; :243
    # pr_info 'test_workqueue:   %-16s %llu items/sec\tp50=...' per completed
    # bench (the sentinel), then "Return -EAGAIN so the module doesn't stay
    # loaded".
    "test_workqueue": {
        "label": "Workqueue stress (test_workqueue)",
        "loaded_on_pass": False,
        "kind": "stress",
        "summary_re": None,
        "sentinel_re": r"test_workqueue:\s+\S+\s+\d+ items/sec",
        "unload": False,
    },
    # lib/find_bit_benchmark.c: prints timings via pr_err (:83
    # "find_next_bit:      %18llu ns, %6ld iterations", the first line of
    # both phases), then returns -EINVAL unconditionally ("return error just
    # to let user run benchmark again without annoying rmmod"); no counts.
    "find_bit_benchmark": {
        "label": "find_bit() benchmark (find_bit_benchmark)",
        "loaded_on_pass": False,
        "kind": "benchmark",
        "summary_re": None,
        "sentinel_re": r"find_next_bit:\s+\d+ ns,\s+\d+ iterations",
        "unload": False,
    },
}
# Dropped after source verification: test_parman (lib/Kconfig:587 gates the
# PARMAN prompt behind COMPILE_TEST, unreachable in a bootable kernel) and
# test_dhry (lib/dhry_run.c only benchmarks when the `run` param is set, so a
# bare modprobe@ load runs nothing: a vacuous pass).

_DEFAULTS = {
    "summary_re": None,
    "sentinel_re": None,
    "unload": False,
    "scan_kmsg": True,
    "fail_re": None,
}

_MODULE_RE = re.compile(r"^[a-z0-9_]+$")


def catalog_entry(module: str) -> dict:
    """The module's catalog entry with defaults filled; an uncataloged module
    (a hand-typed advanced pick) gets the strictest defaults: a stay-loaded
    test, kmsg scanned, no summary counts and no sentinel."""
    entry = CATALOG.get(
        module, {"label": module, "loaded_on_pass": True, "kind": "test"}
    )
    return {**_DEFAULTS, **entry}


def unit_for(module: str) -> str:
    """The guest unit a run item starts: upstream `modprobe@<module>.service`.

    Module names are `[a-z0-9_]+` (enforced), so the instance needs no systemd
    escaping; a name outside that set is refused rather than escaped, since it
    cannot be a cataloged runtime-test module.
    """
    if not _MODULE_RE.match(module or ""):
        raise ValueError(f"not a runtime-test module name: {module!r}")
    return f"modprobe@{module}.service"


def run_status(per_item: list[dict]) -> str:
    """The run verdict from the per-item collect results, the one rule
    `f/runtime_tests/report` and `f/runtime_tests/judge` share: `passed` only
    when every item passed and there was at least one (a `notrun` item is not
    a pass, and a skip_failures error object from a hard step failure is not
    either); aggregating nothing must never read as a pass."""
    ok = bool(per_item) and all(
        isinstance(s, dict) and s.get("status") == "passed" for s in per_item
    )
    return "passed" if ok else "failed"


def _workers() -> Path:
    return Path(os.environ["WORKERS_DIR"])


def cache_dir(vm_name: str, workers: Path | None = None) -> Path:
    """Host-side per-VM cache dir, name-escape hardened.

    `$WORKERS_DIR/shared/runtime-tests/<vm_name>`: a plain host directory (the
    guest needs no share; results travel over the SSH transport), holding only
    the `modules.json` picker cache and the per-kernel `report.json`. `vm_name`
    is resolved and checked to sit directly under the root, so a crafted name
    (`../x`) can never write outside it.
    """
    root = (workers or _workers()) / "shared/runtime-tests"
    path = (root / vm_name).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"vm_name {vm_name!r} resolves outside {root}")
    return path


def modules_cache(vm_name: str, workers: Path | None = None) -> Path:
    """Per-VM cache of the booted kernel's present runtime-test modules.

    `f/runtime_tests/discover` writes it; the run form's `list_modules` picker
    reads it, since a form dynselect cannot reach the guest over vsock.
    """
    return cache_dir(vm_name, workers) / "modules.json"


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


def list_modules(vm_name: str = "", filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_modules` entrypoint: the runnable test modules, named.

    Reads the per-VM cache `f/runtime_tests/discover` writes from the booted
    kernel's `modules.dep` (a form dynselect cannot reach the guest over
    vsock), human-labeled from the curated catalog and featured in catalog
    order (tests first, stress, benchmarks last). Falls back to the catalog
    before the first discovery, so it is never an empty box.
    """
    cached: list[str] = []
    vm = (vm_name or "").strip()
    if vm:
        try:
            data = json.loads(modules_cache(vm).read_text())
            cached = [m for m in data if isinstance(m, str) and m]
        except Exception:
            cached = []
    names = cached or list(CATALOG)
    ordered = [m for m in CATALOG if m in names] + [
        m for m in names if m not in CATALOG
    ]
    needle = (filterText or "").lower()
    return [
        {"value": m, "label": catalog_entry(m)["label"]}
        for m in ordered
        if needle in m.lower() or needle in catalog_entry(m)["label"].lower()
    ]


def main():
    """Library module imported by the f/runtime_tests/* steps; not a runnable step."""
    return "f/runtime_tests/common: module catalog, verdict rules + module listing"
