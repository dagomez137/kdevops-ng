# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Shared library for the f/selftests/* steps (host side of a kselftest run);
# imported as f.selftests.common. Parses the flat KTAP run_kselftest.sh emits to
# its unit's journal, escapes collection names into systemd unit instances, and
# holds the curated collection catalog plus the per-VM collection cache the run
# form's picker reads.
#
# The contract with the guest side (the kselftest@.service templates):
#   * guest mount: /var/lib/kselftests, share tag `selftests`;
#   * <share>/kselftest.env       = systemd EnvironmentFile (KSELFTEST_ARGS=...);
#   * <share>/<kver>/tree/        = the writable kselftest install tree the units
#                                   execute (unit WorkingDirectory=.../%v/tree);
#   * <share>/<kver>/report.json  = the flow's rollup;
#   * <share>/collections.json    = the picker cache f/selftests/discover writes.
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from f.common.remote import list_vms as list_vms

# The guest mount point of the `selftests` share (the units' state dir).
GUEST_STATE_DIR = "/var/lib/kselftests"
GUEST_TAG = "selftests"

# Curated kselftest collections as named, human-labeled choices, featured first
# in the picker. The built tree's kselftest-list.txt is the ground truth; this
# supplies the labels and the fallback when no discovery has run, so the form
# offers named choices, never an empty box. The first block is the curated
# kernel-build default set; the second names well-known collections outside it,
# each labeled with its environment caveat. Extend freely.
CURATED_COLLECTIONS = {
    "size": "size (minimal memory-usage report)",
    "breakpoints": "breakpoints (ptrace hardware breakpoints)",
    "kcmp": "kcmp (kcmp() syscall)",
    "mincore": "mincore (mincore() syscall)",
    "splice": "splice (splice() syscall)",
    "sync": "sync (sw_sync fence framework)",
    "clone3": "clone3 (clone3() syscall)",
    "fchmodat2": "fchmodat2 (fchmodat2() syscall)",
    "mount_setattr": "mount_setattr (mount_setattr() syscall)",
    "memfd": "memfd (memfd_create() sealing)",
    "mqueue": "mqueue (POSIX message queues)",
    "syscall_user_dispatch": "syscall_user_dispatch (SUD)",
    "ptrace": "ptrace (ptrace() interfaces)",
    "seccomp": "seccomp (seccomp filters)",
    "cgroup": "cgroup (control groups)",
    "futex": "futex (futex() syscall)",
    "rlimits": "rlimits (resource limits)",
    "capabilities": "capabilities (POSIX capabilities)",
    "exec": "exec (execve() edge cases)",
    "proc": "proc (procfs interfaces)",
    "sysctl": "sysctl (sysctl interfaces)",
    "lib": "lib (in-kernel library test modules)",
    "kmod": "kmod (module loader stress via test_kmod)",
    "module": "module (kallsyms find_symbol via test_kallsyms)",
    "timers": "timers (POSIX timers and clocks)",
    "kselftest_harness": "kselftest_harness (harness self-checks)",
    "pidfd": "pidfd (pidfd_* syscalls; hang-prone, outside the curated build)",
    "net": "net (networking; heavy deps, outside the curated build)",
    "net/forwarding": "net/forwarding (switch topologies; needs veth setup)",
    "mm": "mm (memory management; needs hugepages configured)",
    "ftrace": "ftrace (ftracetest; long-running)",
    "kvm": "kvm (needs nested virtualization)",
    "livepatch": "livepatch (needs CONFIG_LIVEPATCH + test modules)",
    "user_events": "user_events (needs CONFIG_USER_EVENTS)",
}

