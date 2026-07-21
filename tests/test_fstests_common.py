# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the xfstests xunit parser and shared verdict rule."""

import stat

import pytest

from f.fstests.common import (
    _atomic_write,
    _group_options,
    build_check_args,
    device_sector,
    inject_device_base,
    list_groups,
    parse_group_names,
    parse_sections,
    parse_xfs_info,
    parse_xunit,
    read_mkfs_cmd,
    read_xfs_info,
    render_check_env,
    render_local_config,
    run_status,
    section_block,
    section_block_block_size,
    section_config,
    section_external_devs,
    section_is_v4,
    section_results_dir,
    section_sector_size,
    section_vars,
    share_dir,
    xfs_catalog_text,
    xfs_feature_names,
    xfs_profiles,
    xfs_profiles_matrix,
)

SINGLE_PASS = """\
<testsuite name="xfs_4k" tests="3" failures="1" skipped="1">
  <testcase classname="xfstests" name="generic/001" time="2.1"/>
  <testcase classname="xfstests" name="generic/002" time="0.4">
    <failure message="output mismatch" type="TestFail"/>
  </testcase>
  <testcase classname="xfstests" name="generic/003" time="0.0">
    <skipped message="not supported"/>
  </testcase>
</testsuite>
"""

# Two `-i` passes of one test: the final pass passed, so the header counters
# claim zero failures while the body still carries the first pass's failure.
TWO_PASS = """\
<testsuite name="xfs_4k" tests="1" failures="0" skipped="0">
  <testcase classname="xfstests" name="generic/010" time="1.0">
    <failure message="flaked on pass 1" type="TestFail"/>
  </testcase>
  <testcase classname="xfstests" name="generic/010" time="1.0"/>
</testsuite>
"""


def test_missing_report_degrades_with_the_full_shape(tmp_path):
    s = parse_xunit(tmp_path / "xfs_4k", section="xfs_4k")
    assert s["report_present"] is False
    assert s["error"] == "no xunit report"
    assert (s["passed"], s["failed"], s["skipped"], s["tests"]) == (0, 0, 0, 0)
    assert s["iterations"] == 0
    assert s["per_test"] == []


def test_unparseable_report_degrades(tmp_path):
    d = tmp_path / "xfs_4k"
    d.mkdir()
    (d / "result.xml").write_text("<testsuite")
    s = parse_xunit(d, section="xfs_4k")
    assert s["report_present"] is True
    assert s["error"].startswith("unparseable xunit report")
    assert s["passed"] == 0


def test_single_pass_counts_and_failure_first_ordering(tmp_path):
    d = tmp_path / "xfs_4k"
    d.mkdir()
    (d / "result.xml").write_text(SINGLE_PASS)
    s = parse_xunit(d, section="xfs_4k")
    assert (s["passed"], s["failed"], s["skipped"], s["tests"]) == (1, 1, 1, 3)
    assert s["iterations"] == 1
    assert [t["test"] for t in s["per_test"]] == [
        "generic/002",
        "generic/003",
        "generic/001",
    ]
    assert s["failures"][0]["message"] == "output mismatch"
    assert s["notruns"] == ["generic/003"]


def test_multi_pass_run_derives_from_the_body_not_the_header(tmp_path):
    d = tmp_path / "xfs_4k"
    d.mkdir()
    (d / "result.xml").write_text(TWO_PASS)
    s = parse_xunit(d, section="xfs_4k")
    assert s["failed"] == 1
    assert s["iterations"] == 2
    row = s["per_test"][0]
    assert (row["status"], row["runs"], row["fails"]) == ("failed", 2, 1)


def test_run_status_never_passes_a_vacuous_run():
    assert run_status([]) == "failed"
    assert run_status([{"status": "passed"}]) == "passed"
    assert run_status([{"status": "passed"}, {"status": "failed"}]) == "failed"
    assert run_status([{"error": {"name": "SSH"}}]) == "failed"


