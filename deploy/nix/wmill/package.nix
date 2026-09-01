# SPDX-License-Identifier: copyleft-next-0.3.1
# Workspace CLI (wmill) built from the same fork tree as the server, not from the
# published npm package. The fork carries downstream CLI fixes, and building both
# from one pin keeps the CLI's generated API client in step with the backend's
# openapi.yaml.
{
  stdenvNoCC,
  runCommand,
  makeWrapper,
  callPackage,
  nodejs,
  bun,
  cacert,
}:
let
  inherit (callPackage ../windmill/source.nix { }) version src;

  modules = stdenvNoCC.mkDerivation {
    pname = "windmill-cli-modules";
    inherit version src;

    nativeBuildInputs = [
      nodejs
      bun
      cacert
    ];

    # The generator scripts are `#!/usr/bin/env bash`, and the build sandbox has
    # no /usr/bin.
    postPatch = ''
      patchShebangs cli
    '';

    # Three stages, each of which reaches the network, so the whole tree is one
    # fixed-output derivation: gen_wm_client.sh writes the API client from the
    # backend's own openapi.yaml, build-npm.ts bundles src/ into esm/main.js, and
    # npm pulls the parser wasm packages that build-npm.ts deliberately keeps
    # external to the bundle.
    buildPhase = ''
      runHook preBuild
      export HOME="$TMPDIR"
      export NODE_EXTRA_CA_CERTS="${cacert}/etc/ssl/certs/ca-bundle.crt"

      # The generators reach for their tool with `npx --yes <package>@<version>`,
      # and what npx unpacks carries its own `#!/usr/bin/env node` line. Install
      # those packages here, where their interpreters can be patched, and answer
      # the scripts' `npx` with the patched copy. The specs are read back out of
      # the scripts themselves so this never pins a second version.
      npm install --global --prefix "$TMPDIR/tools" \
        $(grep --no-filename --only-matching 'npx --yes [^ ]*' \
            cli/gen_wm_client.sh cli/windmill-utils-internal/gen_wm_client.sh \
          | cut --delimiter=' ' --fields=3 | sort --unique)
      patchShebangs "$TMPDIR/tools"

      mkdir --parents "$TMPDIR/shim"
      printf '%s\n' \
        '#!/bin/sh' \
        'shift' \
        'package=$1; shift' \
        'exec "$(basename "''${package%@*}")" "$@"' \
        > "$TMPDIR/shim/npx"
      chmod +x "$TMPDIR/shim/npx"
      export PATH="$TMPDIR/shim:$TMPDIR/tools/bin:$PATH"

      cd cli
      ./gen_wm_client.sh
      ./windmill-utils-internal/gen_wm_client.sh
      bun install --frozen-lockfile
      bun run build-npm.ts
      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall
      # npm links a local directory rather than copying it, which would leave the
      # store path pointing back at the build tree; install the packed tarball.
      npm install --global --prefix "$out" "$(npm pack ./npm | tail -1)"
      runHook postInstall
    '';

    outputHashMode = "recursive";
    outputHashAlgo = "sha256";
    outputHash = "sha256-ern3QXW6/jv3LUHX3rv2zpUCQ5ZYeE+Gmm1JR7rhHnw=";
  };
in
runCommand "windmill-cli-${version}"
  {
    nativeBuildInputs = [ makeWrapper ];
    meta = {
      description = "Windmill workspace CLI (wmill), built from the pinned fork";
      mainProgram = "wmill";
    };
  }
  ''
    mkdir --parents "$out/bin"
    makeWrapper ${nodejs}/bin/node "$out/bin/wmill" \
      --add-flags "${modules}/lib/node_modules/windmill-cli/esm/main.js"
  ''
