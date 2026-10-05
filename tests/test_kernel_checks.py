# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the check steps' log parsers (`f.kernel.checks`)."""

from f.kernel import checks as common

WT = "/w/workers/0000/main/linux"


def test_kernel_objects_skip_what_kbuild_does_not_build(tmp_path):
    for obj in ("lib/.xarray.o.cmd", "fs/xfs/.xfs_inode.o.cmd"):
        (tmp_path / obj).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / obj).write_text("")
    files = [
        "lib/xarray.c",
        "include/linux/xarray.h",
        "tools/testing/radix-tree/main.c",
        "scripts/mod/modpost.c",
        "samples/rust/foo.c",
        "fs/xfs/xfs_inode.c",
        "kernel/trace/trace_events_user.c",
    ]
    assert common.kernel_objects(files, str(tmp_path)) == [
        "lib/xarray.o",
        "fs/xfs/xfs_inode.o",
    ]


def test_worktree_path_folds_build_relative_and_absolute_paths():
    assert common.worktree_path("../lib/xarray.c") == "lib/xarray.c"
    assert common.worktree_path("./lib/xarray.c") == "lib/xarray.c"
    assert common.worktree_path(f"{WT}/lib/xarray.c", WT) == "lib/xarray.c"
    assert common.worktree_path(f"{WT}/lib/xarray.c", WT + "/") == "lib/xarray.c"


def test_checkpatch_terse_show_types_levels():
    log = (
        "/c/patches/0001-x.patch:12: ERROR:TRAILING_WHITESPACE: trailing whitespace\n"
        "/c/patches/0001-x.patch:20: WARNING:LONG_LINE: line length of 101 exceeds\n"
        "/c/patches/0002-y.patch:7: CHECK:SPACING: spaces preferred around '+'\n"
        "total: 1 errors, 1 warnings, 1 checks, 40 lines checked\n"
    )
    found = common.diagnostics(log)
    assert [f["level"] for f in found] == ["error", "warning", "check"]
    assert found[0]["message"] == "TRAILING_WHITESPACE: trailing whitespace"
    assert common.counts(found) == {"errors": 1, "warnings": 1, "checks": 1}


def test_compiler_and_sparse_lines():
    log = (
        "  CC      lib/xarray.o\n"
        "../lib/xarray.c: In function 'xas_load':\n"
        "../lib/xarray.c:42:7: warning: unused variable 'x' [-Wunused-variable]\n"
        "../lib/xarray.c:99:1: error: expected ';' before '}' token\n"
        "../include/linux/xarray.h:10:5: fatal error: foo.h: No such file\n"
        "lib/xarray.c:57:9: warning: context imbalance in 'f' - wrong count at exit\n"
    )
    found = common.diagnostics(log)
    assert [(f["file"], f["line"], f["level"]) for f in found] == [
        ("lib/xarray.c", 42, "warning"),
        ("lib/xarray.c", 99, "error"),
        ("include/linux/xarray.h", 10, "error"),
        ("lib/xarray.c", 57, "warning"),
    ]


def test_coccinelle_column_ranges_and_sphinx_lines():
    log = (
        "lib/xarray.c:120:8-14: WARNING: Unsigned expression compared with zero\n"
        f"{WT}/Documentation/core-api/xarray.rst:33: WARNING: Unknown target name\n"
        f"{WT}/Documentation/core-api/foo.rst: CRITICAL: Title level inconsistent\n"
    )
    found = common.diagnostics(log, WT)
    assert [(f["file"], f["line"], f["level"]) for f in found] == [
        ("lib/xarray.c", 120, "warning"),
        ("Documentation/core-api/xarray.rst", 33, "warning"),
        ("Documentation/core-api/foo.rst", None, "error"),
    ]


def test_kernel_doc_warning():
    log = "lib/xarray.c:2001: warning: Function parameter or struct member 'xa' not described in 'xa_load'\n"
    (found,) = common.diagnostics(log)
    assert (found["file"], found["line"], found["level"]) == (
        "lib/xarray.c",
        2001,
        "warning",
    )


def test_kernel_doc_v7_warning_form_counted_once():
    log = (
        "Warning: lib/xarray.c:2490 function parameter 'extra' not described in 'f'\n"
        "Warning: lib/xarray.c:2490 No description found for return value of 'f'\n"
        "Warning: lib/xarray.c:2490 function parameter 'extra' not described in 'f'\n"
    )
    found = common.diagnostics(log)
    assert [(f["file"], f["line"], f["level"]) for f in found] == [
        ("lib/xarray.c", 2490, "warning"),
        ("lib/xarray.c", 2490, "warning"),
    ]


