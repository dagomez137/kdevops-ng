# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the bisect machinery's pure parts (f.kernel.bisect_*)."""

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from f.kernel import bisect_judge, bisect_step, check_usertests

ENV = ("STORE_INDEX_DIR", "SYSTEM_DIR", "WORKBENCH_DIR", "WORKERS_DIR", "MIRRORS_DIR")


def _clear_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def _report(path, data, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    os.utime(path, (mtime, mtime))


def test_state_dir_resolves_under_the_bisect_root(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    assert bisect_step._state_dir("demo") == (tmp_path / "bisect" / "demo").resolve()


def test_state_dir_rejects_a_path_escape(monkeypatch, tmp_path):
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="resolves outside"):
        bisect_step._state_dir("../evil")


def test_fresh_verdict_without_a_report_is_none(tmp_path):
    assert bisect_step._fresh_verdict([], 0.0) is None
    assert bisect_step._fresh_verdict([tmp_path / "missing.json"], 0.0) is None


def test_fresh_verdict_ignores_a_stale_report(tmp_path):
    path = tmp_path / "report.json"
    _report(path, {"status": "passed"}, 100.0)
    assert bisect_step._fresh_verdict([path], 100.0) is None
    assert bisect_step._fresh_verdict([path], 99.0) == "good"


def test_fresh_verdict_maps_the_report_contract(tmp_path):
    path = tmp_path / "report.json"
    _report(path, {"status": "passed"}, 100.0)
    assert bisect_step._fresh_verdict([path], 0.0) == "good"
    _report(path, {"status": "untestable"}, 100.0)
    assert bisect_step._fresh_verdict([path], 0.0) == "skip"
    _report(path, {"status": "failed"}, 100.0)
    assert bisect_step._fresh_verdict([path], 0.0) == "bad"


def test_fresh_verdict_unparseable_report_is_none(tmp_path):
    path = tmp_path / "report.json"
    _report(path, "not json", 100.0)
    assert bisect_step._fresh_verdict([path], 0.0) is None


def test_fresh_verdict_prefers_the_newest_report(tmp_path):
    old = tmp_path / "a" / "report.json"
    new = tmp_path / "b" / "report.json"
    _report(old, {"status": "failed"}, 100.0)
    _report(new, {"status": "passed"}, 200.0)
    assert bisect_step._fresh_verdict([old, new], 0.0) == "good"


def test_fresh_verdict_runtime_threshold_flips_a_pass_to_bad(tmp_path):
    path = tmp_path / "report.json"
    passed = {"status": "passed", "items": [{"runtime": 5}, {"runtime": 6}]}
    _report(path, passed, 100.0)
    assert bisect_step._fresh_verdict([path], 0.0, max_runtime=10.0) == "bad"
    assert bisect_step._fresh_verdict([path], 0.0, max_runtime=11.0) == "good"
    _report(path, {"status": "passed", "items": [{"runtime": None}, {}]}, 100.0)
    assert bisect_step._fresh_verdict([path], 0.0, max_runtime=1.0) == "good"


def test_report_candidates_usertests_build_reads_the_state_dir(tmp_path):
    sdir = tmp_path / "state"
    out = bisect_step._report_candidates("usertests_build", "vm", sdir)
    assert out == [sdir / "report.json"]


def test_report_candidates_selftests_sweeps_the_share(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    root = (tmp_path / "shared/selftests").resolve() / "vm"
    (root / "run1").mkdir(parents=True)
    (root / "run1" / "report.json").write_text("{}")
    out = bisect_step._report_candidates("selftests", "vm", tmp_path / "state")
    assert out == [root / "report.json", root / "run1" / "report.json"]


def test_report_candidates_kunit_sweeps_the_kunit_share(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    root = (tmp_path / "shared/kunit").resolve() / "vm"
    out = bisect_step._report_candidates("kunit", "vm", tmp_path / "state")
    assert out == [root / "report.json"]


def test_bisect_step_requires_a_suite():
    with pytest.raises(ValueError, match="at least one suite"):
        bisect_step.main("vm", "v7.0", "v7.1", suites=[])


def test_bisect_step_rejects_an_unknown_payload():
    with pytest.raises(ValueError, match="unknown payload"):
        bisect_step.main("vm", "v7.0", "v7.1", suites=["s"], payload="bogus")


def test_bisect_step_rejects_an_unknown_usertests_harness():
    with pytest.raises(ValueError, match="unknown usertests harness"):
        bisect_step.main(
            "vm", "v7.0", "v7.1", suites=["nope"], payload="usertests_build"
        )


def _state(tmp_path, monkeypatch, data):
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    sdir = tmp_path / "bisect" / "vm"
    sdir.mkdir(parents=True)
    (sdir / "state.json").write_text(json.dumps(data))


@pytest.mark.parametrize(
    "outcome",
    ["first_bad_found", "not_reproducible_standalone", "good_endpoint_failed"],
)
def test_judge_accepts_each_conclusive_outcome(monkeypatch, tmp_path, outcome):
    _state(tmp_path, monkeypatch, {"outcome": outcome, "first_bad": "abc123"})
    assert bisect_judge.main("vm") == {"outcome": outcome, "first_bad": "abc123"}


@pytest.mark.parametrize(
    "outcome", ["", "max_steps_exceeded", "endpoint_untestable", "inconclusive"]
)
def test_judge_raises_on_an_inconclusive_run(monkeypatch, tmp_path, outcome):
    _state(tmp_path, monkeypatch, {"outcome": outcome, "steps": 3})
    with pytest.raises(RuntimeError, match="did not conclude"):
        bisect_judge.main("vm")


def test_check_usertests_rejects_an_unknown_harness():
    with pytest.raises(ValueError, match="unknown usertests harness"):
        check_usertests.main("vm", "deadbeef", harnesses=["nope"])


def test_check_usertests_requires_a_candidate():
    with pytest.raises(ValueError, match="candidate must name a commit"):
        check_usertests.main("vm", "", harnesses=["vma"])


def test_check_usertests_requires_the_state_clone(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="run bisect_step first"):
        check_usertests.main("vm", "deadbeef", harnesses=["vma"])


class _Git:
    def __init__(self, *args, **kwargs):
        pass

    def run(self, *args, **kwargs):
        return 0


def _run_check(monkeypatch, tmp_path, failures, error_re="", harnesses=None):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path / "system"))
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path / "workers"))
    (tmp_path / "system/bisect/vm/repo").mkdir(parents=True)

    class _Shell:
        def __init__(self, *args, **kwargs):
            pass

        def capture(self, *args, **kwargs):
            directory = next(a for a in args if str(a).startswith("--directory="))
            name = Path(directory.split("=", 1)[1]).name
            if name in failures:
                raise subprocess.CalledProcessError(
                    2, list(args), output=failures[name]
                )
            return "ok\n"

    monkeypatch.setattr(check_usertests, "Git", _Git)
    monkeypatch.setattr(check_usertests, "DevShell", _Shell)
    return check_usertests.main(
        "vm", "deadbeef", harnesses=harnesses, error_re=error_re
    )


