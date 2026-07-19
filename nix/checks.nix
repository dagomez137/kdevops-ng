# SPDX-License-Identifier: copyleft-next-0.3.1
{
  pkgs,
  lintSrc,
  generatedSrc,
  testsSrc,
  toolsets,
}:
let
  inherit (pkgs) runCommandLocal;
in
{
  lint = runCommandLocal "kdevops-check-lint" { nativeBuildInputs = [ pkgs.ruff ]; } ''
    cp --recursive --no-preserve=mode ${lintSrc}/. .
    ruff check scripts f tests
    ruff format --check scripts f tests
    touch $out
  '';

  # The fixture tests over the f/ step modules (parsers, verdict rules, store
  # reads); pure Python, no instance and no network.
  tests =
    runCommandLocal "kdevops-check-tests"
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
