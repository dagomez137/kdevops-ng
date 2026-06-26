# Custom Windmill server built from the dagomez137 fork (branch
# integration/fixes), carrying downstream frontend patches not yet upstream.
# Modeled on the nixpkgs `windmill` derivation: the frontend is a separate
# buildNpmPackage FOD embedded into the Rust binary via the static_frontend
# Cargo feature (env.FRONTEND_BUILD_DIR), so a frontend change is a full rebuild.
#
# Feature set is oss_core + all_languages (NOT no_auth, so authentication stays
# on, and NOT ce/private, which would pull the EE-private repo). all_languages
# enables every language worker; oracledb is gated behind withOracle because the
# `oracle` crate links the unfree Oracle Instant Client.
{
  lib,
  callPackage,
  rustPlatform,
  fetchFromGitHub,
  buildNpmPackage,
  bash,
  cmake,
  cairo,
  deno,
  go,
  lld,
  makeWrapper,
  nsjail,
  openssl,
  pango,
  pixman,
  pkg-config,
  python312,
  rustfmt,
  stdenv,
  perl,
  librusty_v8 ? (
    callPackage ./librusty_v8.nix {
      inherit (callPackage ./fetchers.nix { }) fetchLibrustyV8;
    }
  ),
  ui_builder ? (callPackage ./ui_builder.nix { }),
  libxml2,
  xmlsec,
  libxslt,
  flock,
  powershell,
  uv,
  bun,
  dotnet-sdk_9,
  php,
  procps,
  cargo,
  coreutils,
  mold, # the fork's .cargo/config.toml links release builds with -fuse-ld=mold
  # all_languages additions beyond the nixpkgs-wired set.
  krb5, # mssql-kerberos: tiberius integrated-auth-gssapi links libgssapi
  jdk, # java executor runtime
  coursier, # java dependency fetcher windmill shells out to
  ruby, # ruby executor runtime
  R, # rlang executor runtime (Rscript)
  nushell, # nu executor runtime
  duckdb, # duckdb is dlopen'd (libloading) at runtime
  git, # several executors shell out to git
  oracle-instantclient ? null, # oracledb: build links libclntsh (unfree)
  withOracle ? false,
  withEnterpriseFeatures ? false,
  withClosedSourceFeatures ? false,
  nixosTests,
}:

let
  pname = "windmill";
  version = "1.741.0";

  src = fetchFromGitHub {
    owner = "dagomez137";
    repo = "windmill";
    rev = "c60d32371c98a02a7b8f794bc5786e02d9ae07bb";
    hash = "sha256-xCVJRARjqMbOflmKznnLb09MAlveHgJ2EIUe8Uhtrb4=";
  };

  # all_languages minus oracledb; oracledb is appended only when withOracle.
  languageFeatures = [
    "python"
    "deno_core"
    "rust"
    "mysql"
    "duckdb"
    "mssql-kerberos"
    "bigquery"
    "csharp"
    "nu"
    "php"
    "java"
    "ruby"
    "rlang"
  ]
  ++ lib.optional withOracle "oracledb";