def test_unrelated_make_noise_is_not_a_finding():
    log = (
        "make[2]: *** [scripts/Makefile.build:203: lib/xarray.o] Error 1\n"
        "WARNING: modpost: missing MODULE_DESCRIPTION() in foo.o\n"
        "  LD      vmlinux\n"
    )
    assert common.diagnostics(log) == []


def test_rust_diagnostics_take_the_location_line():
    log = (
        "  CLIPPY  rust/kernel.o\n"
        "warning: unused variable: `x`\n"
        f"  --> {WT}/rust/kernel/xarray.rs:12:9\n"
        "   |\n"
        "error[E0308]: mismatched types\n"
        "  --> ../rust/kernel/alloc.rs:40:5\n"
        "warning: this could be simplified\n"
        "  --> rust/kernel/sync.rs:3:1\n"
        "   = note: `-W clippy::needless-return` implied by `-W clippy::all`\n"
        "warning: 2 warnings emitted\n"
        "error: aborting due to 1 previous error\n"
    )
    found = common.rust_diagnostics(log, WT)
    assert [(f["file"], f["line"], f["level"]) for f in found] == [
        ("rust/kernel/xarray.rs", 12, "warning"),
        ("rust/kernel/alloc.rs", 40, "error"),
        ("rust/kernel/sync.rs", 3, "warning"),
    ]


def test_rustfmt_check_both_diff_header_forms():
    log = (
        f"Diff in {WT}/rust/kernel/xarray.rs:12:\n"
        "-    let x=1;\n"
        "+    let x = 1;\n"
        f"Diff in {WT}/rust/kernel/sync.rs at line 3:\n"
    )
    found = common.rustfmt_diffs(log, WT)
    assert [(f["file"], f["line"]) for f in found] == [
        ("rust/kernel/xarray.rs", 12),
        ("rust/kernel/sync.rs", 3),
    ]
    assert common.counts(found)["errors"] == 2


def test_rusttest_totals_sum_every_test_binary():
    log = (
        "test result: ok. 12 passed; 0 failed; 0 ignored; 0 measured\n"
        "test result: FAILED. 3 passed; 2 failed; 0 ignored; 0 measured\n"
    )
    assert common.rusttest_totals(log) == {"passed": 15, "failed": 2}


def test_checkstack_lines():
    log = (
        "0xffffffff81a2b3c4 snd_hda_codec_new [vmlinux]:\t\t1032\n"
        "0x0000000000001234 xfs_bmapi_write [xfs.ko]:\t\t\t512\n"
        "unrelated line\n"
    )
    assert common.stack_frames(log) == [
        {"function": "snd_hda_codec_new", "object": "vmlinux", "bytes": 1032},
        {"function": "xfs_bmapi_write", "object": "xfs.ko", "bytes": 512},
    ]


def test_in_files_narrows_to_the_series():
    found = [
        {"file": "lib/xarray.c", "line": 1, "level": "warning", "message": ""},
        {"file": "mm/filemap.c", "line": 2, "level": "error", "message": ""},
    ]
    assert common.in_files(found, ["lib/xarray.c"]) == found[:1]


def test_errors_fail_and_warnings_do_not():
    assert common.status(0) == "passed"
    assert common.status(1) == "failed"
    assert common.status(0, failed=True) == "failed"
    found = [{"file": "a.c", "line": 1, "level": "warning", "message": "w"}]
    out = common.result("sparse", found, None, objects=["a.o"])
    assert (out["status"], out["warnings"], out["objects"]) == ("passed", 1, ["a.o"])
    assert common.result("sparse", [], None, failed=True)["status"] == "failed"


def test_counting_keeps_warnings_as_warnings_and_merges_existing_flags():
    assert common.counting("") == ("KCFLAGS=-Wno-error KRUSTFLAGS=--cap-lints=warn")
    out = common.counting("LLVM=1 'KCFLAGS=-O2 -fdebug-prefix-map=/w/='")
    assert out == (
        "LLVM=1 'KCFLAGS=-O2 -fdebug-prefix-map=/w/= -Wno-error' "
        "KRUSTFLAGS=--cap-lints=warn"
    )