def test_check_usertests_all_green_passes(monkeypatch, tmp_path):
    out = _run_check(monkeypatch, tmp_path, {}, harnesses=["vma", "memblock"])
    assert out == {
        "status": "passed",
        "candidate": "deadbeef",
        "harnesses": ["vma", "memblock"],
        "failed": [],
        "matched": None,
    }
    written = json.loads((tmp_path / "system/bisect/vm/report.json").read_text())
    assert written == out


def test_check_usertests_default_harnesses_are_the_catalog(monkeypatch, tmp_path):
    from f.kernel.build_usertests import CATALOG

    out = _run_check(monkeypatch, tmp_path, {})
    assert out["harnesses"] == list(CATALOG)


def test_check_usertests_failure_without_error_re_fails(monkeypatch, tmp_path):
    out = _run_check(
        monkeypatch, tmp_path, {"vma": "vma.c: error: boom"}, harnesses=["vma"]
    )
    assert (out["status"], out["failed"], out["matched"]) == ("failed", ["vma"], None)


def test_check_usertests_matching_signature_fails(monkeypatch, tmp_path):
    out = _run_check(
        monkeypatch,
        tmp_path,
        {"vma": "error: implicit declaration of function 'kmalloc_objs'"},
        error_re="implicit declaration of function 'kmalloc_objs'",
        harnesses=["vma", "memblock"],
    )
    assert (out["status"], out["failed"], out["matched"]) == ("failed", ["vma"], True)


def test_check_usertests_failure_without_the_signature_passes(monkeypatch, tmp_path):
    out = _run_check(
        monkeypatch,
        tmp_path,
        {"vma": "some unrelated older breakage"},
        error_re="implicit declaration of function 'kmalloc_objs'",
        harnesses=["vma", "memblock"],
    )
    assert (out["status"], out["failed"], out["matched"]) == ("passed", ["vma"], False)


REPO = Path(__file__).resolve().parent.parent

