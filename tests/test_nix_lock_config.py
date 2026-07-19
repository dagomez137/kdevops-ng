# SPDX-License-Identifier: copyleft-next-0.3.1
"""Fixture tests for the per-VM flake source-input scan (`f.nix.lock_config`)."""

from f.nix.lock_config import _src_inputs

FLAKE = """\
{
  inputs = {
    nixos-flake.url = "path:/vendor/nixos-flake";
    nixpkgs.follows = "nixos-flake/nixpkgs";

    xfstests-src = {
      type = "path";
      path = "/home/me/src/xfstests";
      flake = false;
    };
    xfsprogs-src = {
      type = "git";
      url = "https://git.kernel.org/pub/scm/fs/xfs/xfsprogs-dev.git";
      flake = false;
    };
    #   fio-src = {
    #     type = "path";
    #   };
    xfstests-src = {
      flake = false;
    };
  };
}
"""


def test_missing_flake_yields_no_inputs(tmp_path):
    assert _src_inputs(str(tmp_path)) == []


def test_declared_src_inputs_are_found_deduped_in_order(tmp_path):
    (tmp_path / "flake.nix").write_text(FLAKE)
    assert _src_inputs(str(tmp_path)) == ["xfstests-src", "xfsprogs-src"]


def test_a_flake_without_src_inputs_yields_none(tmp_path):
    (tmp_path / "flake.nix").write_text('{ inputs.nixos-flake.url = "path:/v"; }\n')
    assert _src_inputs(str(tmp_path)) == []