in
rustPlatform.buildRustPackage (finalAttrs: {
  inherit pname version src;
  sourceRoot = "${src.name}/backend";

  env = {
    SQLX_OFFLINE = "true";
    FRONTEND_BUILD_DIR = "${finalAttrs.passthru.web-ui}/share/windmill-frontend";
    RUSTY_V8_ARCHIVE = librusty_v8;
    # The fork's .cargo/config.toml forces [build] incremental = true, which
    # splits every crate into up to 256 codegen units. With all_languages that
    # is ~13.8k object files on the final link command, over ARG_MAX. Incremental
    # is also pointless in a clean nix build (no reuse). The env var overrides
    # the config-file setting.
    CARGO_INCREMENTAL = "0";
  };

  cargoHash = "sha256-e0HZPedUqR/3mcBt2+6DE5mfNVCgS6bdSUmDuHFZTxU=";

  # oss_core is the full open-source surface (static_frontend, mcp, oauth2, the
  # triggers, smtp, embedding, parquet, quickjs, bedrock, run_inline, ...);
  # the language features add every language worker. We pass the languages
  # explicitly rather than the all_languages meta so oracledb can be gated.
  buildFeatures = [
    "oss_core"
  ]
  ++ languageFeatures
  ++ (lib.optionals withEnterpriseFeatures [
    "enterprise_saml"
    "enterprise"
    "otel"
    "prometheus"
    "stripe"
    "tantivy"
  ])
  ++ (lib.optionals withClosedSourceFeatures [ "private" ]);

  # NixOS runtime fixes, applied as structure-independent transforms rather than
  # the line-numbered nixpkgs (1.601) patches: upstream restructured these files
  # by 1.738, so fixed-offset hunks no longer apply across the version gap. The
  # rust_executor HOME-panic patch is dropped because 1.738 already reads the
  # non-panicking HOME_ENV.
  #
  #  - stamp the real version into the build,
  #  - make uv prefer the system (nix) python rather than download managed
  #    CPython (which would not run on NixOS),
  #  - pin the offered python set to 3.12 and refuse managed installs,
  #  - mount /nix/store into every nsjail sandbox and make the host /bin//lib//usr
  #    binds non-mandatory (they are absent on NixOS); see fix-nsjail.awk.
  postPatch = ''
    substituteInPlace windmill-common/src/utils.rs \
      --replace-fail 'unknown-version' 'v${version}'

    substituteInPlace windmill-worker/src/python_executor.rs \
      --replace-fail 'only-managed' 'only-system'

    substituteInPlace windmill-worker/src/python_versions.rs \
      --replace-fail 'only-managed' 'only-system'

    substituteInPlace windmill-worker/src/python_versions.rs \
      --replace-fail \
      'pub async fn list_available_python_versions() -> Vec<Self> {' \
      'pub async fn list_available_python_versions() -> Vec<Self> { return vec![PyVAlias::Py312.into()];'

    substituteInPlace windmill-worker/src/python_versions.rs \
      --replace-fail \
      'append_logs(job_id, w_id, format!("\nINSTALLING PYTHON ({})", v), conn).await;' \
      'append_logs(job_id, w_id, format!("\nREQUESTED PYTHON INSTALL IGNORED ({})", v), conn).await; return Err(error::Error::BadConfig(format!("Python is managed through the NixOS system configuration. Change the Windmill instance setting to version 3.12")));'

    for f in windmill-worker/nsjail/*.config.proto; do
      awk -f ${./fix-nsjail.awk} "$f" > "$f.tmp" && mv "$f.tmp" "$f"
    done
  '';

  buildInputs = [
    openssl
    rustfmt
    lld
    (lib.getLib stdenv.cc.cc)
    libxml2
    xmlsec
    libxslt
    krb5
  ]
  ++ lib.optionals withOracle [ oracle-instantclient ];

  nativeBuildInputs = [
    pkg-config
    makeWrapper
    cmake # for libz-ng-sys crate
    perl
    mold
    # libgssapi-sys (mssql-kerberos) runs bindgen in its build script, which
    # needs libclang on LIBCLANG_PATH and the krb5/gssapi headers on the clang
    # search path; bindgenHook wires both.
    rustPlatform.bindgenHook
  ];

  # needs a postgres database running
  doCheck = false;

  # cargo-auditable's `cargo metadata` pass trips on windmill's dep:oauth2
  # feature syntax (the windmill crate itself compiles fine); the embedded
  # audit manifest is not needed for this deployment, so skip the pass.
  auditable = false;

  postFixup = ''
    wrapProgram "$out/bin/windmill" \
      --prefix LD_LIBRARY_PATH : ${
        lib.makeLibraryPath (
          [
            stdenv.cc.cc
            duckdb
          ]
          ++ lib.optionals withOracle [ oracle-instantclient ]
        )
      } \
      --prefix PATH : ${
        lib.makeBinPath [
          # uv searches for python on path as well!
          python312

          procps # bash_executor
          coreutils # bash_executor
        ]
      } \
      --set PYTHON_PATH "${python312}/bin/python3" \
      --set GO_PATH "${go}/bin/go" \
      --set DENO_PATH "${deno}/bin/deno" \
      --set NSJAIL_PATH "${nsjail}/bin/nsjail" \
      --set FLOCK_PATH "${flock}/bin/flock" \
      --set BASH_PATH "${bash}/bin/bash" \
      --set GIT_PATH "${git}/bin/git" \
      --set POWERSHELL_PATH "${powershell}/bin/pwsh" \
      --set BUN_PATH "${bun}/bin/bun" \
      --set UV_PATH "${uv}/bin/uv" \
      --set DOTNET_PATH "${dotnet-sdk_9}/bin/dotnet" \
      --set DOTNET_ROOT "${dotnet-sdk_9}/share/dotnet" \
      --set PHP_PATH "${php}/bin/php" \
      --set CARGO_PATH "${cargo}/bin/cargo" \
      --set JAVA_PATH "${jdk}/bin/java" \
      --set JAVAC_PATH "${jdk}/bin/javac" \
      --set COURSIER_PATH "${coursier}/bin/cs" \
      --set RUBY_PATH "${ruby}/bin/ruby" \
      --set RSCRIPT_PATH "${R}/bin/Rscript" \
      --set NU_PATH "${nushell}/bin/nu"
  '';

  passthru.web-ui = buildNpmPackage {
    inherit version src;

    pname = "windmill-ui";

    sourceRoot = "${src.name}/frontend";

    npmDepsHash = "sha256-O/h70MoRnjuL4eiFrml1kPzBEXrZ5n9D/lCpB9eOQyE=";

    # without these you get a
    # FATAL ERROR: Ineffective mark-compacts near heap limit Allocation failed - JavaScript heap out of memory
    env.NODE_OPTIONS = "--max-old-space-size=8192";

    postUnpack = ''
      cp ${src}/openflow.openapi.yaml .
    '';

    # WORKS
    npmFlags = [
      # Skip "postinstall" script that attempts to download and unpack ui-builder (patching out the url with nix-store path doesn't work)
      "--ignore-scripts"
    ];

    preBuild = ''
      npm run generate-backend-client
    '';

    buildInputs = [
      pixman
      cairo
      pango
    ];
    nativeBuildInputs = [
      pkg-config
    ];

    installPhase = ''
      mkdir -p $out/share
      mv build $out/share/windmill-frontend

      mkdir -p $out/share/windmill-frontend/static
      ln -s ${ui_builder} $out/share/windmill-frontend/static/ui_builder
    '';
  };

  passthru.tests = lib.optionalAttrs (stdenv.hostPlatform.isLinux) nixosTests.windmill;

  meta = {
    description = "Open-source developer platform to turn scripts into workflows and UIs (dagomez137 fork)";
    homepage = "https://windmill.dev";
    license = lib.licenses.agpl3Only;
    mainProgram = "windmill";
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
    ];
  };
})
