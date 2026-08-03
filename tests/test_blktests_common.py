# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the blktests TSV parser and shared verdict rules."""

import stat
from pathlib import Path

import pytest

from f.blktests.common import (
    GROUPS,
    _atomic_write,
    build_args,
    catalog_tests,
    collect_group_rows,
    default_groups,
    group_names,
    group_status,
    groups_cache,
    list_devices,
    list_exclude,
    list_groups,
    list_tests,
    parse_seqres,
    render_blktests_config,
    results_dir,
    run_status,
    runtime_seconds,
    share_dir,
)

SEQRES_PASS = (
    "date\t2026-08-02 12:00:01\n"
    "status\tpass\n"
    "runtime\t4.077s\n"
    "description\tremove a device while running blktrace\n"
    "exit_status\t0\n"
)

SEQRES_FAIL = "status\tfail\nreason\toutput\nruntime\t0.512s\nexit_status\t0\n"

SEQRES_NOTRUN = "status\tnot run\nruntime\t0.001s\n"


def test_parse_seqres_reads_the_tsv_keys():
    got = parse_seqres(SEQRES_PASS)
    assert got["status"] == "pass"
    assert got["runtime"] == "4.077s"
    assert got["date"] == "2026-08-02 12:00:01"
    assert got["exit_status"] == "0"
    assert "reason" not in got


def test_parse_seqres_fail_row_carries_the_reason():
    got = parse_seqres(SEQRES_FAIL)
    assert (got["status"], got["reason"]) == ("fail", "output")


def test_parse_seqres_not_run_has_no_reason():
    got = parse_seqres(SEQRES_NOTRUN)
    assert got["status"] == "not run"
    assert "reason" not in got


def test_parse_seqres_missing_or_truncated_degrades():
    # A missing or truncated file (no status key survives) must never pass.
    assert parse_seqres("") == {"status": "missing"}
    assert parse_seqres("runtime\t1.000s\n")["status"] == "missing"
    # Lines without a tab (a partial write) are skipped, not misparsed.
    got = parse_seqres("garbage line\nstatus\tpass\n")
    assert got["status"] == "pass"
    assert "garbage line" not in got


def test_runtime_seconds_parses_the_suffixed_value():
    assert runtime_seconds("4.077s") == 4.077
    assert runtime_seconds("12s") == 12.0
    assert runtime_seconds("3") == 3.0
    assert runtime_seconds("") is None
    assert runtime_seconds("abc") is None


def _write_seqres(
    path: Path, status: str = "pass", reason: str = "", runtime: str = "1.500s"
):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"status\t{status}"]
    if reason:
        lines.append(f"reason\t{reason}")
    if runtime:
        lines.append(f"runtime\t{runtime}")
    path.write_text("\n".join(lines) + "\n")


def test_collect_group_rows_fans_out_across_devdirs(tmp_path):
    # One test number yields one row PER devdir it ran under: the plain nodev
    # run, a set_conditions variant (nodev_tr_tcp), and a device dir.
    root = tmp_path / "results"
    _write_seqres(root / "nodev/nvme/002")
    _write_seqres(root / "nodev_tr_tcp/nvme/002", status="fail", reason="output")
    _write_seqres(root / "nvme1n1/nvme/010", status="not run", runtime="0.000s")
    # Another group's row, not collected.
    _write_seqres(root / "nodev/block/001")
    # Companions are not result files and are skipped.
    (root / "nodev/nvme/002.full").write_text("verbose output\n")
    (root / "nodev/nvme/002.out.bad").write_text("diff\n")
    rows = collect_group_rows(root, "nvme")
    assert [(r["devdir"], r["test"]) for r in rows] == [
        ("nodev", "nvme/002"),
        ("nodev_tr_tcp", "nvme/002"),
        ("nvme1n1", "nvme/010"),
    ]
    assert rows[0] == {
        "devdir": "nodev",
        "test": "nvme/002",
        "status": "pass",
        "reason": "",
        "runtime": 1.5,
    }
    assert (rows[1]["status"], rows[1]["reason"]) == ("fail", "output")
    assert rows[2]["status"] == "not run"


