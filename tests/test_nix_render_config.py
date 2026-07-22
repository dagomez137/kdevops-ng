# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the per-VM imageless config renderer (`f.nix.render_config`)."""

from pathlib import Path

import pytest

from f.nix import render_config
from f.nix.render_config import (
    _nix_str,
    _override_input,
    _reject_unknown,
    _render_flake,
)

REPO = Path(__file__).resolve().parent.parent

TEMPLATE = """\
{
  inputs = {
    nixos-flake.url = "path:/path/to/nixos-flake";
    nixpkgs.follows = "nixos-flake/nixpkgs";
  };
}
"""

ENV = ("STORE_INDEX_DIR", "SYSTEM_DIR", "WORKBENCH_DIR", "WORKERS_DIR", "MIRRORS_DIR")


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    workers = tmp_path / "workbench" / "workers"
    workers.mkdir(parents=True)
    monkeypatch.setenv("WORKERS_DIR", str(workers))
    monkeypatch.setenv("WORKER_INDEX", "0")
    monkeypatch.setenv("SYSTEM_DIR", str(tmp_path / "system"))
    vendor = tmp_path / "vendor"
    imageless = vendor / "nixos-flake" / "templates" / "imageless"
    imageless.mkdir(parents=True)
    (imageless / "flake.nix").write_text(TEMPLATE)
    monkeypatch.setenv("VENDOR_DIR", str(vendor))
    return tmp_path


def test_the_curated_registries_hold_their_shape():
    suites = render_config._TEST_SUITES
    assert suites == sorted(suites)
    assert len(suites) == len(set(suites))
    assert {"fstests", "selftests", "usertests"} <= set(suites)
    assert render_config._FEATURED_TEST_SUITES == suites
    assert render_config._FEATURED_TEST_SUITES is not suites
    profiles = render_config._PROFILES
    assert set(render_config._FEATURED_PROFILES) == profiles - {
        "controller",
        "telemetry",
    }
    assert set(render_config._PROFILE_ENABLE) == {
        "monitoring",
        "controller",
        "telemetry",
    }
    for prof, opt in render_config._PROFILE_ENABLE.items():
        assert opt == f"nixos-flake.{prof}.enable"
    for pkg in render_config._OVERRIDABLE_PKGS:
        assert render_config._PKG_RE.match(pkg)
    collectors = render_config._TELEMETRY_COLLECTORS
    assert len(collectors) == len(set(collectors))
    assert "biolatency" in render_config._TELEMETRY_EBPF_CONFIGS


def test_the_vendored_template_still_carries_the_rewrite_anchors():
    text = (REPO / "vendor/nixos-flake/templates/imageless/flake.nix").read_text()
    assert render_config._TEMPLATE_PATH_PLACEHOLDER in text
    assert render_config._FOLLOWS_ANCHOR in text


def test_nix_str_escapes_the_nix_specials():
    assert _nix_str("plain") == '"plain"'
    assert _nix_str('say "hi"') == '"say \\"hi\\""'
    assert _nix_str("a\\b") == '"a\\\\b"'
    assert _nix_str("${out}") == '"\\${out}"'


def test_reject_unknown_names_the_strays():
    assert _reject_unknown("profile", ["devel"], {"devel", "monitoring"}) is None
    with pytest.raises(ValueError, match="unknown profile"):
        _reject_unknown("profile", ["devel", "gaming"], {"devel"})


def test_override_input_path_form():
    block = _override_input({"pkg": "fio", "src": "/home/me/fio"})
    assert block == "\n".join(
        [
            "    fio-src = {",
            '      type = "path";',
            '      path = "/home/me/fio";',
            "      flake = false;",
            "    };",
        ]
    )


def test_override_input_git_form_pins_ref_and_submodules():
    block = _override_input(
        {"pkg": "spdk", "src": "https://github.com/spdk/spdk.git", "ref": "master"}
    )
    assert block == "\n".join(
        [
            "    spdk-src = {",
            '      type = "git";',
            '      url = "https://github.com/spdk/spdk.git";',
            '      ref = "master";',
            "      submodules = true;",
            "      flake = false;",
            "    };",
        ]
    )


