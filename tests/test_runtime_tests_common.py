# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the runtime-tests catalog, unit mapping and verdict rule."""

import json
import re

import pytest

from f.runtime_tests.common import (
    CATALOG,
    catalog_entry,
    list_modules,
    run_status,
    unit_for,
)


def test_catalog_entry_fills_defaults_for_a_cataloged_module():
    e = catalog_entry("test_xarray")
    assert e["label"] == "XArray (test_xarray)"
    assert (e["loaded_on_pass"], e["kind"], e["unload"]) == (True, "test", True)
    assert (e["scan_kmsg"], e["fail_re"], e["sentinel_re"]) == (True, None, None)


def test_catalog_entry_keeps_the_inverted_ida_overrides():
    e = catalog_entry("test_ida")
    assert e["loaded_on_pass"] is False
    assert e["scan_kmsg"] is False
    assert e["unload"] is True


def test_uncataloged_module_gets_the_strictest_defaults():
    e = catalog_entry("test_mystery")
    assert e["label"] == "test_mystery"
    assert (e["loaded_on_pass"], e["kind"]) == (True, "test")
    assert (e["summary_re"], e["sentinel_re"], e["fail_re"]) == (None, None, None)
    assert (e["unload"], e["scan_kmsg"]) == (False, True)


def test_source_verified_drops_stay_out_of_the_catalog():
    assert "test_parman" not in CATALOG
    assert "test_dhry" not in CATALOG


def test_unit_for_maps_onto_the_upstream_modprobe_template():
    assert unit_for("test_xarray") == "modprobe@test_xarray.service"
    assert unit_for("find_bit_benchmark") == "modprobe@find_bit_benchmark.service"


@pytest.mark.parametrize("bad", ["", "net/forwarding", "Test_Ida", "a-b", "a b"])
def test_unit_for_refuses_a_name_outside_the_module_charset(bad):
    with pytest.raises(ValueError):
        unit_for(bad)


def test_run_status_never_passes_a_vacuous_or_errored_run():
    assert run_status([]) == "failed"
    assert run_status([{"status": "passed"}]) == "passed"
    assert run_status([{"status": "passed"}, {"status": "failed"}]) == "failed"
    assert run_status([{"status": "passed"}, {"error": {"name": "SSH"}}]) == "failed"
    assert run_status([{"status": "notrun"}]) == "failed"


def _summary(module: str, line: str):
    return re.search(catalog_entry(module)["summary_re"], line)


def _sentinel(module: str, line: str):
    return re.search(catalog_entry(module)["sentinel_re"], line)


def test_exit_honest_summary_regexes_extract_the_counts():
    m = _summary("test_xarray", "XArray: 840 of 840 tests passed")
    assert m and (m["passed"], m["total"]) == ("840", "840")
    m = _summary("test_maple_tree", "maple_tree: 158M of 158M tests passed")
    assert m is None
    m = _summary("test_maple_tree", "maple_tree: 71 of 71 tests passed")
    assert m and (m["passed"], m["total"]) == ("71", "71")


def test_bpf_and_rhashtable_summaries_demand_the_all_clean_line():
    ok = "test_bpf: Summary: 1061 PASSED, 0 FAILED, [1061/1061 JIT'ed]"
    m = _summary("test_bpf", ok)
    assert m and m["passed"] == "1061"
    assert _summary("test_bpf", ok.replace("0 FAILED", "2 FAILED")) is None
    ok = "Started 10 threads, 0 failed, rhltable test returns 0"
    m = _summary("test_rhashtable", ok)
    assert m and m["total"] == "10"
    assert _summary("test_rhashtable", ok.replace("0 failed", "1 failed")) is None


def test_inverted_ida_still_reads_a_plain_counts_summary():
    m = _summary("test_ida", "IDA: 10 of 10 tests passed")
    assert m and (m["passed"], m["total"]) == ("10", "10")


def test_auto_unload_sentinels_match_the_opening_and_closing_lines():
    assert _sentinel("rbtree_test", "rbtree testing")
    assert _sentinel("rbtree_test", "augmented rbtree testing")
    assert _sentinel("interval_tree_test", "interval tree insert/remove")
    assert _sentinel("percpu_test", "percpu test done")
    assert _sentinel("percpu_test", "percpu test start") is None


def test_vmalloc_stress_reads_the_per_worker_summary_lines():
    clean = "Summary: fix_size_alloc_test passed: 6 failed: 0 xfailed: 0"
    dirty = "Summary: fix_size_alloc_test passed: 5 failed: 1 xfailed: 0"
    assert _sentinel("test_vmalloc", clean)
    assert _sentinel("test_vmalloc", dirty)
    fail_re = catalog_entry("test_vmalloc")["fail_re"]
    assert re.search(fail_re, clean) is None
    assert re.search(fail_re, dirty)


def test_benchmark_sentinels_match_the_timing_lines():
    line = "find_next_bit:            109731 ns,  32786 iterations"
    assert _sentinel("find_bit_benchmark", line)
    line = "test_workqueue:   wq_unbound       123456 items/sec\tp50=10us"
    assert _sentinel("test_workqueue", line)


def test_list_modules_falls_back_to_the_catalog(monkeypatch):
    monkeypatch.delenv("WORKERS_DIR", raising=False)
    rows = list_modules()
    assert [r["value"] for r in rows] == list(CATALOG)
    assert rows[0]["label"] == "XArray (test_xarray)"


def test_list_modules_reads_the_cache_in_catalog_order(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    cache = tmp_path / "shared/runtime-tests/vm0"
    cache.mkdir(parents=True)
    (cache / "modules.json").write_text(
        json.dumps(["test_new_thing", "test_ida", "test_xarray"])
    )
    rows = list_modules(vm_name="vm0")
    assert [r["value"] for r in rows] == ["test_xarray", "test_ida", "test_new_thing"]
    assert list_modules(vm_name="vm0", filterText="ida") == [
        {"value": "test_ida", "label": "IDA allocator (test_ida)"}
    ]