def test_collect_group_rows_tolerates_missing_trees_and_refuses_traversal(tmp_path):
    assert collect_group_rows(tmp_path / "absent", "nvme") == []
    with pytest.raises(ValueError):
        collect_group_rows(tmp_path, "../nvme")


def test_group_status_truth_table():
    passing = [{"status": "pass"}]
    # Zero result files is notrun, never a pass (group_requires exits 0).
    assert group_status([], True, False, False) == "notrun"
    assert group_status([{"status": "not run"}], True, False, False) == "notrun"
    assert group_status(passing, True, False, False) == "passed"
    assert group_status(passing + [{"status": "not run"}], True, False, False) == (
        "passed"
    )
    assert group_status(passing + [{"status": "fail"}], True, False, False) == "failed"
    # A truncated (missing-status) row is failure-adjacent, never a pass.
    assert group_status([{"status": "missing"}], True, False, False) == "failed"
    # The run outcome gates the verdict even over passing rows.
    assert group_status(passing, True, True, False) == "failed"
    assert group_status(passing, True, False, True) == "failed"
    assert group_status(passing, False, False, False) == "failed"


def test_run_status_never_passes_a_vacuous_run():
    assert run_status([]) == "failed"
    assert run_status([{"status": "passed"}]) == "passed"
    assert run_status([{"status": "passed"}, {"status": "failed"}]) == "failed"
    # A notrun group is NOT a pass for the run.
    assert run_status([{"status": "passed"}, {"status": "notrun"}]) == "failed"
    assert run_status([{"error": {"name": "SSH"}}]) == "failed"


def test_build_args_groups_mode_one_positional_per_group():
    assert build_args("groups", ["loop", "nbd"]) == {"loop": "loop", "nbd": "nbd"}
    assert build_args("groups", ["nvme", "nvme"]) == {"nvme": "nvme"}
    # Empty falls back to upstream's own default: every group except meta.
    got = build_args("groups", [])
    assert list(got) == default_groups()
    assert "meta" not in got


def test_build_args_tests_mode_accepts_a_list():
    got = build_args("tests", None, ["block/002", "nvme/010", "block/005"])
    assert got == {"block": "block/002 block/005", "nvme": "nvme/010"}


def test_build_args_tests_mode_splits_per_group_and_ignores_groups():
    got = build_args("tests", ["loop"], "block/002 nvme/010 block/005")
    assert got == {"block": "block/002 block/005", "nvme": "nvme/010"}


def test_build_args_refuses_bad_names():
    with pytest.raises(ValueError):
        build_args("groups", ["../escape"])
    with pytest.raises(ValueError):
        build_args("groups", [""])
    with pytest.raises(ValueError):
        build_args("tests", [], "")
    with pytest.raises(ValueError):
        build_args("tests", [], "block/2")
    with pytest.raises(ValueError):
        build_args("tests", [], "block002")


def test_catalog_and_picker_fallbacks_hold_their_shape():
    tests = catalog_tests()
    assert len(tests) == 211
    assert tests[0].startswith("block/") and all("/" in t for t in tests)
    devs = list_devices("")
    assert [d["value"] for d in devs] == [f"/dev/nvme{i}n1" for i in range(5)]
    excl = list_exclude("")
    assert excl[0]["value"] == "block" and len(excl) == 15 + 211


def test_pickers_read_the_discover_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    cache = groups_cache("demo")
    cache.parent.mkdir(parents=True)
    cache.write_text(
        '{"groups": ["loop"], "tests": ["loop/001"], '
        '"devices": [{"name": "/dev/nvme2n1", "size": "10G"}]}'
    )
    assert [g["value"] for g in list_groups("demo")] == ["loop"]
    assert [t["value"] for t in list_tests("demo")] == ["loop/001"]
    devs = list_devices("demo")
    assert devs == [{"value": "/dev/nvme2n1", "label": "/dev/nvme2n1 (10G)"}]