# run_kselftest.sh emits one flat KTAP document per invocation: a column-0
# `TAP version 13` header, the up-front `1..N` plan, then exactly one column-0
# `ok N`/`not ok N` result line per test whose description is
# `selftests: <collection>: <test>`; a test's own output rides between them,
# every line prefixed `# ` (not spec-nested subtests; ignored here).
_HEADER_RE = re.compile(r"^TAP version 13\s*$")
_PLAN_RE = re.compile(r"^1\.\.(?P<count>[0-9]+)\s*$")
_RESULT_RE = re.compile(
    r"^(?P<status>ok|not ok) (?P<num>[0-9]+) "
    r"(?P<desc>[^#]*?)\s*(?:# (?P<directive>.*))?$"
)
_DESC_RE = re.compile(r"^selftests: (?P<collection>.+?): (?P<test>.+)$")
# The runner's closing per-category totals diagnostic; the last match wins.
_TOTALS_RE = re.compile(
    r"^# Totals: pass:(?P<passed>[0-9]+) fail:(?P<failed>[0-9]+) "
    r"xfail:(?P<xfail>[0-9]+) xpass:(?P<xpass>[0-9]+) "
    r"skip:(?P<skipped>[0-9]+) error:(?P<error>[0-9]+)\s*$"
)
_SKIP_RE = re.compile(r"^\s*SKIP\b", re.IGNORECASE)
_XFAIL_RE = re.compile(r"^\s*XFAIL\b", re.IGNORECASE)


def _empty_summary() -> dict:
    return {
        "tests": [],
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "plan": None,
        "complete": False,
        "report_present": False,
    }


def parse_ktap(text: str) -> dict:
    """Parse run_kselftest.sh's flat KTAP output into a per-test result summary.

    The runner emits one flat document per invocation: a column-0 `TAP version 13`
    header, the up-front `1..N` plan, then exactly one column-0 result line per
    test, description `selftests: <collection>: <test>`; each test's own output
    rides between them with every line prefixed `# ` (not spec-nested subtests),
    so only column-0 plan and result lines are read. Parsing anchors on the LAST
    header in the text (should the input ever carry more than one run's output,
    only the newest counts; journal junk around the document is tolerated).

    Directives, per the runner's vocabulary: `# SKIP` counts the test as skipped
    (status `notrun`); `# XFAIL` counts it as passed (upstream calls an expected
    failure a pass) with the directive kept as the message; a failing directive
    (`# TIMEOUT <secs> seconds`, `# exit=<rc>`) becomes the failure message.

    The `1..N` plan is captured and checked: `complete` is True only when a plan
    was found, exactly N result lines followed, and the runner's own closing
    `# Totals: pass:P fail:F xfail:X xpass:N skip:S error:N` diagnostic, when
    present, agrees with the parsed counts (passed against pass+xfail+xpass:
    every non-SKIP `ok` counts as passed here; failed against fail+error), so a
    journal truncated mid-run can never read as a full pass. Returns
    `{tests, passed, failed, skipped, plan, complete, report_present}` where
    each `tests` entry is `{collection, test, status, message}` with status in
    `passed`/`failed`/`notrun`; `report_present` is False when no header is
    found (a crash or a partial journal).
    """
    summary = _empty_summary()
    lines = (text or "").splitlines()
    header = next(
        (i for i in range(len(lines) - 1, -1, -1) if _HEADER_RE.match(lines[i])),
        None,
    )
    if header is None:
        return summary
    summary["report_present"] = True

    totals = None
    for ln in lines[header + 1 :]:
        p = _PLAN_RE.match(ln)
        if p and summary["plan"] is None:
            summary["plan"] = int(p.group("count"))
            continue
        t = _TOTALS_RE.match(ln)
        if t:
            totals = t
            continue
        m = _RESULT_RE.match(ln)
        if not m:
            continue
        directive = m.group("directive") or ""
        message = directive.strip()
        if _SKIP_RE.match(directive):
            status = "notrun"
            summary["skipped"] += 1
        elif m.group("status") == "ok":
            # A non-SKIP ok passes; an XFAIL keeps its directive as the note.
            status = "passed"
            summary["passed"] += 1
            if not _XFAIL_RE.match(directive):
                message = ""
        else:
            status = "failed"
            summary["failed"] += 1
        d = _DESC_RE.match(m.group("desc").strip())
        summary["tests"].append(
            {
                "collection": d.group("collection") if d else "",
                "test": d.group("test") if d else m.group("desc").strip(),
                "status": status,
                "message": message,
            }
        )
    totals_ok = totals is None or (
        int(totals["passed"]) + int(totals["xfail"]) + int(totals["xpass"])
        == summary["passed"]
        and int(totals["failed"]) + int(totals["error"]) == summary["failed"]
        and int(totals["skipped"]) == summary["skipped"]
    )
    summary["complete"] = (
        summary["plan"] is not None
        and len(summary["tests"]) == summary["plan"]
        and totals_ok
    )
    return summary