def test_override_input_git_form_without_a_ref():
    block = _override_input({"pkg": "kmod", "src": "https://example.org/kmod.git"})
    assert "ref =" not in block
    assert "      submodules = true;" in block


def test_render_flake_pins_the_vendored_path(tmp_path):
    template = tmp_path / "flake.nix"
    template.write_text(TEMPLATE)
    text = _render_flake(template, Path("/vendor/nixos-flake"), [])
    assert 'nixos-flake.url = "path:/vendor/nixos-flake";' in text
    assert render_config._TEMPLATE_PATH_PLACEHOLDER not in text


def test_render_flake_appends_override_inputs_after_the_follows_anchor(tmp_path):
    template = tmp_path / "flake.nix"
    template.write_text(TEMPLATE)
    text = _render_flake(
        template, Path("/v/nixos-flake"), [{"pkg": "fio", "src": "/s/fio"}]
    )
    anchor = render_config._FOLLOWS_ANCHOR
    assert f"{anchor}\n\n    fio-src = {{\n" in text


def test_render_flake_asserts_on_a_drifted_placeholder(tmp_path):
    template = tmp_path / "flake.nix"
    template.write_text("{ }\n")
    with pytest.raises(RuntimeError, match="no longer contains"):
        _render_flake(template, Path("/v/nixos-flake"), [])


def test_render_flake_asserts_on_a_drifted_follows_anchor(tmp_path):
    template = tmp_path / "flake.nix"
    template.write_text('nixos-flake.url = "path:/path/to/nixos-flake";\n')
    with pytest.raises(RuntimeError, match="follows anchor"):
        _render_flake(template, Path("/v/nixos-flake"), [{"pkg": "fio", "src": "/s"}])


def test_main_renders_the_featured_defaults(env):
    out = render_config.main()
    config_dir = Path(out["config_dir"])
    assert config_dir == (env / "workbench/workers/0/nix/nixos").resolve()
    flake = Path(out["flake"]).read_text()
    default = Path(out["default"]).read_text()
    assert f"path:{env / 'vendor/nixos-flake'}" in flake
    for prof in ("devel", "build-tools", "monitoring"):
        assert f"nixos-flake.nixosModules.profiles.{prof}" in default
    for suite in render_config._TEST_SUITES:
        assert f"nixos-flake.nixosModules.testSuites.{suite}" in default
    assert "nixos-flake.nixosModules.mounts.shares" in default
    assert "  nixos-flake.monitoring.enable = true;" in default
    assert "telemetry" not in default
    assert 'networking.hostName = "nixos";' in default
    assert 'nixos-flake.user.name = "kdevops";' in default
    assert 'nixos-flake.shares."/var/lib/xfstests" = { tag = "fstests"; };' in default
    assert (
        'nixos-flake.shares."/var/lib/kselftests" = { tag = "selftests"; };' in default
    )
    assert (
        'nixos-flake.shares."/var/lib/usertests" = { tag = "usertests"; };' in default
    )
    assert "authorizedKeys" not in default
    assert out["vm_name"] == "nixos"


def test_main_falls_back_to_the_default_vm_name(env):
    out = render_config.main(vm_name="")
    assert out["vm_name"] == "nixos"


def test_no_suites_renders_no_imports_or_shares(env):
    out = render_config.main(vm_name="bare", profiles=[], test_suites=[])
    default = Path(out["default"]).read_text()
    assert "imports = [" not in default
    assert "nixos-flake.shares." not in default


def test_main_renders_the_telemetry_profile_options(env):
    out = render_config.main(
        vm_name="tvm",
        profiles=["telemetry"],
        test_suites=[],
        telemetry_collectors=["buddyinfo", "zoneinfo"],
        telemetry_ebpf=True,
        telemetry_ebpf_configs=["bio-trace"],
    )
    default = Path(out["default"]).read_text()
    assert "  nixos-flake.telemetry.enable = true;" in default
    assert (
        'nixos-flake.telemetry.metrics.url = "http://10.0.2.2:9090/api/v1/write";'
        in default
    )
    assert (
        'nixos-flake.telemetry.logs.url = "http://10.0.2.2:3100/loki/api/v1/push";'
        in default
    )
    assert (
        'nixos-flake.telemetry.extraCollectors = [ "buddyinfo" "zoneinfo" ];' in default
    )
    assert "  nixos-flake.telemetry.ebpf.enable = true;" in default
    assert 'nixos-flake.telemetry.ebpf.configs = [ "bio-trace" ];' in default


