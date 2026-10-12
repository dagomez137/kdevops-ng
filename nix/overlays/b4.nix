# SPDX-License-Identifier: copyleft-next-0.3.1
#
# b4 from upstream master, ahead of the nixpkgs release. Master needs pygit2
# and liblore, which the nixpkgs recipe (0.15.2) does not list; liblore is not
# in nixpkgs, so it is packaged here from its PyPI release.
final: prev:
let
  py = final.python3Packages;

  liblore = py.buildPythonPackage rec {
    pname = "liblore";
    version = "0.10.1";
    pyproject = true;
    src = final.fetchPypi {
      inherit pname version;
      hash = "sha256-N+bXVtgQx4NRF5waNINh7ylmwWSXEcE1CuchflSX8kg=";
    };
    build-system = [ py.setuptools ];
    dependencies = [ py.requests ];
    pythonImportsCheck = [ "liblore" ];
  };
in
{
  b4 = prev.b4.overridePythonAttrs (old: {
    version = "0.17-dev-unstable-2026-10-10";
    # GitHub mirror of git.kernel.org/pub/scm/utils/b4/b4.git, same commit.
    src = final.fetchFromGitHub {
      owner = "mricon";
      repo = "b4";
      rev = "39d60a31eb6736cddc4eb7045aa453d4b4db9c65";
      hash = "sha256-wF7iz9tfOwqiefk/NMuckOt8Kl+qZytDpixKe3oNTYg=";
    };
    propagatedBuildInputs = old.propagatedBuildInputs ++ [
      py.pygit2
      py.rich
      liblore
    ];
    pythonImportsCheck = [ "b4" ];
  });
}
