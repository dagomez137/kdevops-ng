# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the build-identity digest and release baking (f.kernel.identity)."""

import hashlib
from pathlib import Path

from f.kernel import identity

TREE = "1111111111111111111111111111111111111111"
DRV = "/nix/store/aaaa-build-kernel.drv"
CONFIG = 'CONFIG_A=y\n# CONFIG_B is not set\nCONFIG_LOCALVERSION="-dirty"\n'


class _TreeGit:
    def __init__(self, tree):
        self._tree = tree

    def capture(self, *args, **kwargs):
        return self._tree + "\n"


def _pin(monkeypatch, tree=TREE, drv=DRV):
    monkeypatch.setattr(identity, "Git", lambda: _TreeGit(tree))
    monkeypatch.setattr(identity, "_toolchain", lambda: drv)


class _FakeShell:
    def __init__(self, build, version="7.1.0"):
        self._build = Path(build)
        self._version = version
        self.ran = []

    def capture(self, *args, **kwargs):
        if args[-1] == "kernelversion":
            return self._version + "\n"
        assert args[-1] == "kernelrelease"
        text = (self._build / ".config").read_text()
        return self._version + identity._localversion(text) + "\n"

    def run(self, *args, **kwargs):
        self.ran.append(args)


def _build(tmp_path, config):
    build = tmp_path / "build"
    build.mkdir(exist_ok=True)
    (build / ".config").write_text(config)
    return build


def test_main_is_a_library_marker():
    assert identity.main() == "f/kernel/identity: build-identity helper"


def test_digest_is_the_documented_sha256_prefix(monkeypatch):
    _pin(monkeypatch)
    config = 'CONFIG_A=y\nCONFIG_LOCALVERSION="-x"'
    blob = "\0".join(["CONFIG_A=y", DRV, "", TREE]).encode()
    expected = hashlib.sha256(blob).hexdigest()[:12]
    assert identity._digest(config, "/wt", "") == expected


def test_digest_is_deterministic(monkeypatch):
    _pin(monkeypatch)
    a = identity._digest(CONFIG, "/wt", "LLVM=1")
    b = identity._digest(CONFIG, "/wt", "LLVM=1")
    assert a == b
    assert len(a) == 12
    assert set(a) <= set("0123456789abcdef")


def test_digest_ignores_the_localversion_line(monkeypatch):
    _pin(monkeypatch)
    base = identity._digest(CONFIG, "/wt", "")
    relabeled = CONFIG.replace('"-dirty"', '"-other-label"')
    assert identity._digest(relabeled, "/wt", "") == base


def test_digest_ignores_the_debug_prefix_map_value(monkeypatch):
    _pin(monkeypatch)
    a = identity._digest(CONFIG, "/wt", "KCFLAGS=-fdebug-prefix-map=/hosta/x/=")
    b = identity._digest(CONFIG, "/wt", "KCFLAGS=-fdebug-prefix-map=/hostb/y/=")
    assert a == b


def test_digest_is_sensitive_to_each_input(monkeypatch):
    _pin(monkeypatch)
    base = identity._digest(CONFIG, "/wt", "LLVM=1")
    assert identity._digest(CONFIG + "CONFIG_C=m\n", "/wt", "LLVM=1") != base
    assert identity._digest(CONFIG, "/wt", "LLVM=1 W=1") != base
    _pin(monkeypatch, tree="2" * 40)
    assert identity._digest(CONFIG, "/wt", "LLVM=1") != base
    _pin(monkeypatch, drv="/nix/store/bbbb-build-kernel.drv")
    assert identity._digest(CONFIG, "/wt", "LLVM=1") != base


def test_bake_identity_release_format(monkeypatch, tmp_path):
    _pin(monkeypatch)
    config = "CONFIG_A=y\n# CONFIG_LOCALVERSION_AUTO is not set\n"
    build = _build(tmp_path, config)
    digest = identity._digest(config, str(tmp_path / "wt"), "")
    shell = _FakeShell(build)
    release = identity.bake_identity(
        shell, str(tmp_path / "wt"), str(build), "", label="vanilla"
    )
    assert release == f"7.1.0-vanilla-{digest}"


def test_bake_identity_without_a_label(monkeypatch, tmp_path):
    _pin(monkeypatch)
    config = "CONFIG_A=y\n# CONFIG_LOCALVERSION_AUTO is not set\n"
    build = _build(tmp_path, config)
    digest = identity._digest(config, str(tmp_path / "wt"), "")
    shell = _FakeShell(build)
    release = identity.bake_identity(shell, str(tmp_path / "wt"), str(build), "")
    assert release == f"7.1.0-{digest}"


