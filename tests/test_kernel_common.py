# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the remaining pure logic across the f.kernel step modules."""

import os
import shutil
from pathlib import Path

import pytest

import f.kernel.compile as compile_step
from f.common import store
from f.kernel import (
    build_selftests,
    build_usertests,
    configure_fragments,
    configure_make,
    configure_preset,
    fetch_devel,
    fetch_identity,
    publish_devel,
    publish_selftests,
    publish_usertests,
    reuse_check,
)

ENV = (
    "STORE_INDEX_DIR",
    "SYSTEM_DIR",
    "WORKBENCH_DIR",
    "WORKERS_DIR",
    "MIRRORS_DIR",
    "VENDOR_DIR",
)
RELEASE = "7.1.0-vanilla-abcdef123456"


def _clear_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def _index(monkeypatch, tmp_path):
    index = tmp_path / "store-index"
    index.mkdir()
    monkeypatch.setenv("STORE_INDEX_DIR", str(index))
    return index


def test_fragment_merge_order_is_canonical():
    shuffled = [
        "builtin/fs/xfs.config",
        "unknown/zz.config",
        "debug/kasan.config",
        "core/localversion.config",
        "fs/xfs.config",
        "core/64bit.config",
        "builtin/core/modules.config",
        "core/core.config",
        "net/tls.config",
    ]
    assert sorted(shuffled, key=configure_fragments._sort_key) == [
        "core/64bit.config",
        "core/core.config",
        "core/localversion.config",
        "fs/xfs.config",
        "net/tls.config",
        "debug/kasan.config",
        "unknown/zz.config",
        "builtin/core/modules.config",
        "builtin/fs/xfs.config",
    ]


def test_fragment_order_breaks_ties_by_name():
    frags = ["fs/btrfs.config", "fs/xfs.config"]
    assert sorted(reversed(frags), key=configure_fragments._sort_key) == frags


def _vendor(monkeypatch, tmp_path):
    vendor = tmp_path / "vendor"
    monkeypatch.setenv("VENDOR_DIR", str(vendor))
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path / "workers"))
    return vendor


def test_configure_fragments_requires_the_library(monkeypatch, tmp_path):
    _vendor(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError, match="fragment library missing"):
        configure_fragments.main("wt", "build", fragments=["core/core.config"])


def test_configure_fragments_requires_a_selection(monkeypatch, tmp_path):
    vendor = _vendor(monkeypatch, tmp_path)
    (vendor / "linux-config-fragments/kernel/configs").mkdir(parents=True)
    with pytest.raises(ValueError, match="at least one fragment"):
        configure_fragments.main("wt", "build", fragments=[])


def test_configure_fragments_rejects_a_missing_fragment(monkeypatch, tmp_path):
    vendor = _vendor(monkeypatch, tmp_path)
    (vendor / "linux-config-fragments/kernel/configs").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="fragment not found"):
        configure_fragments.main("wt", "build", fragments=["core/nope.config"])


def test_resolve_preset_finds_a_library_preset(monkeypatch, tmp_path):
    vendor = _vendor(monkeypatch, tmp_path)
    defconfigs = vendor / "linux-config-fragments/defconfigs"
    defconfigs.mkdir(parents=True)
    (defconfigs / "imageless_defconfig").write_text("CONFIG_A=y\n")
    out = configure_preset._resolve_preset(tmp_path / "workers", "imageless_defconfig")
    assert out == (defconfigs / "imageless_defconfig").resolve()


def test_resolve_preset_rejects_a_path_escape(monkeypatch, tmp_path):
    vendor = _vendor(monkeypatch, tmp_path)
    (vendor / "linux-config-fragments/defconfigs").mkdir(parents=True)
    with pytest.raises(ValueError, match="resolves outside"):
        configure_preset._resolve_preset(tmp_path / "workers", "../../etc/passwd")


def test_resolve_preset_lists_what_it_has(monkeypatch, tmp_path):
    vendor = _vendor(monkeypatch, tmp_path)
    defconfigs = vendor / "linux-config-fragments/defconfigs"
    defconfigs.mkdir(parents=True)
    (defconfigs / "imageless_defconfig").write_text("")
    with pytest.raises(FileNotFoundError, match="have: imageless_defconfig"):
        configure_preset._resolve_preset(tmp_path / "workers", "nope")


def test_configure_make_rejects_empty_goals(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="no config goals"):
        configure_make.main(str(tmp_path), str(tmp_path / "b"), defconfig=[""])


def test_usertests_catalog_names_the_harness_binaries():
    assert list(build_usertests.CATALOG) == [
        "radix-tree",
        "vma",
        "memblock",
        "scatterlist",
    ]
    assert build_usertests.CATALOG["radix-tree"] == [
        "main",
        "xarray",
        "maple",
        "idr-test",
        "multiorder",
    ]


def test_effective_harnesses_default_dedupe_and_reject():
    assert build_usertests._effective_harnesses(None) == list(build_usertests.CATALOG)
    assert build_usertests._effective_harnesses(["vma", "vma", "memblock", ""]) == [
        "vma",
        "memblock",
    ]
    with pytest.raises(ValueError, match="unknown usertests harness"):
        build_usertests._effective_harnesses(["nope"])


