# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the usertests catalog, unit escaping and verdict rule."""

import re

from f.usertests.common import (
    CATALOG,
    catalog_entry,
    item_args,
    item_unit,
    run_status,
    unit_escape,
)


def test_catalog_entry_fills_defaults_for_a_cataloged_item():
    e = catalog_entry("vma/vma")
    assert e["label"] == "VMA userland tests (vma/vma)"
    assert (e["kind"], e["silent_ok"], e["count_lines"]) == ("test", False, False)
    assert (e["sentinel_re"], e["expected_assert_re"], e["args"]) == (None, None, "")


def test_memblock_entry_keeps_its_line_counting_policy():
    e = catalog_entry("memblock/main")
    assert e["count_lines"] is True
    assert e["args"] == "--verbose"
    assert re.search(e["sentinel_re"], "memblock_add_simple_check: passed")
    assert re.search(e["fail_line_re"], "memblock_add_simple_check: failed")
    assert re.search(e["sentinel_re"], "memblock_add_simple_check: failed") is None


def test_uncataloged_item_gets_the_strictest_defaults():
    e = catalog_entry("rbtree/rbtree_test")
    assert e["label"] == "rbtree/rbtree_test"
    assert (e["kind"], e["silent_ok"]) == ("test", True)
    assert (e["summary_re"], e["sentinel_re"], e["fail_line_re"]) == (None, None, None)
    assert (e["count_lines"], e["expected_assert_re"], e["args"]) == (False, None, "")


def test_unbuildable_rbtree_pair_and_scatterlist_peers_stay_out():
    assert "rbtree/rbtree_test" not in CATALOG
    assert "rbtree/interval_tree_test" not in CATALOG
    assert "scatterlist/main" in CATALOG


def test_item_args_composes_the_radix_tree_main_knobs():
    assert item_args("radix-tree/main") == ""
    assert item_args("radix-tree/main", seed=12345) == "-s 12345"
    assert item_args("radix-tree/main", long_run=True) == "-l"
    assert item_args("radix-tree/main", seed=7, long_run=True) == "-s 7 -l"


def test_item_args_is_the_catalog_constant_everywhere_else():
    assert item_args("memblock/main", seed=7, long_run=True) == "--verbose"
    assert item_args("vma/vma", seed=7, long_run=True) == ""
    assert item_args("unknown/thing") == ""


def test_run_status_never_passes_a_vacuous_or_errored_run():
    assert run_status([]) == "failed"
    assert run_status([{"status": "passed"}]) == "passed"
    assert run_status([{"status": "passed"}, {"status": "failed"}]) == "failed"
    assert run_status([{"status": "passed"}, {"error": {"name": "SSH"}}]) == "failed"


def test_unit_escape_follows_systemd_do_escape():
    assert unit_escape("radix-tree/main") == r"radix\x2dtree-main"
    assert unit_escape("vma/vma") == "vma-vma"
    assert unit_escape("cpu-hotplug") == r"cpu\x2dhotplug"
    assert unit_escape("a:b_c.9") == "a:b_c.9"
    assert unit_escape(".hidden") == r"\x2ehidden"
    assert unit_escape("a\\b") == r"a\x5cb"


def test_item_unit_maps_onto_the_one_usertests_template():
    assert item_unit("radix-tree/main") == r"usertests@radix\x2dtree-main.service"
    assert item_unit("vma/vma") == "usertests@vma-vma.service"


def test_idr_whitelist_matches_only_the_deliberate_idr_warns():
    whitelist = catalog_entry("radix-tree/idr-test")["expected_assert_re"]
    assert whitelist == catalog_entry("radix-tree/main")["expected_assert_re"]
    assert re.search(whitelist, "assertion failed at lib/idr.c:594")
    assert re.search(whitelist, "assertion failed at lib/xarray.c:437") is None


def test_summary_and_sentinel_regexes_extract_the_counts():
    m = re.search(
        catalog_entry("vma/vma")["summary_re"], "36 tests run, 36 passed, 0 failed."
    )
    assert m and (m["run"], m["passed"], m["failed"]) == ("36", "36", "0")
    m = re.search(
        catalog_entry("radix-tree/xarray")["summary_re"],
        "XArray: 158000000 of 158000000 tests passed",
    )
    assert m and m["passed"] == m["total"]
    assert re.search(catalog_entry("radix-tree/main")["sentinel_re"], "tests completed")
    assert (
        re.search(
            catalog_entry("radix-tree/main")["sentinel_re"], "all tests completed?"
        )
        is None
    )