def test_build_check_args_defaults_to_the_report_flag():
    assert build_check_args() == "-R xunit"
    assert build_check_args(report="") == ""


def test_build_check_args_groups_mode_joins_and_excludes():
    got = build_check_args(groups=["auto", "quick"], exclude_group="dangerous")
    assert got == "-g auto,quick -x dangerous -R xunit"


def test_build_check_args_tests_mode_drops_the_group_flags():
    got = build_check_args(
        test_selection="tests",
        tests="generic/001 generic/002",
        groups=["auto"],
        exclude_group="dangerous",
        exclude="ban.txt",
        randomize=True,
        iterations=3,
        loop_on_fail=2,
    )
    assert got == "-X ban.txt -R xunit -r -I 3 -L 2 generic/001 generic/002"


def test_build_check_args_iteration_flag_tracks_stop_on_fail():
    assert build_check_args(iterations=3, report="") == "-I 3"
    assert build_check_args(iterations=3, stop_on_fail=False, report="") == "-i 3"
    assert build_check_args(iterations=1, report="") == ""


def test_render_check_env_carries_only_the_set_watchdog_vars():
    got = render_check_env("/var/lib/xfstests/local.config", "-g auto -R xunit")
    assert got == (
        "HOST_OPTIONS=/var/lib/xfstests/local.config\n"
        "XFSTESTS_CHECK_ARGS=-g auto -R xunit\n"
    )
    got = render_check_env(
        "/var/lib/xfstests/local.config",
        "-g auto -R xunit",
        test_timeout=300,
        test_timeouts={"generic/001": 60, "": 5, "generic/002": 0},
    )
    assert got.endswith("TEST_TIMEOUT=300\nTEST_TIMEOUTS=generic/001:60\n")


def test_render_local_config_returns_a_nonempty_config_verbatim():
    assert render_local_config("[foo]\nFSTYP=ext4") == "[foo]\nFSTYP=ext4\n"
    assert render_local_config("[foo]\nFSTYP=ext4\n") == "[foo]\nFSTYP=ext4\n"


def test_render_local_config_synthesizes_a_two_device_base():
    got = render_local_config("", devices=[{"dev": "/dev/vdb"}, "/dev/vdc"])
    assert got == (
        "[default]\n"
        "FSTYP=xfs\n"
        "TEST_DEV=/dev/vdb\n"
        "TEST_DIR=/media/test\n"
        "SCRATCH_DEV=/dev/vdc\n"
        "SCRATCH_MNT=/media/scratch\n"
    )


def test_render_local_config_refuses_an_empty_synthesis():
    with pytest.raises(ValueError):
        render_local_config("", devices=[])


def test_render_local_config_never_sets_both_scratch_keys():
    got = render_local_config("", devices=["/dev/vdb", "/dev/vdc", "/dev/vdd"])
    assert "SCRATCH_DEV=" not in got.replace("SCRATCH_DEV_POOL=", "")
    assert 'SCRATCH_DEV_POOL="/dev/vdc /dev/vdd"' in got


def test_xfs_feature_names_pins_the_selector_forms_first():
    names = xfs_feature_names()
    assert names[:2] == ["all", "default"]
    assert "" not in names
    assert "nocrc" in names and "realtime_reflink" in names


def test_xfs_profiles_lists_the_full_matrix_in_catalog_order():
    assert xfs_profiles() == list(xfs_profiles_matrix())


def test_xfs_profiles_matrix_default_geometry_is_one_section_per_feature():
    assert xfs_profiles_matrix("quota", geometry="default") == {
        "xfs_quota": {"mkfs": "", "mount": "-o usrquota,grpquota"}
    }
    assert xfs_profiles_matrix("default", geometry="default") == {
        "xfs": {"mkfs": "", "mount": ""}
    }


def test_xfs_profiles_matrix_refuses_unknown_selectors():
    with pytest.raises(ValueError):
        xfs_profiles_matrix("bogus")
    with pytest.raises(ValueError):
        xfs_profiles_matrix("quota", geometry="bogus")