def test_build_usertests_reuse_short_circuits(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    index = _index(monkeypatch, tmp_path)
    target = tmp_path / "published"
    target.mkdir()
    (index / f"usertests-{RELEASE}").symlink_to(target)
    out = build_usertests.main("", "", reuse_present=True, uts_release=RELEASE)
    assert out == {
        "install_dir": "",
        "reused": True,
        "name": f"usertests-{RELEASE}",
        "store_path": os.path.realpath(target),
    }


def test_effective_targets_default_extra_and_dedupe():
    eff = build_selftests._effective_targets(None, "")
    assert eff == build_selftests.DEFAULT_TARGETS
    eff = build_selftests._effective_targets(["size"], "cgroup net/forwarding size")
    assert eff == ["size", "cgroup", "net/forwarding"]


@pytest.mark.parametrize("bad", ["$(x)", "a/../b", "a//b", "...", "a/...", ""])
def test_effective_targets_rejects_unsafe_collections(bad):
    with pytest.raises(ValueError, match="invalid selftests collection"):
        build_selftests._effective_targets([bad], "")


def test_build_selftests_reuse_short_circuits(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    index = _index(monkeypatch, tmp_path)
    target = tmp_path / "published"
    target.mkdir()
    (index / f"kselftests-{RELEASE}").symlink_to(target)
    out = build_selftests.main("", "", reuse_present=True, uts_release=RELEASE)
    assert out == {
        "install_dir": "",
        "reused": True,
        "name": f"kselftests-{RELEASE}",
        "store_path": os.path.realpath(target),
    }


def test_build_info_reads_the_generated_headers(tmp_path):
    gen = tmp_path / "include/generated"
    gen.mkdir(parents=True)
    (gen / "utsrelease.h").write_text(f'#define UTS_RELEASE "{RELEASE}"\n')
    (gen / "compile.h").write_text(
        '#define LINUX_COMPILE_BY "kdevops"\n'
        '#define LINUX_COMPILE_HOST "kdevops"\n'
        '#define LINUX_COMPILER "gcc (GCC) 14.2.0"\n'
        '#define UTS_MACHINE "x86_64"\n'
        "#define LINUX_COMPILE_IRRELEVANT 7\n"
        '#define SOMETHING_ELSE "ignored"\n'
    )
    (gen / "utsversion.h").write_text(
        '#define UTS_VERSION "#1 SMP Sun Aug 25 20:57:08 UTC 1991"\n'
    )
    assert compile_step._build_info(tmp_path) == {
        "uts_release": RELEASE,
        "linux_compiler": "gcc (GCC) 14.2.0",
        "uts_machine": "x86_64",
        "linux_compile_by": "kdevops",
        "linux_compile_host": "kdevops",
        "uts_version": "#1 SMP Sun Aug 25 20:57:08 UTC 1991",
    }


def test_build_info_degrades_to_none(tmp_path):
    info = compile_step._build_info(tmp_path)
    assert set(info.values()) == {None}
    assert set(info) == set(compile_step._BUILD_INFO.values())


def test_devel_stage_filter_keeps_only_the_index(tmp_path):
    build = tmp_path / "build"
    (build / "scripts").mkdir(parents=True)
    (build / "tools").mkdir()
    (build / "drivers/scripts").mkdir(parents=True)
    (build / "include/config").mkdir(parents=True)
    (build / "include/generated").mkdir()
    (build / "rust/bindings").mkdir(parents=True)
    (build / "arch/x86/entry/vdso").mkdir(parents=True)
    for rel in (
        "main.cmd",
        "autoconf.h",
        "gen.c",
        ".config",
        "vmlinux",
        "System.map",
        "scripts/x.cmd",
        "tools/y.h",
        "drivers/obj.o",
        "drivers/d.cmd",
        "drivers/scripts/z.cmd",
        "include/config/auto.conf",
        "include/generated/rustc_cfg",
        "rust/bindings/bindings_generated.rs",
        "rust/libmacros.so",
        "arch/x86/entry/vdso/vdso64.so",
    ):
        (build / rel).write_text("")
    (build / "source").symlink_to(tmp_path / "nowhere")
    stage = tmp_path / "stage"
    shutil.copytree(
        build,
        stage,
        symlinks=True,
        ignore=store.subset_filter(
            str(build), publish_devel._DEVEL_KEEP, publish_devel._DROP_TREES
        ),
    )
    kept = sorted(str(p.relative_to(stage)) for p in stage.rglob("*"))
    assert kept == [
        ".config",
        "arch",
        "arch/x86",
        "arch/x86/entry",
        "arch/x86/entry/vdso",
        "autoconf.h",
        "drivers",
        "drivers/d.cmd",
        "drivers/scripts",
        "drivers/scripts/z.cmd",
        "gen.c",
        "include",
        "include/config",
        "include/config/auto.conf",
        "include/generated",
        "include/generated/rustc_cfg",
        "main.cmd",
        "rust",
        "rust/bindings",
        "rust/bindings/bindings_generated.rs",
        "source",
    ]


def _run_layer(root, release):
    (root / "boot").mkdir(parents=True)
    (root / "boot" / f"bzImage-{release}").write_text("")
    (root / "boot" / f"System.map-{release}").write_text("")
    (root / "boot" / f"config-{release}").write_text("")
    (root / "lib/modules" / release).mkdir(parents=True)


def test_reuse_check_absent_identity(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    dest = tmp_path / "dest"
    dest.mkdir()
    assert reuse_check.main(str(dest), RELEASE) == {
        "present": False,
        "devel_present": False,
        "uts_release": RELEASE,
        "bzImage": None,
        "boot": None,
        "modules": None,
        "destdir": str(dest),
    }


def test_reuse_check_finds_the_local_install(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    dest = tmp_path / "dest"
    _run_layer(dest, RELEASE)
    assert reuse_check.main(str(dest), RELEASE) == {
        "present": True,
        "devel_present": False,
        "uts_release": RELEASE,
        "bzImage": str(dest / "boot" / f"bzImage-{RELEASE}"),
        "boot": str(dest / "boot"),
        "modules": str(dest / "lib/modules"),
        "destdir": str(dest),
    }


def test_reuse_check_sidecars_alone_are_not_an_image(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    dest = tmp_path / "dest"
    (dest / "boot").mkdir(parents=True)
    (dest / "boot" / f"System.map-{RELEASE}").write_text("")
    (dest / "boot" / f"config-{RELEASE}").write_text("")
    (dest / "lib/modules" / RELEASE).mkdir(parents=True)
    out = reuse_check.main(str(dest), RELEASE)
    assert out["present"] is False
    assert out["bzImage"] is None
    assert out["modules"] == str(dest / "lib/modules")


def test_reuse_check_falls_back_to_the_store(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    index = _index(monkeypatch, tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()
    target = tmp_path / "published"
    _run_layer(target, RELEASE)
    (index / f"kernel-{RELEASE}").symlink_to(target)
    real = Path(os.path.realpath(target))
    assert reuse_check.main(str(dest), RELEASE) == {
        "present": True,
        "devel_present": False,
        "uts_release": RELEASE,
        "bzImage": str(real / "boot" / f"bzImage-{RELEASE}"),
        "boot": str(real / "boot"),
        "modules": str(real / "lib/modules"),
        "destdir": str(dest),
    }


def test_fetch_identity_peer_fetch_off(monkeypatch):
    _clear_env(monkeypatch)
    assert fetch_identity.main("/dest", RELEASE, use_peers=False) == {
        "fetched": False,
        "uts_release": RELEASE,
        "destdir": "/dest",
    }


def test_fetch_identity_without_a_peer_builds_locally(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path))
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    assert fetch_identity.main("/dest", RELEASE) == {
        "fetched": False,
        "uts_release": RELEASE,
        "destdir": "/dest",
    }


def _worktree(tmp_path):
    wt = tmp_path / "wt"
    gen = wt / "scripts/clang-tools/gen_compile_commands.py"
    gen.parent.mkdir(parents=True)
    gen.write_text("")
    return wt


def test_fetch_devel_requires_a_checkout(tmp_path):
    with pytest.raises(FileNotFoundError, match="no kernel source checkout"):
        fetch_devel.main(str(tmp_path / "wt"), RELEASE)


def test_fetch_devel_rejects_a_build_dir_outside_the_worktree(tmp_path):
    wt = _worktree(tmp_path)
    with pytest.raises(ValueError, match="must live under the worktree"):
        fetch_devel.main(str(wt), RELEASE, build_dir=str(tmp_path / "elsewhere"))


def test_fetch_devel_without_a_layer_reports_not_fetched(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path))
    wt = _worktree(tmp_path)
    assert fetch_devel.main(str(wt), RELEASE) == {
        "fetched": False,
        "worktree": str(wt),
        "build_dir": str(wt / "build"),
        "uts_release": RELEASE,
    }
    assert (wt / "build").is_dir()


@pytest.mark.parametrize(
    ("module", "prefix"),
    [(publish_selftests, "kselftests"), (publish_usertests, "usertests")],
)
def test_publish_suite_reuses_the_index_entry(monkeypatch, tmp_path, module, prefix):
    _clear_env(monkeypatch)
    index = _index(monkeypatch, tmp_path)
    target = tmp_path / "published"
    target.mkdir()
    (index / f"{prefix}-{RELEASE}").symlink_to(target)
    assert module.main("", RELEASE) == {
        "name": f"{prefix}-{RELEASE}",
        "store_path": os.path.realpath(target),
        "uts_release": RELEASE,
        "reused": True,
    }


@pytest.mark.parametrize("module", [publish_selftests, publish_usertests])
def test_publish_suite_without_anything_raises(monkeypatch, module):
    _clear_env(monkeypatch)
    with pytest.raises(RuntimeError, match="nothing to publish or reuse"):
        module.main("", RELEASE)
