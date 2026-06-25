# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Read-only verification, run by `nix flake check` (and so by CI). These are the
# semantically correct home for lint and drift checks, as opposed to apps: an
# app runs a program, a check verifies the source. Each runs against a writable
# copy of just the sources it needs, so tool caches and bytecode have somewhere
# to go and the whole repo is not copied into every check.
{
  pkgs,
  self,
  toolsets,
}:
let
  inherit (pkgs) runCommandLocal;
in
{
  # ruff lint plus format verification, the same rules make format applies.
  lint = runCommandLocal "kdevops-check-lint" { nativeBuildInputs = [ pkgs.ruff ]; } ''
    cp --recursive --no-preserve=mode ${self}/scripts ${self}/f ${self}/pyproject.toml .
    ruff check scripts f
    ruff format --check scripts f
    touch $out
  '';

  # The generated flow and reflowed descriptions still match their generators.
  generated =
    runCommandLocal "kdevops-check-generated"
      {
        nativeBuildInputs = [
          pkgs.bash
          toolsets.pyEnv
        ];
      }
      ''
        cp --recursive --no-preserve=mode ${self}/scripts ${self}/f .
        export PYTHONDONTWRITEBYTECODE=1
        bash scripts/check-generated.sh
        touch $out
      '';
}
