# SPDX-License-Identifier: copyleft-next-0.3.1
{
  stdenvNoCC,
  runCommand,
  makeWrapper,
  nodejs,
  cacert,
}:
let
  version = "1.738.0";

  modules = stdenvNoCC.mkDerivation {
    pname = "windmill-cli-modules";
    inherit version;
    dontUnpack = true;
    nativeBuildInputs = [
      nodejs
      cacert
    ];
    buildPhase = ''
      export HOME="$TMPDIR"
      export NODE_EXTRA_CA_CERTS="${cacert}/etc/ssl/certs/ca-bundle.crt"
      npm install --global --prefix "$out" "windmill-cli@${version}"
    '';
    outputHashMode = "recursive";
    outputHashAlgo = "sha256";
    outputHash = "sha256-aUh3nSAZQ4fYXlAapGrpYHaZ+SguaMMQGpEm3m/FvyQ=";
  };
in
runCommand "windmill-cli-${version}"
  {
    nativeBuildInputs = [ makeWrapper ];
    meta = {
      description = "Windmill workspace CLI (wmill), pinned to the server version";
      mainProgram = "wmill";
    };
  }
  ''
    mkdir --parents "$out/bin"
    makeWrapper ${nodejs}/bin/node "$out/bin/wmill" \
      --add-flags "${modules}/lib/node_modules/windmill-cli/esm/main.js"
  ''