PASTED_GUEST = {
    "kernel": {
        "mode": "build",
        "build": {
            "ccache": True,
            "compiler": "gcc",
            "make_flags": "",
            "reproducible": True,
            "ccache_max_size": 10,
            "targets": "",
            "timestamp_from_commit": False,
        },
        "reuse": {"reuse": True, "use_peers": False},
        "devtools": {"compile_commands": True, "rust_analyzer": False},
        "worktree": {
            "ref": "modules-next",
            "b4_series": "20260922-module-string-v1-0-c18766690185@suse.com",
            "custom_ref": False,
            "wipe_build": False,
            "custom_label": False,
            "recreate_build_worktree": False,
        },
        "selftests": {
            "enable": True,
            "targets": ["firmware", "kmod", "module"],
            "extra_targets": "",
        },
        "usertests": {"enable": True, "harnesses": ["radix-tree", "vma", "memblock"]},
        "installation": {"install": True, "modules_install": True},
        "configuration": {"preset": "imageless_defconfig", "config_method": "preset"},
    },
    "closure": {
        "lock": {"update_lock": True},
        "mode": "build",
        "guest": {
            "home": True,
            "shares": {},
            "vm_name": "modules",
            "home_dir": "",
            "ssh_keys": [],
            "user_name": "kdevops",
        },
        "closure": {
            "profiles": ["devel", "build-tools", "monitoring"],
            "test_suites": ["blktests", "fstests", "kunit", "usertests"],
            "telemetry_ebpf": False,
        },
    },
    "qemu": {"mode": "reuse", "qemu_pick": "qemu-11.1.1-vanilla-24c216fd7853"},
    "worktree": {
        "custom_group": True,
        "worktree_group": "modules",
        "deploy_developer_worktree": True,
        "recreate_developer_worktree": False,
    },
    "boot_qemu": {
        "cpu": "host",
        "ram": 4096,
        "cpus": 4,
        "accel": "kvm",
        "iommu": "",
        "machine_type": "q35",
        "extra_qemu_args": "",
    },
    "boot_kernel": {"kernel_parameters": [], "extra_kernel_parameters": ""},
    "boot_networking": {"vm_index": 0, "custom_ssh_port": False},
    "boot_sharing": {"home_share": True, "controller_share": False},
    "boot_nvme": {
        "customize_drives": False,
        "nvme_drive_count": 5,
        "nvme_drive_size_gb": 20,
    },
    "boot_orchestration": {"wait_timeout": 300, "emit_vars_yaml_snapshot": False},
    "vm": {"vm_name": "modules", "vm_target": "new", "auto_vm_name": False},
}


def _bringup(payload, suites, guest=None, **kwargs):
    return bisect_step.bringup_args(
        "abc123", "bisect", payload, suites, guest=guest, **kwargs
    )


def _flow(path):
    return yaml.safe_load((REPO / path / "flow.yaml").read_text())


def test_bringup_builtin_selftests_targets_are_the_suites():
    args = _bringup("selftests", ["kmod", "firmware"])
    kernel = args["kernel"]
    assert kernel["selftests"] == {
        "enable": True,
        "targets": ["kmod", "firmware"],
        "extra_targets": "",
    }
    assert kernel["usertests"] == {"enable": False, "harnesses": []}
    assert args["closure"]["closure"]["test_suites"] == ["selftests"]
    assert kernel["worktree"]["git_ref"] == "abc123"
    assert args["vm"]["vm_name"] == args["closure"]["guest"]["vm_name"] == "bisect"
    assert args["qemu"] == {"mode": "nixpkgs"}
    assert args["boot_nvme"]["nvme_drive_count"] == 4


def test_bringup_builtin_usertests_harnesses_are_the_suite_dirs():
    args = _bringup("usertests", ["radix-tree/xarray", "radix-tree/maple", "vma"])
    assert args["kernel"]["usertests"] == {
        "enable": True,
        "harnesses": ["radix-tree", "vma"],
    }
    assert args["kernel"]["selftests"]["enable"] is False
    assert args["closure"]["closure"]["test_suites"] == ["usertests"]


@pytest.mark.parametrize(
    ("payload", "suites"),
    [("kunit", ["kunit"]), ("runtime_tests", []), ("usertests_build", ["kunit"])],
)
def test_bringup_builtin_closure_test_suites(payload, suites):
    args = _bringup(payload, ["x"])
    assert args["closure"]["closure"]["test_suites"] == suites


def test_bringup_builtin_takes_compiler_and_make_flags():
    build = _bringup("kunit", ["s"], compiler="clang", make_flags="W=1")["kernel"][
        "build"
    ]
    assert build == {
        "ccache": True,
        "compiler": "clang",
        "make_flags": "W=1",
        "reproducible": True,
    }