def test_xfs_profiles_matrix_v5_starts_at_the_crc_block_floor():
    m = xfs_profiles_matrix("default")
    assert "xfs_bs512_ss512" not in m
    assert "xfs_bs1k_ss2k" not in m
    assert m["xfs_bs4k_ss512"] == {"mkfs": "-b size=4096 -s size=512", "mount": ""}


def test_xfs_profiles_matrix_v4_reaches_512_but_caps_at_the_page_size():
    m = xfs_profiles_matrix("nocrc")
    assert list(m)[0] == "xfs_nocrc_bs512_ss512"
    assert list(m)[-1] == "xfs_nocrc_bs4k_ss4k"
    assert not any("bs8k" in name for name in m)
    assert m["xfs_nocrc_bs512_ss512"] == {
        "mkfs": "-m crc=0 -b size=512 -s size=512",
        "mount": "",
    }


def test_xfs_profiles_matrix_rtx_variants_clear_the_min_rtextsize():
    m = xfs_profiles_matrix("realtime_rtx2")
    assert list(m)[0] == "xfs_realtime_rtx2_bs2k_ss512"
    assert not any("bs1k" in name for name in m)
    assert m["xfs_realtime_rtx2_bs2k_ss512"] == {
        "mkfs": "-b size=2048 -s size=512 -r extsize=4096",
        "mount": "",
        "needs": "rtdev",
    }


def test_xfs_profiles_matrix_rt_reflink_honors_its_min_block():
    m = xfs_profiles_matrix("realtime_reflink")
    assert list(m)[0] == "xfs_realtime_reflink_bs4k_ss512"
    assert not any("bs1k" in name or "bs2k" in name for name in m)
    assert m["xfs_realtime_reflink_bs4k_ss512"]["mkfs"] == (
        "-m metadir=1 -b size=4096 -s size=512"
    )


def test_xfs_catalog_text_renders_device_agnostic_sections():
    assert xfs_catalog_text("quota", geometry="default") == (
        '[xfs_quota]\nFSTYP=xfs\nMOUNT_OPTIONS="-o usrquota,grpquota"\n'
    )
    assert xfs_catalog_text("logdev", geometry="default") == (
        "[xfs_logdev]\nFSTYP=xfs\nUSE_EXTERNAL=yes\nTEST_LOGDEV=\nSCRATCH_LOGDEV=\n"
    )


def test_xfs_catalog_text_round_trips_through_parse_sections():
    assert parse_sections(xfs_catalog_text()) == xfs_profiles()


CONFIG = """\
# host options
[xfs_a]
FSTYP=xfs
MKFS_OPTIONS="-m crc=0 -b size=1024 -s size=512"

[xfs_b]
FSTYP=xfs
MOUNT_OPTIONS='-o quota'
[xfs_a]
X=1
"""


def test_parse_sections_keeps_file_order_and_collapses_duplicates():
    assert parse_sections(CONFIG) == ["xfs_a", "xfs_b"]
    assert parse_sections("") == []


def test_section_vars_reads_one_block_and_strips_quotes():
    assert section_vars(CONFIG, "xfs_b") == {
        "FSTYP": "xfs",
        "MOUNT_OPTIONS": "-o quota",
    }
    v = section_vars(CONFIG, "xfs_a")
    assert v["MKFS_OPTIONS"] == "-m crc=0 -b size=1024 -s size=512"


def test_section_vars_reads_the_whole_file_when_sectionless():
    assert section_vars('A=1\nB="two words"\n', "anything") == {
        "A": "1",
        "B": "two words",
    }


def test_section_block_extracts_the_verbatim_block():
    assert section_block(CONFIG, "xfs_b") == (
        "[xfs_b]\nFSTYP=xfs\nMOUNT_OPTIONS='-o quota'\n"
    )
    assert section_block(CONFIG, "missing") == ""