def run_status(per_item: list[dict]) -> str:
    """The run verdict from the per-item collect results, the one rule
    `f/selftests/report` and `f/selftests/judge` share: `passed` only when every
    item passed and there was at least one (a `notrun` item is not a pass, and a
    skip_failures error object from a hard step failure is not either);
    aggregating nothing must never read as a pass."""
    ok = bool(per_item) and all(
        isinstance(s, dict) and s.get("status") == "passed" for s in per_item
    )
    return "passed" if ok else "failed"


def unit_escape(name: str) -> str:
    """A collection or COLLECTION:TEST name as a systemd unit instance string.

    Implements systemd's `unit_name_escape` (src/basic/unit-name.c, do_escape):
    `/` becomes `-`; ASCII alphanumerics, `:`, `_` and `.` stay; everything else
    (including `-` and `\\`) becomes `\\x<2-hex-lowercase>` of the byte, and a
    leading `.` is escaped too. The guest unit's `%I` specifier unescapes it
    back, so `kselftest@net-forwarding.service` runs `net/forwarding` and
    `kselftest@cpu\\x2dhotplug.service` runs `cpu-hotplug`.
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


# The two guest templates a run item maps onto: a bare collection runs as
# `kselftest@` (run_kselftest.sh --collection), a COLLECTION:TEST list entry as
# `kselftest-test@` (run_kselftest.sh --test).
UNIT_TEMPLATES = ("kselftest", "kselftest-test")


def item_unit(item: str) -> str:
    """The guest unit a run item starts: `kselftest-test@<esc>.service` for a
    COLLECTION:TEST entry (it carries a `:`), else `kselftest@<esc>.service` for
    a whole collection; the instance is the systemd-escaped item name."""
    template = "kselftest-test" if ":" in item else "kselftest"
    return f"{template}@{unit_escape(item)}.service"


def _workers() -> Path:
    return Path(os.environ["WORKERS_DIR"])


def share_dir(vm_name: str, workers: Path | None = None) -> Path:
    """Host path of the VM's `selftests` virtiofs share, name-escape hardened.

    `$WORKERS_DIR/shared/selftests/<vm_name>`, mounted in the guest at
    `/var/lib/kselftests`. Lives under `shared/` so every worker sees the same
    bytes the guest's virtiofsd serves; `vm_name` is resolved and checked to sit
    directly under the share root, so a crafted name (`../x`) can never write
    outside it.
    """
    root = (workers or _workers()) / "shared/selftests"
    path = (root / vm_name).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"vm_name {vm_name!r} resolves outside {root}")
    return path


def collections_cache(vm_name: str, workers: Path | None = None) -> Path:
    """Per-VM cache of the guest kernel's kselftest collection names.

    `f/selftests/discover` writes the built tree's collections here; the run
    form's `list_collections` picker reads them, since a form dynselect cannot
    reach the guest over vsock.
    """
    return share_dir(vm_name, workers) / "collections.json"


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


def list_collections(
    vm_name: str = "", filterText: str = "", **_: object
) -> list[dict]:
    """`dynmultiselect-list_collections` entrypoint: the kernel's collections, named.

    Reads the per-VM cache `f/selftests/discover` writes from the built tree's
    `kselftest-list.txt` (a form dynselect cannot reach the guest over vsock),
    human-labeled from the curated catalog and featured first. Falls back to the
    curated catalog before the first discovery, so it is never an empty box.
    """
    cached: list[str] = []
    vm = (vm_name or "").strip()
    if vm:
        try:
            data = json.loads(collections_cache(vm).read_text())
            cached = [c for c in data if isinstance(c, str) and c]
        except Exception:
            cached = []
    names = cached or list(CURATED_COLLECTIONS)
    ordered = [c for c in CURATED_COLLECTIONS if c in names] + [
        c for c in names if c not in CURATED_COLLECTIONS
    ]
    needle = (filterText or "").lower()
    return [
        {"value": c, "label": CURATED_COLLECTIONS.get(c, c)}
        for c in ordered
        if needle in c.lower() or needle in CURATED_COLLECTIONS.get(c, c).lower()
    ]


def main():
    """Library module imported by the f/selftests/* steps; not a runnable step."""
    return "f/selftests/common: KTAP parser + kselftest collection listing"
