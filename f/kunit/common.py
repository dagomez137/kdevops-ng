# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Shared library for the f/kunit/* steps (host side of a KUnit run); imported as
# f.kunit.common. Parses the KTAP a suite emits to its journal, and holds the
# curated suite catalog plus the per-VM suite cache the run form's picker reads.
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from f.common.remote import list_vms as list_vms

# The KUnit debugfs root the guest exposes; each entry is one runnable suite.
DEBUGFS_DIR = "/sys/kernel/debug/kunit"

# Curated KUnit suites as named, human-labeled choices, featured first in the
# picker. The booted guest's /sys/kernel/debug/kunit/ is the ground truth; this
# supplies the labels and the fallback when no guest is reachable, so the form
# offers named choices, never an empty box. Extend freely.
CURATED_SUITES = {
    "rust_rxarray": "rxarray (Rust XArray)",
    "rust_doctests_kernel": "Rust kernel crate doctests",
}

# The kunit.py result-line contract (a TAP/KTAP `ok`/`not ok` line), extended with a
# leading-indent capture so a suite's own per-test lines (at the `# Subtest:` indent)
# are told apart from the column-0 suite verdict and any deeper nested subtests.
_RESULT_RE = re.compile(
    r"^(?P<indent>\s*)(?P<status>ok|not ok) (?P<num>[0-9]+) ?"
    r"(?P<sep>:?- )?(?P<name>[^#]*)(?P<directive> # .*)?$"
)
_SUBTEST_RE = re.compile(r"^(?P<indent>\s*)# Subtest:\s*(?P<name>.+?)\s*$")
_PLAN_RE = re.compile(r"^(?P<indent>\s*)1\.\.(?P<count>[0-9]+)\s*$")
# A `# SKIP` directive (case-insensitive) marks the test skipped regardless of
# the ok/not ok verdict, per the KTAP spec.
_SKIP_RE = re.compile(r"#\s*SKIP\b", re.IGNORECASE)
# KUnit's own stats line (`kunit.stats_enabled`, emitted when more than one
# test ran). The suite-level `# <suite>: pass:...` line counts CASES, matching
# what this parser counts; the `# Totals:` line counts parameter iterations
# (test.c prints param_stats there) and must not be compared against cases.
_STATS_RE = re.compile(
    r"^\s*# (?P<name>[^:]+): pass:(?P<passed>[0-9]+) fail:(?P<failed>[0-9]+) "
    r"skip:(?P<skipped>[0-9]+) total:[0-9]+\s*$"
)


def _empty_summary(suite: str) -> dict:
    return {
        "suite": suite,
        "tests": [],
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "plan": None,
        "complete": False,
        "report_present": False,
    }


def parse_ktap(text: str, suite: str) -> dict:
    """Parse a KUnit suite's KTAP output into a per-test result summary.

    KTAP is the kernel's TAP dialect: a KUnit suite is a nested subtest, an indented
    `# Subtest: <suite>` block with its own `1..N` plan and indented `ok/not ok N
    <test>` result lines, closed by a column-0 `ok 1 <suite>` suite verdict. Parsing
    anchors on the LAST `# Subtest: <suite>` header in the text (should the input
    ever carry more than one run's output, only the newest counts; junk around the
    document, e.g. the trigger's echoed input, is tolerated) and reads the result
    lines at the suite's own indent: a deeper-indented line is a nested
    parameterised subtest (skipped, the suite-level verdict counts it once), a
    less-indented line is the outer verdict (stops the scan). A `# SKIP` directive
    counts the test as skipped (status `notrun`) whatever its ok/not ok verdict.

    The suite's `1..N` plan is captured and checked: `complete` is True only when
    a plan was found, exactly N result lines followed, and the kernel's own
    suite-level stats line (`# <suite>: pass:...`), when emitted, agrees with the
    parsed counts, so a journal truncated mid-suite (all surviving lines `ok`)
    can never read as a full pass. Returns
    `{suite, tests, passed, failed, skipped, plan, complete, report_present}`
    where each `tests` entry is `{name, status, message}` with status in
    `passed`/`failed`/`notrun`; `report_present` is False when no
    `# Subtest: <suite>` block is found (a crash or a partial journal).
    """
    summary = _empty_summary(suite)
    lines = (text or "").splitlines()
    header = next(
        (
            i
            for i in range(len(lines) - 1, -1, -1)
            if (m := _SUBTEST_RE.match(lines[i])) and m.group("name").strip() == suite
        ),
        None,
    )
    if header is None:
        return summary
    summary["report_present"] = True
    sub_indent = len(_SUBTEST_RE.match(lines[header]).group("indent"))

    for ln in lines[header + 1 :]:
        p = _PLAN_RE.match(ln)
        if p and summary["plan"] is None and len(p.group("indent")) == sub_indent:
            summary["plan"] = int(p.group("count"))
            continue
        m = _RESULT_RE.match(ln)
        if not m:
            continue
        indent = len(m.group("indent"))
        if indent < sub_indent:
            break
        if indent > sub_indent:
            continue
        directive = m.group("directive") or ""
        message = directive.lstrip(" #").strip()
        if _SKIP_RE.search(directive):
            status = "notrun"
            summary["skipped"] += 1
        elif m.group("status") == "ok":
            status = "passed"
            summary["passed"] += 1
        else:
            status = "failed"
            summary["failed"] += 1
        summary["tests"].append(
            {"name": m.group("name").strip(), "status": status, "message": message}
        )
    # The kernel's own suite-level stats line, when emitted, must agree with the
    # parsed counts; the last match wins (per-case stats lines carry case names,
    # never the suite's).
    stats = next(
        (
            m
            for ln in reversed(lines[header + 1 :])
            if (m := _STATS_RE.match(ln)) and m.group("name").strip() == suite
        ),
        None,
    )
    stats_ok = stats is None or (
        int(stats["passed"]) == summary["passed"]
        and int(stats["failed"]) == summary["failed"]
        and int(stats["skipped"]) == summary["skipped"]
    )
    summary["complete"] = (
        summary["plan"] is not None
        and len(summary["tests"]) == summary["plan"]
        and stats_ok
    )
    return summary