def test_section_geometry_helpers_read_the_mkfs_options():
    assert section_block_block_size(CONFIG, "xfs_a") == 1024
    assert section_sector_size(CONFIG, "xfs_a") == 512
    assert section_is_v4(CONFIG, "xfs_a") is True
    assert section_block_block_size(CONFIG, "xfs_b") == 4096
    assert section_sector_size(CONFIG, "xfs_b") is None
    assert section_is_v4(CONFIG, "xfs_b") is False


def test_section_external_devs_reads_empty_canonical_vars():
    # Empty external-device vars are the injector's fill list, in EXTERNAL_DEV_KEYS order.
    assert section_external_devs("[x]\nFSTYP=xfs\nTEST_RTDEV=\nSCRATCH_RTDEV=\n") == [
        "TEST_RTDEV",
        "SCRATCH_RTDEV",
    ]
    assert section_external_devs("[x]\nSCRATCH_LOGDEV=\n") == ["SCRATCH_LOGDEV"]
    # A hardcoded (non-empty) external device is left alone, not returned.
    assert section_external_devs("[x]\nSCRATCH_RTDEV=/dev/nvme2n1\n") == []
    assert section_external_devs("[x]\nFSTYP=xfs\n") == []
    assert section_external_devs("") == []


def test_device_sector_takes_the_max_and_defaults_to_512():
    devs = [{"name": "/dev/a", "log_sec": 512}, {"name": "/dev/b", "log_sec": 4096}]
    assert device_sector(devs) == 4096
    assert device_sector(["/dev/a", "/dev/b"]) == 512
    assert device_sector([]) == 512


def test_inject_device_base_binds_two_devices():
    got = inject_device_base("[p]\nFSTYP=xfs\n", ["/dev/nvme0n1", "/dev/nvme1n1"])
    assert got == (
        "[p]\n"
        "FSTYP=xfs\n"
        "TEST_DEV=/dev/nvme0n1\n"
        "TEST_DIR=/media/test\n"
        "SCRATCH_MNT=/media/scratch\n"
        "SCRATCH_DEV=/dev/nvme1n1\n"
    )


def test_inject_device_base_pools_the_extras_without_scratch_dev():
    devs = ["/dev/nvme0n1", "/dev/nvme1n1", "/dev/nvme2n1", "/dev/nvme3n1"]
    got = inject_device_base("[p]\nFSTYP=xfs\n", devs)
    assert 'SCRATCH_DEV_POOL="/dev/nvme1n1 /dev/nvme2n1 /dev/nvme3n1"' in got
    assert "SCRATCH_DEV=" not in got


def test_inject_device_base_never_overrides_a_hardcoded_device():
    block = '[p]\nFSTYP=xfs\nTEST_DEV=/dev/sda\nSCRATCH_DEV_POOL="/dev/sdb"\n'
    got = inject_device_base(block, ["/dev/nvme0n1", "/dev/nvme1n1"])
    assert "TEST_DEV=/dev/sda" in got
    assert "TEST_DEV=/dev/nvme0n1" not in got
    assert "SCRATCH_DEV=" not in got


def test_inject_device_base_fills_empty_external_vars_on_test_and_scratch():
    # The catalog declares the canonical external vars empty; the injector fills them
    # in place (keeping their line order) from devs[2:], after TEST_DEV/SCRATCH_DEV.
    block = "[rt]\nFSTYP=xfs\nUSE_EXTERNAL=yes\nTEST_RTDEV=\nSCRATCH_RTDEV=\n"
    devs = ["/dev/nvme0n1", "/dev/nvme1n1", "/dev/nvme2n1", "/dev/nvme3n1"]
    got = inject_device_base(block, devs)
    assert got == (
        "[rt]\n"
        "FSTYP=xfs\n"
        "USE_EXTERNAL=yes\n"
        "TEST_RTDEV=/dev/nvme2n1\n"
        "SCRATCH_RTDEV=/dev/nvme3n1\n"
        "TEST_DEV=/dev/nvme0n1\n"
        "TEST_DIR=/media/test\n"
        "SCRATCH_MNT=/media/scratch\n"
        "SCRATCH_DEV=/dev/nvme1n1\n"
    )