def test_render_blktests_config_arrays_and_only_set_knobs():
    got = render_blktests_config(
        test_devs=["/dev/nvme1n1", "/dev/nvme2n1"],
        exclude=["block/002", "nvme/010"],
        quick_run=True,
        timeout=30,
    )
    assert "TEST_DEVS=(/dev/nvme1n1 /dev/nvme2n1)\n" in got
    assert "EXCLUDE=(block/002 nvme/010)\n" in got
    assert "QUICK_RUN=1\n" in got
    assert "TIMEOUT=30\n" in got
    assert "DEVICE_ONLY" not in got
    assert "NVME_IMG_SIZE" not in got
    assert "USE_RXE" not in got


def test_render_blktests_config_always_carries_normal_user():
    assert render_blktests_config() == "NORMAL_USER=blktests\n"
    assert "NORMAL_USER=tester\n" in render_blktests_config(normal_user="tester")


def test_render_blktests_config_joins_the_list_variables():
    got = render_blktests_config(
        device_only=True,
        run_zoned_tests=True,
        nvmet_trtypes=["loop", "tcp"],
        nvmet_blkdev_types=["device", "file"],
        nvme_img_size="2G",
        nvme_num_iter=8,
        use_rxe=True,
        throtl_blkdev_types=["nullb"],
    )
    assert 'NVMET_TRTYPES="loop tcp"\n' in got
    assert 'NVMET_BLKDEV_TYPES="device file"\n' in got
    assert 'THROTL_BLKDEV_TYPES="nullb"\n' in got
    assert "NVME_IMG_SIZE=2G\n" in got
    assert "NVME_NUM_ITER=8\n" in got
    assert "USE_RXE=1\n" in got
    assert "RUN_ZONED_TESTS=1\n" in got
    assert "DEVICE_ONLY=1\n" in got


def test_render_blktests_config_carries_only_the_set_watchdog_vars():
    got = render_blktests_config(
        test_timeout=300,
        test_timeouts={"block/002": 30, "": 5, "nvme/010": 0},
    )
    assert "TEST_TIMEOUT=300\n" in got
    assert 'TEST_TIMEOUTS="block/002:30"\n' in got
    assert "TEST_TIMEOUT" not in render_blktests_config()


def test_render_blktests_config_raw_override_replaces_wholesale():
    raw = "TEST_DEVS=(/dev/sdz)\nTIMEOUT=5"
    got = render_blktests_config(
        test_devs=["/dev/nvme1n1"], edit_config=True, config=raw
    )
    assert got == raw + "\n"
    # The gate needs BOTH the toggle and a non-empty config.
    assert "NORMAL_USER=" in render_blktests_config(edit_config=True, config="  ")
    assert "NORMAL_USER=" in render_blktests_config(edit_config=False, config=raw)


def test_atomic_write_replaces_in_place_without_leftovers(tmp_path):
    target = tmp_path / "deep" / "config"
    _atomic_write(target, "one\n")
    _atomic_write(target, "two\n", mode=0o600)
    assert target.read_text() == "two\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [p.name for p in target.parent.iterdir()] == ["config"]


def test_share_dir_resolves_under_the_blktests_root(tmp_path, monkeypatch):
    assert share_dir("vm0", tmp_path) == (tmp_path / "shared/blktests/vm0").resolve()
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    assert share_dir("vm0") == (tmp_path / "shared/blktests/vm0").resolve()
    with pytest.raises(ValueError):
        share_dir("../escape", tmp_path)


def test_results_dir_keys_by_kernel_and_refuses_traversal(tmp_path):
    got = results_dir("vm0", "6.19.0", tmp_path)
    assert got == share_dir("vm0", tmp_path) / "6.19.0/results"
    for bad in ("a/b", "", ".", ".."):
        with pytest.raises(ValueError):
            results_dir("vm0", bad, tmp_path)


def test_group_catalog_names_and_default_set():
    names = group_names()
    assert len(names) == 15
    assert len(set(names)) == 15
    assert "meta" in names
    assert default_groups() == [n for n in names if n != "meta"]
    counts = {g["name"]: g["tests"] for g in GROUPS}
    assert counts["nvme"] == 61
    assert counts["bcache"] == 1
