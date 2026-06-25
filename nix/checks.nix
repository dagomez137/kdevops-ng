# SPDX-License-Identifier: copyleft-next-0.3.1
{
  pkgs,
  lintSrc,
  generatedSrc,
  toolsets,
}:
let
  inherit (pkgs) runCommandLocal;
in
{
  lint = runCommandLocal "kdevops-check-lint" { nativeBuildInputs = [ pkgs.ruff ]; } ''
    cp --recursive --no-preserve=mode ${lintSrc}/. .
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
        cp --recursive --no-preserve=mode ${generatedSrc}/. .
        export PYTHONDONTWRITEBYTECODE=1
        bash scripts/check-generated.sh
        touch $out
      '';
}
