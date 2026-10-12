# SPDX-License-Identifier: copyleft-next-0.3.1
{
  pkgs,
  lintSrc,
  generatedSrc,
  testsSrc,
  nixSrc,
  shellSrc,
  apps,
  toolsets,
}:
let
  inherit (pkgs) lib runCommandLocal;
in
{
  lint = runCommandLocal "kdevops-ng-check-lint" { nativeBuildInputs = [ pkgs.ruff ]; } ''
    cp --recursive --no-preserve=mode ${lintSrc}/. .
    ruff check scripts f tests
    ruff format --check scripts f tests
    touch $out
  '';

  # The fixture tests over the f/ step modules (parsers, verdict rules, store
  # reads); pure Python, no instance and no network.
  tests =
    runCommandLocal "kdevops-ng-check-tests"
      {
        nativeBuildInputs = [ toolsets.pyEnv ];
      }
      ''
        cp --recursive --no-preserve=mode ${testsSrc}/. .
        export PYTHONDONTWRITEBYTECODE=1
        pytest tests
        touch $out
      '';

  # The generated flow and reflowed descriptions still match their generators.
  generated =
    runCommandLocal "kdevops-ng-check-generated"
      {
        nativeBuildInputs = [
          pkgs.bash
          toolsets.pyEnv
        ];
      }
      ''
        cp --recursive --no-preserve=mode ${generatedSrc}/. .
        export PYTHONDONTWRITEBYTECODE=1
        bash scripts/check-generated.sh
        touch $out
      '';

  # statix and deadnix over the root and deploy Nix; the deploy backend owns
  # its formatting (treefmt skips it), so nixfmt checks it here. vendor/ is
  # each project's own (scripts/check-vendor.sh).
  nix-lint =
    runCommandLocal "kdevops-ng-check-nix-lint"
      {
        nativeBuildInputs = [
          pkgs.statix
          pkgs.deadnix
          pkgs.nixfmt
        ];
      }
      ''
        cd ${nixSrc}
        statix check .
        deadnix --fail .
        nixfmt --check $(find deploy -name '*.nix')
        touch $out
      '';

  shellcheck =
    runCommandLocal "kdevops-ng-check-shellcheck" { nativeBuildInputs = [ pkgs.shellcheck ]; }
      ''
        cd ${shellSrc}
        shellcheck --external-sources $(find . -type f)
        touch $out
      '';

  # Building every app runs writeShellApplication's own shellcheck on it.
  apps = pkgs.linkFarm "kdevops-ng-check-apps" (
    lib.mapAttrsToList (name: app: {
      inherit name;
      path = app.program;
    }) apps
  );
}