def test_telemetry_ebpf_defaults_to_biolatency(env):
    out = render_config.main(
        vm_name="tvm", profiles=["telemetry"], test_suites=[], telemetry_ebpf=True
    )
    default = Path(out["default"]).read_text()
    assert 'nixos-flake.telemetry.ebpf.configs = [ "biolatency" ];' in default


def test_managed_vm_key_leads_and_dedupes_the_keys(env):
    ssh = env / "system" / "ssh"
    ssh.mkdir(parents=True)
    (ssh / "id_ed25519.pub").write_text("ssh-ed25519 AAAA vm@kdevops\n")
    out = render_config.main(
        vm_name="kvm",
        profiles=[],
        test_suites=[],
        ssh_keys=["ssh-ed25519 BBBB me@host", "ssh-ed25519 AAAA vm@kdevops"],
    )
    default = Path(out["default"]).read_text()
    keys = '[ "ssh-ed25519 AAAA vm@kdevops" "ssh-ed25519 BBBB me@host" ]'
    assert f"users.users.root.openssh.authorizedKeys.keys = {keys};" in default
    assert f"users.users.kdevops.openssh.authorizedKeys.keys = {keys};" in default


def test_home_share_mounts_the_operator_home(env, monkeypatch):
    monkeypatch.setenv("HOME", "/home/alice")
    out = render_config.main(vm_name="hvm", profiles=[], test_suites=[], home=True)
    default = Path(out["default"]).read_text()
    assert 'nixos-flake.shares."/home/alice" = { tag = "home"; };' in default
    assert 'users.users.root.home = lib.mkForce "/home/alice";' in default


def test_home_outside_slash_home_falls_back_to_kdevops(env, monkeypatch):
    monkeypatch.setenv("HOME", "/root")
    out = render_config.main(vm_name="hvm", profiles=[], test_suites=[], home=True)
    default = Path(out["default"]).read_text()
    assert 'nixos-flake.shares."/home/kdevops" = { tag = "home"; };' in default


def test_explicit_home_dir_wins_over_the_env(env, monkeypatch):
    monkeypatch.setenv("HOME", "/home/alice")
    out = render_config.main(
        vm_name="hvm", profiles=[], test_suites=[], home=True, home_dir="/home/bob"
    )
    default = Path(out["default"]).read_text()
    assert 'nixos-flake.shares."/home/bob" = { tag = "home"; };' in default
    assert 'lib.mkForce "/home/bob";' in default


def test_home_off_ignores_home_dir(env):
    out = render_config.main(
        vm_name="hvm", profiles=[], test_suites=[], home_dir="/home/bob"
    )
    default = Path(out["default"]).read_text()
    assert "mkForce" not in default
    assert '= { tag = "home"; };' not in default


def test_an_explicit_share_wins_over_the_suite_default(env):
    out = render_config.main(
        vm_name="svm",
        profiles=[],
        test_suites=["fstests"],
        shares={"/var/lib/xfstests": {"tag": "custom", "options": ["ro"]}},
    )
    default = Path(out["default"]).read_text()
    assert (
        'nixos-flake.shares."/var/lib/xfstests" = '
        '{ tag = "custom"; options = [ "ro" ]; };'
    ) in default
    assert '{ tag = "fstests"; }' not in default


def test_blank_form_rows_are_dropped(env):
    out = render_config.main(
        vm_name="fvm",
        profiles=["", "devel"],
        test_suites=["", "fstests"],
        ssh_keys=["", "   "],
        source_overrides={"xfstests": {"src": ""}},
    )
    default = Path(out["default"]).read_text()
    assert "profiles.devel" in default
    assert "profiles.build-tools" not in default
    assert "testSuites.fstests" in default
    assert "testSuites.blktests" not in default
    assert "authorizedKeys" not in default