def test_bake_identity_is_idempotent(monkeypatch, tmp_path):
    _pin(monkeypatch)
    build = _build(tmp_path, "CONFIG_A=y\n# CONFIG_LOCALVERSION_AUTO is not set\n")
    shell = _FakeShell(build)
    args = (shell, str(tmp_path / "wt"), str(build), "")
    first = identity.bake_identity(*args, label="vanilla")
    second = identity.bake_identity(*args, label="vanilla")
    assert second == first


def test_bake_identity_keeps_a_prior_series_prefix(monkeypatch, tmp_path):
    _pin(monkeypatch)
    config = 'CONFIG_LOCALVERSION="-myseries"\n# CONFIG_LOCALVERSION_AUTO is not set\n'
    build = _build(tmp_path, config)
    digest = identity._digest(config, str(tmp_path / "wt"), "")
    shell = _FakeShell(build)
    release = identity.bake_identity(
        shell, str(tmp_path / "wt"), str(build), "", label="vanilla"
    )
    assert release == f"7.1.0-myseries-vanilla-{digest}"


def test_bake_identity_fits_the_release_into_64_chars(monkeypatch, tmp_path):
    _pin(monkeypatch)
    build = _build(tmp_path, "CONFIG_A=y\n# CONFIG_LOCALVERSION_AUTO is not set\n")
    shell = _FakeShell(build)
    release = identity.bake_identity(
        shell, str(tmp_path / "wt"), str(build), "", label="a" * 80
    )
    assert len(release) == 64
    assert release.startswith("7.1.0-" + "a" * 45 + "-")


def test_bake_identity_forces_localversion_auto_off(monkeypatch, tmp_path):
    _pin(monkeypatch)
    build = _build(tmp_path, "CONFIG_A=y\nCONFIG_LOCALVERSION_AUTO=y\n")
    shell = _FakeShell(build)
    identity.bake_identity(shell, str(tmp_path / "wt"), str(build), "")
    text = (build / ".config").read_text()
    assert "# CONFIG_LOCALVERSION_AUTO is not set" in text
    assert "CONFIG_LOCALVERSION_AUTO=y" not in text


def test_fit_label_zero_budget_drops_the_label():
    assert identity._fit_label("anything", 0) == ""
    assert identity._fit_label("anything", -1) == ""


def test_fit_label_keeps_a_fitting_label():
    assert identity._fit_label("iomap-fixes", 20) == "iomap-fixes"


def test_fit_label_cuts_at_a_dash():
    assert identity._fit_label("iomap-consolidate-bio", 12) == "iomap"


def test_fit_label_preserves_a_revision_suffix():
    assert identity._fit_label("long-series-name-v3", 12) == "long-v3"


def test_fit_label_falls_back_to_the_bare_revision():
    assert identity._fit_label("feature-v12", 4) == "v12"


def test_fit_head_truncates_without_a_dash():
    assert identity._fit_head("abcdefgh", 4) == "abcd"


def test_fit_head_strips_trailing_separators():
    assert identity._fit_head("abc.x", 4) == "abc"


def test_fit_head_zero_budget_is_empty():
    assert identity._fit_head("abc", 0) == ""


def test_localversion_reads_the_quoted_value():
    assert identity._localversion('CONFIG_A=y\nCONFIG_LOCALVERSION="-x"\n') == "-x"
    assert identity._localversion("CONFIG_A=y\n") == ""


def test_set_localversion_replaces_and_appends(tmp_path):
    config = tmp_path / ".config"
    config.write_text('CONFIG_A=y\nCONFIG_LOCALVERSION="-old"\n')
    identity._set_localversion(config, "-new")
    assert config.read_text() == 'CONFIG_A=y\nCONFIG_LOCALVERSION="-new"\n'
    config.write_text("CONFIG_A=y\n")
    identity._set_localversion(config, "-new")
    assert config.read_text() == 'CONFIG_A=y\nCONFIG_LOCALVERSION="-new"\n'


def test_disable_localversion_auto_rewrites_and_appends(tmp_path):
    off = "# CONFIG_LOCALVERSION_AUTO is not set"
    config = tmp_path / ".config"
    config.write_text("CONFIG_LOCALVERSION_AUTO=y\nCONFIG_A=y\n")
    identity._disable_localversion_auto(config)
    assert config.read_text() == f"{off}\nCONFIG_A=y\n"
    identity._disable_localversion_auto(config)
    assert config.read_text() == f"{off}\nCONFIG_A=y\n"
    config.write_text("CONFIG_A=y\n")
    identity._disable_localversion_auto(config)
    assert config.read_text() == f"CONFIG_A=y\n{off}\n"