def test_inject_device_base_keeps_a_hardcoded_external_device():
    # A non-empty external var is operator-pinned; the injector leaves it and its
    # section falls back to the plain test/scratch bind (no external fill).
    block = "[rt]\nFSTYP=xfs\nUSE_EXTERNAL=yes\nSCRATCH_RTDEV=/dev/sdz\n"
    got = inject_device_base(block, ["/dev/nvme0n1", "/dev/nvme1n1"])
    assert "SCRATCH_RTDEV=/dev/sdz" in got
    assert "TEST_DEV=/dev/nvme0n1" in got


def test_inject_device_base_reserves_the_last_device_for_logwrites():
    got = inject_device_base(
        "[p]\nFSTYP=xfs\n",
        ["/dev/nvme0n1", "/dev/nvme1n1", "/dev/nvme2n1"],
        logwrites=True,
    )
    assert got.endswith("SCRATCH_DEV=/dev/nvme1n1\nLOGWRITES_DEV=/dev/nvme2n1\n")
    block = "[l]\nFSTYP=xfs\nUSE_EXTERNAL=yes\nTEST_LOGDEV=\nSCRATCH_LOGDEV=\n"
    devs = [f"/dev/nvme{i}n1" for i in range(5)]
    got = inject_device_base(block, devs, logwrites=True)
    assert "TEST_LOGDEV=/dev/nvme2n1\nSCRATCH_LOGDEV=/dev/nvme3n1\n" in got
    assert got.endswith("SCRATCH_DEV=/dev/nvme1n1\nLOGWRITES_DEV=/dev/nvme4n1\n")


def test_inject_device_base_refuses_too_few_devices():
    with pytest.raises(ValueError):
        inject_device_base("[p]\nFSTYP=xfs\n", ["/dev/nvme0n1"])
    # An external section needs 4 devices (test + scratch external pair), not 3.
    with pytest.raises(ValueError):
        inject_device_base(
            "[rt]\nFSTYP=xfs\nUSE_EXTERNAL=yes\nTEST_RTDEV=\nSCRATCH_RTDEV=\n",
            ["/dev/nvme0n1", "/dev/nvme1n1", "/dev/nvme2n1"],
        )
    with pytest.raises(ValueError):
        inject_device_base(
            "[p]\nFSTYP=xfs\n", ["/dev/nvme0n1", "/dev/nvme1n1"], logwrites=True
        )


def test_share_dir_resolves_under_the_fstests_root(tmp_path, monkeypatch):
    assert share_dir("vm0", tmp_path) == (tmp_path / "shared/fstests/vm0").resolve()
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    assert share_dir("vm0") == (tmp_path / "shared/fstests/vm0").resolve()
    with pytest.raises(ValueError):
        share_dir("../escape", tmp_path)


def test_section_results_dir_keys_by_kernel_and_refuses_traversal(tmp_path):
    got = section_results_dir("vm0", "6.18.0", "xfs_a", tmp_path)
    assert got == share_dir("vm0", tmp_path) / "6.18.0/results/xfs_a"
    with pytest.raises(ValueError):
        section_results_dir("vm0", "a/b", "xfs_a", tmp_path)
    with pytest.raises(ValueError):
        section_results_dir("vm0", "", "xfs_a", tmp_path)
    with pytest.raises(ValueError):
        section_results_dir("vm0", "6.18.0", "../../6.19.0/results/x", tmp_path)


def test_section_config_reads_the_rendered_geometry(tmp_path):
    d = share_dir("vm0", tmp_path)
    d.mkdir(parents=True)
    (d / "xfs_a.config").write_text(
        '[xfs_a]\nFSTYP=xfs\nMKFS_OPTIONS="-m crc=0 -b size=1024 -s size=512"\n'
    )
    assert section_config("vm0", "xfs_a", tmp_path) == {
        "fstype": "xfs",
        "mkfs_options": "-m crc=0 -b size=1024 -s size=512",
        "mount_options": "",
        "bsize": 1024,
        "sectsize": 512,
    }
    assert section_config("vm0", "missing", tmp_path) == {}