def test_a_curated_override_lands_in_flake_and_overlay(env):
    # The curated form asks only for the source; the known xfsprogs autoreconf build
    # step is attached automatically.
    out = render_config.main(
        vm_name="ovm",
        profiles=[],
        test_suites=[],
        source_overrides={"xfsprogs": {"src": "/home/me/xfsprogs"}},
    )
    flake = Path(out["flake"]).read_text()
    default = Path(out["default"]).read_text()
    assert "    xfsprogs-src = {" in flake
    assert '      path = "/home/me/xfsprogs";' in flake
    assert "submodules" not in flake
    assert (
        "      xfsprogs = prev.xfsprogs.overrideAttrs "
        '(_: { src = inputs.xfsprogs-src; autoreconfPhase = "make configure"; });'
    ) in default


def test_source_overrides_emits_only_filled_curated_packages(env):
    out = render_config.main(
        vm_name="ovm",
        profiles=[],
        test_suites=[],
        # xfstests filled (git, with ref); fio blank; an unknown key is ignored (the
        # form only offers the curated packages, so this can't happen from the UI).
        source_overrides={
            "xfstests": {"src": "git+file:///home/me/xfstests", "ref": "for-next"},
            "fio": {"src": ""},
            "spdk": {"src": "/nope"},
        },
    )
    flake = Path(out["flake"]).read_text()
    default = Path(out["default"]).read_text()
    assert '      url = "git+file:///home/me/xfstests";' in flake
    assert '      ref = "for-next";' in flake
    assert (
        "xfstests = prev.xfstests.overrideAttrs (_: { src = inputs.xfstests-src; });"
        in default
    )
    assert "fio-src" not in flake
    assert "spdk" not in default


def test_an_extra_override_takes_any_git_package(env):
    out = render_config.main(
        vm_name="ovm",
        profiles=[],
        test_suites=[],
        extra_overrides=[
            {"pkg": "spdk", "src": "https://github.com/spdk/spdk.git", "ref": "master"}
        ],
    )
    flake = Path(out["flake"]).read_text()
    default = Path(out["default"]).read_text()
    assert '      url = "https://github.com/spdk/spdk.git";' in flake
    assert '      ref = "master";' in flake
    assert "      submodules = true;" in flake
    assert "spdk = prev.spdk.overrideAttrs (_: { src = inputs.spdk-src; });" in default


def test_invalid_vm_name_is_rejected():
    with pytest.raises(ValueError, match="invalid vm_name"):
        render_config.main(vm_name="bad name")


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError, match="unknown profile"):
        render_config.main(profiles=["gaming"], test_suites=[])


def test_unknown_test_suite_is_rejected():
    with pytest.raises(ValueError, match="unknown test_suite"):
        render_config.main(profiles=[], test_suites=["cthulhu"])


def test_unknown_collector_and_ebpf_config_are_rejected():
    with pytest.raises(ValueError, match="unknown telemetry collector"):
        render_config.main(
            profiles=[], test_suites=[], telemetry_collectors=["cpufreq"]
        )
    with pytest.raises(ValueError, match="unknown telemetry ebpf config"):
        render_config.main(
            profiles=[], test_suites=[], telemetry_ebpf_configs=["oomkill"]
        )


def test_extra_override_needs_a_src():
    with pytest.raises(ValueError, match="missing src"):
        render_config.main(
            profiles=[], test_suites=[], extra_overrides=[{"pkg": "spdk"}]
        )


def test_override_attrs_must_be_string_valued():
    with pytest.raises(ValueError, match="attrs must be a dict"):
        render_config.main(
            profiles=[],
            test_suites=[],
            extra_overrides=[{"pkg": "spdk", "src": "/s", "attrs": {"patches": ["x"]}}],
        )


def test_missing_imageless_template_raises(env):
    (env / "vendor/nixos-flake/templates/imageless/flake.nix").unlink()
    with pytest.raises(FileNotFoundError, match="imageless template"):
        render_config.main(vm_name="tvm", profiles=[], test_suites=[])