def test_bringup_override_keeps_the_pasted_guest_shape():
    args = _bringup("selftests", ["kmod"], guest=PASTED_GUEST)
    for key in ("qemu", "boot_qemu", "boot_nvme", "boot_kernel"):
        assert args[key] == PASTED_GUEST[key]
    assert args["closure"]["closure"]["profiles"] == [
        "devel",
        "build-tools",
        "monitoring",
    ]
    assert args["closure"]["lock"] == PASTED_GUEST["closure"]["lock"]
    assert args["kernel"]["devtools"] == PASTED_GUEST["kernel"]["devtools"]


def test_bringup_override_forces_what_the_bisect_owns():
    args = _bringup(
        "selftests", ["kmod"], guest=PASTED_GUEST, compiler="clang", make_flags="W=1"
    )
    assert args["vm"] == {
        "vm_target": "new",
        "auto_vm_name": False,
        "vm_name": "bisect",
    }
    assert args["closure"]["guest"]["vm_name"] == "bisect"
    kernel = args["kernel"]
    assert kernel["worktree"] == {
        "custom_ref": True,
        "git_ref": "abc123",
        "b4_series": "",
        "custom_label": False,
        "wipe_build": False,
        "recreate_build_worktree": False,
    }
    assert kernel["build"]["compiler"] == "clang"
    assert kernel["build"]["make_flags"] == "W=1"
    assert kernel["build"]["ccache_max_size"] == 10
    assert kernel["reuse"] == {"reuse": True, "use_peers": True}


def test_bringup_override_does_not_mutate_the_input():
    pasted = copy.deepcopy(PASTED_GUEST)
    _bringup("selftests", ["kmod"], guest=pasted)
    assert pasted == PASTED_GUEST


def test_bringup_override_unions_selftests_targets_and_closure_suite():
    args = _bringup("selftests", ["kmod", "lib"], guest=PASTED_GUEST)
    selftests = args["kernel"]["selftests"]
    assert selftests["targets"] == ["firmware", "kmod", "module", "lib"]
    assert selftests["enable"] is True
    assert args["closure"]["closure"]["test_suites"] == [
        "blktests",
        "fstests",
        "kunit",
        "usertests",
        "selftests",
    ]


def test_bringup_override_unions_usertests_harness_dirs():
    args = _bringup("usertests", ["radix-tree/maple", "maple"], guest=PASTED_GUEST)
    assert args["kernel"]["usertests"]["harnesses"] == [
        "radix-tree",
        "vma",
        "memblock",
        "maple",
    ]
    assert args["closure"]["closure"]["test_suites"].count("usertests") == 1


def test_bringup_override_creates_a_missing_closure_guest():
    guest = {"kernel": {}, "closure": {}}
    args = _bringup("runtime_tests", ["test_xarray"], guest=guest)
    assert args["closure"]["guest"] == {"vm_name": "bisect"}
    assert args["closure"]["closure"]["test_suites"] == []
    assert args["kernel"]["worktree"]["git_ref"] == "abc123"


@pytest.mark.parametrize("guest", [{"kernel": {}}, ["kernel", "closure"], "x"])
def test_bringup_override_rejects_a_non_bringup_guest(guest):
    with pytest.raises(ValueError, match="kernel` and `closure"):
        _bringup("kunit", ["s"], guest=guest)


def test_bisect_step_rejects_an_invalid_guest_before_touching_state():
    with pytest.raises(ValueError, match="kernel` and `closure"):
        bisect_step.main("vm", "v7.0", "v7.1", suites=["s"], guest={"qemu": {}})


def test_bisect_flow_passes_every_bringup_input_from_the_step():
    flow = _flow("f/kernel/bisect.flow")
    loop = flow["value"]["modules"][0]["value"]["modules"]
    bringup = next(m for m in loop if m["id"] == "bringup")["value"]
    transforms = bringup["input_transforms"]
    accepted = set(_flow("f/qsu/bringup.flow")["schema"]["properties"])
    assert set(transforms) == accepted
    for key, transform in transforms.items():
        assert transform["expr"].startswith(f"results.step.bringup.{key}")
    assert set(_bringup("kunit", ["s"])) <= accepted
    assert set(_bringup("kunit", ["s"], guest=PASTED_GUEST)) <= accepted


def test_bringup_args_override_drops_the_developer_worktree():
    args = bisect_step.bringup_args(
        "abc123", "bisect", "selftests", ["kmod"], guest=PASTED_GUEST
    )
    assert "worktree" not in args
    assert PASTED_GUEST["worktree"]["worktree_group"] == "modules"
