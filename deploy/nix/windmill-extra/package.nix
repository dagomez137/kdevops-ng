# The windmill-extra LSP gateway, built from nix. Mirrors the fork's lsp/ image
# (ghcr.io/windmill-labs/windmill-extra): a Tornado websocket server that spawns
# a language server per editor session (pyright, ruff, deno, gopls, and the
# generic diagnostic-languageserver) on :3001, behind the caddy /ws* routes.
#
# Full Pipfile parity: pyright resolves imports against the python interpreter on
# PATH, so the whole upstream Pipfile is built into pyEnv and put there. The
# launcher's `pipenv run` wrapper is dropped: with pyEnv on PATH, pyright uses it
# directly, no venv needed.
{
  lib,
  python312,
  fetchPypi,
  writeShellApplication,
  runCommand,
  pyright,
  gopls,
  deno,
  go,
  shellcheck,
  ruff,
  diagnostic-languageserver,
  awscli2,
  windmill,
}:

let
  # Install the wheel directly: wmill's sdist declares the legacy
  # poetry.masonry.api build backend (full poetry, not poetry-core), and it is
  # pure python, so the prebuilt py3 wheel avoids the backend entirely.
  wmill = python312.pkgs.buildPythonPackage rec {
    pname = "wmill";
    version = "1.738.0";
    format = "wheel";
    src = fetchPypi {
      inherit pname version format;
      dist = "py3";
      python = "py3";
      hash = "sha256-Gs+IRNPHr0ntRe2B9vjONE3kmpCZM5metY8rWho60jw=";
    };
    dependencies = [ python312.pkgs.httpx ];
    doCheck = false;
    pythonImportsCheck = [ "wmill" ];
  };

  # The python environment pyright resolves imports against: the full upstream
  # Pipfile, plus the gateway's own runtime (tornado, python-lsp-jsonrpc, ujson).
  pyEnv = python312.withPackages (
    ps: with ps; [
      # gateway runtime
      tornado
      python-lsp-jsonrpc
      ujson
      cython
      httpx
      wmill
      # the Pipfile data/SaaS libraries, so in-editor autocomplete resolves them
      numpy
      pandas
      polars
      matplotlib
      seaborn
      nltk
      pyyaml
      toml
      psycopg2
      psycopg
      sqlalchemy
      requests
      urllib3
      boto3
      s3transfer
      six
      certifi
      idna
      charset-normalizer
      typing-extensions
      rsa
      cryptography
      pyparsing
      jmespath
      google-api-python-client
      google-api-core
      pyjwt
      python-dateutil
      pytz
      sendgrid
      mysql-connector
      pymongo
      slack-sdk
      yfinance
      pyowm
      pyairtable
    ]
  );

  # Drop the `pipenv run` wrapper in front of pyright-langserver: on nix the
  # interpreter pyright should use is pyEnv on PATH, not a pipenv venv.
  launcher = runCommand "pyls_launcher.py" { } ''
    substitute ${windmill.src}/lsp/pyls_launcher.py $out \
      --replace-fail '"pipenv", "run", "pyright-langserver"' '"pyright-langserver"'
  '';
in
writeShellApplication {
  name = "windmill-extra";
  runtimeInputs = [
    pyEnv
    pyright
    gopls
    go
    deno
    shellcheck
    ruff
    diagnostic-languageserver
    awscli2
  ];
  text = ''
    exec python3 ${launcher}
  '';
}
