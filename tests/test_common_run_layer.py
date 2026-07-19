# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the install-root run-layer resolution (`f.common.run_layer`)."""

from f.common import run_layer

RELEASE = "6.9.0-test"


def _kernel_root(tmp_path, *, image=True, modules=True):
    boot = tmp_path / "boot"
    boot.mkdir()
    if image:
        (boot / f"vmlinuz-{RELEASE}").write_text("")
    (boot / f"System.map-{RELEASE}").write_text("")
    (boot / f"config-{RELEASE}").write_text("")
    if modules:
        (tmp_path / "lib/modules" / RELEASE).mkdir(parents=True)
    return tmp_path


def test_kernel_run_layer_resolves_image_and_modules(tmp_path):
    root = _kernel_root(tmp_path)
    image, has_modules = run_layer.kernel_run_layer(str(root), RELEASE)
    assert image == str(root / "boot" / f"vmlinuz-{RELEASE}")
    assert has_modules is True


def test_kernel_siblings_never_count_as_the_image(tmp_path):
    root = _kernel_root(tmp_path, image=False)
    image, has_modules = run_layer.kernel_run_layer(str(root), RELEASE)
    assert image is None
    assert has_modules is True


def test_kernel_release_mismatch_degrades(tmp_path):
    root = _kernel_root(tmp_path)
    image, has_modules = run_layer.kernel_run_layer(str(root), "6.10.0-other")
    assert (image, has_modules) == (None, False)


def test_kernel_missing_boot_dir_degrades(tmp_path):
    assert run_layer.kernel_run_layer(str(tmp_path), RELEASE) == (None, False)


def test_kernel_missing_modules_flags_them_absent(tmp_path):
    root = _kernel_root(tmp_path, modules=False)
    image, has_modules = run_layer.kernel_run_layer(str(root), RELEASE)
    assert image is not None
    assert has_modules is False


def test_kernel_picks_the_first_image_in_sort_order(tmp_path):
    root = _kernel_root(tmp_path)
    (root / "boot" / f"bzImage-{RELEASE}").write_text("")
    image, _ = run_layer.kernel_run_layer(str(root), RELEASE)
    assert image == str(root / "boot" / f"bzImage-{RELEASE}")


def test_qemu_emulators_resolve_sorted(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("qemu-system-x86_64", "qemu-system-aarch64", "qemu-img"):
        (bindir / name).write_text("")
    assert run_layer.qemu_emulators(str(tmp_path)) == [
        bindir / "qemu-system-aarch64",
        bindir / "qemu-system-x86_64",
    ]


def test_qemu_missing_bin_dir_degrades(tmp_path):
    assert run_layer.qemu_emulators(str(tmp_path)) == []


def test_qemu_empty_bin_dir_degrades(tmp_path):
    (tmp_path / "bin").mkdir()
    assert run_layer.qemu_emulators(str(tmp_path)) == []