def run_status(per_suite: list[dict]) -> str:
    """The run verdict from the per-suite collect results, the one rule
    `f/kunit/report` and `f/kunit/judge` share: `passed` only when every suite
    passed and there was at least one (a `notrun` suite is not a pass, and a
    skip_failures error object from a hard step failure is not either);
    aggregating nothing must never read as a pass."""
    ok = bool(per_suite) and all(
        isinstance(s, dict) and s.get("status") == "passed" for s in per_suite
    )
    return "passed" if ok else "failed"


def _workers() -> Path:
    return Path(os.environ["WORKERS_DIR"])


def share_dir(vm_name: str, workers: Path | None = None) -> Path:
    """Host path of the VM's `kunit` report tree, name-escape hardened.

    `$WORKERS_DIR/shared/kunit/<vm_name>`, where `f/kunit/report` keeps its
    `report.json` rollup. Under `shared/` so every worker sees the same bytes;
    `vm_name` is resolved and checked to sit directly under the root, so a crafted
    name (`../x`) can never write outside it.
    """
    root = (workers or _workers()) / "shared/kunit"
    path = (root / vm_name).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"vm_name {vm_name!r} resolves outside {root}")
    return path


def suites_cache(vm_name: str, workers: Path | None = None) -> Path:
    """Per-VM cache of the guest's KUnit suite names.

    `f/kunit/discover` writes the guest's `/sys/kernel/debug/kunit/` entries here;
    the run form's `list_suites` picker reads them, since a form dynselect cannot
    reach the guest over vsock.
    """
    return share_dir(vm_name, workers) / "suites.json"


def _atomic_write(path: Path, data: str, mode: int = 0o644) -> None:
    """Write via a hidden temp file + rename so a concurrent reader on the shared
    dir never sees a half-written `report.json`."""
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


def _cached_names(cache: Path) -> list[str]:
    try:
        data = json.loads(cache.read_text())
    except Exception:
        return []
    return [s for s in data if isinstance(s, str) and s]


def _cached_union() -> list[str]:
    """Union of every VM's cached suite names, sorted."""
    names: set[str] = set()
    try:
        caches = sorted((_workers() / "shared/kunit").glob("*/suites.json"))
    except Exception:
        return []
    for cache in caches:
        names.update(_cached_names(cache))
    return sorted(names)


def list_suites(vm_name: str = "", filterText: str = "", **_: object) -> list[dict]:
    """`dynmultiselect-list_suites` entrypoint: the guest's KUnit suites, named.

    Reads the per-VM cache `f/kunit/discover` writes from the guest's
    `/sys/kernel/debug/kunit/` (a form dynselect cannot reach the guest over
    vsock), human-labeled from the curated catalog and featured first. Before the
    selected guest's first discovery it falls back to the union of every VM's
    cache, then to the curated catalog, so it is never an empty box.
    """
    cached: list[str] = []
    vm = (vm_name or "").strip()
    if vm:
        try:
            cached = _cached_names(suites_cache(vm))
        except Exception:
            cached = []
    names = cached or _cached_union() or list(CURATED_SUITES)
    ordered = [s for s in CURATED_SUITES if s in names] + [
        s for s in names if s not in CURATED_SUITES
    ]
    needle = (filterText or "").lower()
    return [
        {"value": s, "label": CURATED_SUITES.get(s, s)}
        for s in ordered
        if needle in s.lower() or needle in CURATED_SUITES.get(s, s).lower()
    ]


def main():
    """Library module imported by the f/kunit/* steps; not a runnable step."""
    return "f/kunit/common: KTAP parser + KUnit suite listing"
