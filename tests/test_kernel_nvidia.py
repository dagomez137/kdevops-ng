# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the NVIDIA open GPU modules built with a kernel."""

import pytest

from f.kernel import nvidia, reuse_check

RELEASE = "7.3.0-rc6-demo-abc"
CLANG = (
    "LLVM=1 CC=/nix/store/x-clang/bin/clang CFLAGS_KERNEL=-I/r CFLAGS_MODULE=-I/r "
    "KBUILD_BUILD_TIMESTAMP=0 W=1 KCFLAGS=-Werror"
)


def test_toolchain_flags_keep_only_the_kernels_compiler():
    assert nvidia.toolchain_flags(
        "'CC=ccache gcc' CFLAGS_MODULE=-I/r KBUILD_BUILD_TIMESTAMP=0 W=1"
    ) == ["CC=ccache gcc", "CFLAGS_MODULE=-I/r", "CXX=g++"]


def test_toolchain_flags_name_gcc_for_a_gcc_kernel():
    assert nvidia.toolchain_flags("W=1") == ["CC=gcc", "CXX=g++"]


@pytest.mark.parametrize("flags", [CLANG, "CC=clang", "LLVM=1"])
def test_toolchain_flags_refuse_a_clang_kernel(flags):
    with pytest.raises(ValueError, match="GCC kernels only"):
        nvidia.toolchain_flags(flags)


def test_missing_config_names_what_the_modules_link_against(tmp_path):
    (tmp_path / ".config").write_text("CONFIG_MODULES=y\n# CONFIG_DRM is not set\n")
    assert nvidia.missing_config(str(tmp_path)) == ["CONFIG_DMA_SHARED_BUFFER"]
    (tmp_path / ".config").write_text(
        "CONFIG_MODULES=y\nCONFIG_SYNC_FILE=y\nCONFIG_DMA_SHARED_BUFFER=y\n"
    )
    assert nvidia.missing_config(str(tmp_path)) == []


def _run_layer(root, with_nvidia: bool):
    (root / "boot").mkdir(parents=True)
    (root / f"boot/vmlinuz-{RELEASE}").write_text("image")
    mods = root / "lib/modules" / RELEASE
    (mods / "kernel/drivers/video").mkdir(parents=True)
    (mods / "modules.dep").write_text("")
    if with_nvidia:
        (mods / "kernel/drivers/video/nvidia.ko").write_text("ko")


@pytest.fixture
def driver(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKERS_DIR", str(tmp_path / "workers"))
    monkeypatch.setattr(
        nvidia, "source", lambda workers: ("/nix/store/src", "615.71.09")
    )
    monkeypatch.setattr(reuse_check.store, "local_path", lambda name: None)
    versions = {}
    monkeypatch.setattr(
        nvidia,
        "installed_version",
        lambda root, rel, workers: versions.get(str(root)),
    )
    return versions


def test_reuse_without_nvidia_ignores_the_modules(driver, tmp_path):
    _run_layer(tmp_path / "d", with_nvidia=False)
    assert reuse_check.main(str(tmp_path / "d"), RELEASE)["present"] is True


def test_reuse_with_nvidia_rebuilds_an_identity_without_them(driver, tmp_path):
    _run_layer(tmp_path / "d", with_nvidia=False)
    out = reuse_check.main(str(tmp_path / "d"), RELEASE, nvidia=True)
    assert out["present"] is False


def test_reuse_with_nvidia_rebuilds_another_release(driver, tmp_path):
    _run_layer(tmp_path / "d", with_nvidia=True)
    driver[str(tmp_path / "d")] = "610.57.04"
    out = reuse_check.main(str(tmp_path / "d"), RELEASE, nvidia=True)
    assert (out["present"], out["nvidia_version"]) == (False, "610.57.04")


def test_reuse_with_nvidia_keeps_the_same_release(driver, tmp_path):
    _run_layer(tmp_path / "d", with_nvidia=True)
    driver[str(tmp_path / "d")] = "615.71.09"
    out = reuse_check.main(str(tmp_path / "d"), RELEASE, nvidia=True)
    assert (out["present"], out["nvidia_version"]) == (True, "615.71.09")


def test_reuse_refuses_a_kernel_without_dma_buf_before_the_compile(driver, tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "b/.config").write_text("CONFIG_MODULES=y\n")
    with pytest.raises(ValueError, match="CONFIG_DMA_SHARED_BUFFER"):
        reuse_check.main(
            str(tmp_path / "d"), RELEASE, nvidia=True, build_dir=str(tmp_path / "b")
        )


def test_reuse_refuses_a_clang_kernel_before_the_compile(driver, tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "b/.config").write_text(
        "CONFIG_MODULES=y\nCONFIG_DMA_SHARED_BUFFER=y\n"
    )
    with pytest.raises(ValueError, match="GCC kernels only"):
        reuse_check.main(
            str(tmp_path / "d"),
            RELEASE,
            nvidia=True,
            build_dir=str(tmp_path / "b"),
            make_flags=CLANG,
        )


def test_reuse_checks_nothing_for_a_present_identity(driver, tmp_path):
    _run_layer(tmp_path / "d", with_nvidia=True)
    driver[str(tmp_path / "d")] = "615.71.09"
    out = reuse_check.main(
        str(tmp_path / "d"),
        RELEASE,
        nvidia=True,
        build_dir=str(tmp_path / "missing"),
        make_flags=CLANG,
    )
    assert out["present"] is True
