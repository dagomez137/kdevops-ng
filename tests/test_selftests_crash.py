# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the kselftest userspace crash verdict and repeat folding."""

import json
import re

import pytest

from f.selftests import collect, report
from f.selftests.common import USER_CRASH_PATTERN

CRASH_RE = re.compile(USER_CRASH_PATTERN)

SEGFAULT = (
    "modprobe[18867]: segfault at 6f8 ip 00007b74962f2d9d sp 00007ffdb125ba68 "
    "error 4 in ld-linux-x86-64.so.2[12d9d,7b74962e1000+2b000] likely on CPU 2 "
    "(core 2, socket 0)"
)
GPF = (
    "traps: foo[123] general protection fault ip:7f0e3c2a1b2c sp:7ffd8a3b1c20 "
    "error:0 in libc.so.6[7f0e3c200000+1b5000]"
)
KTAP = """\
TAP version 13
1..1
ok 1 selftests: kmod: kmod.sh
# Totals: pass:1 fail:0 xfail:0 xpass:0 skip:0 error:0
"""


@pytest.mark.parametrize(
    "line",
    [
        SEGFAULT,
        GPF,
        "traps: bar[9] general protection ip:401000 sp:7ffd0 error:0",
        "traps: baz[77] trap invalid opcode ip:401000 sp:7ffd0 error:0",
        "traps: qux[5] trap int3 ip:401000 sp:7ffd0 error:0 in qux[401000+1000]",
        "app[42] control protection ip:401000 sp:7ffd0 ssp:7ffe0 error:3(user)",
        "sh[300]: unhandled exception: DABT (lower EL), ESR 0x92000006, "
        "level 2 translation fault in sh[aaaab1230000+1000]",
        "sh[300]: unhandled level 3 translation fault (11) at 0x00000000, esr 0x92",
    ],
)
def test_crash_pattern_matches_kernel_reports(line):
    assert CRASH_RE.search(line)


@pytest.mark.parametrize(
    "line",
    [
        "misc test_kmod0: Sync thread 35 return status: 139",
        "misc test_kmod0: Test case: 0004 completed",
        "kmod: request_module: modprobe fs-xfs cannot be processed",
        "systemd[1]: Started kselftest@kmod.service.",
        "general protection fault, probably for non-canonical address 0xdead: 0000",
    ],
)
def test_crash_pattern_ignores_chatter(line):
    assert not CRASH_RE.search(line)


def _collect(**kw):
    base = dict(
        vm_name="vm",
        item="kmod",
        kernel_version="7.3.0",
        ktap=KTAP,
        result="done",
        runtime=10.0,
    )
    return collect.main(**{**base, **kw})


def test_collect_records_crashes_without_failing_by_default():
    r = _collect(user_crashes=[SEGFAULT], user_crash_count=1)
    assert r["status"] == "passed"
    assert r["user_crashes"] == 1
    assert r["detail"]["user_crashes"] == [SEGFAULT]
    assert r["detail"]["failures"] == []


def test_collect_fails_on_crash_when_asked():
    r = _collect(
        user_crashes=[SEGFAULT, GPF], user_crash_count=2, fail_on_user_crash=True
    )
    assert r["status"] == "failed"
    (synthetic,) = r["detail"]["failures"]
    assert synthetic["collection"] == "kmod"
    assert synthetic["test"] == "userspace crash"
    assert synthetic["message"].startswith(SEGFAULT)
    assert "(2 crash line(s))" in synthetic["message"]


def test_collect_flag_without_crashes_passes():
    clean = _collect(user_crashes=[], user_crash_count=0, fail_on_user_crash=True)
    assert clean["status"] == "passed"
    unknown = _collect(fail_on_user_crash=True)
    assert unknown["status"] == "passed"
    assert unknown["user_crashes"] is None


def test_report_folds_repeats(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "share_dir", lambda vm: tmp_path / vm)
    runs = [
        _collect(runtime=10.0, user_crash_count=0),
        _collect(
            runtime=30.0,
            user_crashes=[SEGFAULT],
            user_crash_count=1,
            fail_on_user_crash=True,
        ),
        _collect(runtime=12.0, user_crash_count=0),
    ]
    out = report.main(runs, vm_name="vm")
    run_info, item_rows, _ = out["render_all"]

    (row,) = item_rows
    assert row["status"] == "❌"
    assert row["time(s)"] == 12.0
    assert row["spread(s)"] == 20.0
    assert row["samples"] == 3
    assert row["crashes"] == 1
    assert run_info[0]["time(s)"] == 12.0

    rollup = json.loads((tmp_path / "vm/7.3.0/report.json").read_text())
    assert rollup["status"] == "failed"
    (item,) = rollup["items"]
    assert item["runtime"] == 12.0
    assert [r["runtime"] for r in item["runs"]] == [10.0, 30.0, 12.0]
    assert item["detail"]["user_crashes"] == [SEGFAULT]
    assert [f["test"] for f in rollup["failures"]] == ["userspace crash"]


def test_report_single_run_unfolded_shape():
    out = report.main([_collect()], vm_name="")
    _, item_rows, _ = out["render_all"]
    assert item_rows[0]["samples"] == 1
    assert item_rows[0]["spread(s)"] == 0
    assert item_rows[0]["status"] == "✅"


def _remote_with(out):
    from f.common.remote import RemoteSystemd

    remote = RemoteSystemd.__new__(RemoteSystemd)
    remote.ssh = lambda *args, **kwargs: out
    return remote


def test_journal_kernel_grep_no_match_is_an_empty_read():
    # journalctl --output=json --grep with no match: exit 1, only a cursor line.
    remote = _remote_with("-- cursor: s=abc;i=1\n")
    assert remote.journal_kernel("c0", grep=USER_CRASH_PATTERN) == ("s=abc;i=1", [])


def test_journal_kernel_grep_failed_fetch_raises():
    remote = _remote_with("")
    with pytest.raises(RuntimeError):
        remote.journal_kernel("c0", grep=USER_CRASH_PATTERN)