def test_parse_xfs_info_reads_hyphenated_keys_and_last_value_wins():
    text = (
        "meta-data=/dev/vdb isize=512 crc=1 finobt=1, sparse=1\n"
        "naming =version 2 bsize=4096 ascii-ci=0\n"
        "data = bsize=512 lazy-count=1\n"
        "realtime =external extsz=8192 blocks=2048, rtextents=1024\n"
    )
    got = parse_xfs_info(text)
    assert got["meta-data"] == "/dev/vdb"
    assert (got["crc"], got["ascii-ci"], got["lazy-count"]) == ("1", "0", "1")
    # rtextents (no space before =) is captured, the realtime proof the report shows.
    assert got["rtextents"] == "1024"
    assert parse_xfs_info("") == {}


def test_read_xfs_info_surfaces_the_capture_or_degrades(tmp_path):
    d = share_dir("vm0", tmp_path)
    d.mkdir(parents=True)
    (d / "xfs_a.xfs_info").write_text("crc=1 reflink=1")
    got = read_xfs_info("vm0", "xfs_a", tmp_path)
    assert got["raw"] == "crc=1 reflink=1"
    assert got["features"] == {"crc": "1", "reflink": "1"}
    assert read_xfs_info("vm0", "missing", tmp_path) == {}


def test_read_mkfs_cmd_surfaces_the_realized_command_or_degrades(tmp_path):
    d = share_dir("vm0", tmp_path)
    d.mkdir(parents=True)
    (d / "xfs_rt.mkfs").write_text(
        "mkfs --type xfs -f -r extsize=8192 -r rtdev=/dev/nvme2n1 /dev/nvme1n1\n"
    )
    got = read_mkfs_cmd("vm0", "xfs_rt", tmp_path)
    assert got.endswith("-r rtdev=/dev/nvme2n1 /dev/nvme1n1")
    assert read_mkfs_cmd("vm0", "missing", tmp_path) == ""


GROUP_NAMES = """\
===============         ================================================
Group Name:             Description
===============         ================================================

zebra                   comes last alphabetically
auto                    include in automatic testing
quick                   tests which are expected
                        to run quickly
"""


def test_parse_group_names_reads_the_table_and_joins_wrapped_lines():
    assert parse_group_names(GROUP_NAMES) == [
        {"name": "zebra", "description": "comes last alphabetically"},
        {"name": "auto", "description": "include in automatic testing"},
        {"name": "quick", "description": "tests which are expected to run quickly"},
    ]
    assert parse_group_names("") == []


def test_group_options_pin_auto_and_quick_then_sort_and_filter():
    opts = _group_options(parse_group_names(GROUP_NAMES))
    assert [o["value"] for o in opts] == ["auto", "quick", "zebra"]
    assert opts[0]["label"] == "auto: include in automatic testing"
    filtered = _group_options(parse_group_names(GROUP_NAMES), filterText="ZEB")
    assert [o["value"] for o in filtered] == ["zebra"]


def test_group_options_clip_a_long_description():
    opts = _group_options([{"name": "zzz", "description": "x" * 100}])
    assert opts[0]["label"] == "zzz: " + "x" * 77 + "..."


def test_list_groups_falls_back_before_any_guest(monkeypatch):
    monkeypatch.delenv("WORKERS_DIR", raising=False)
    assert [o["value"] for o in list_groups()] == ["auto", "quick"]


def test_atomic_write_replaces_in_place_without_leftovers(tmp_path):
    target = tmp_path / "deep" / "local.config"
    _atomic_write(target, "one\n")
    _atomic_write(target, "two\n", mode=0o600)
    assert target.read_text() == "two\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [p.name for p in target.parent.iterdir()] == ["local.config"]
