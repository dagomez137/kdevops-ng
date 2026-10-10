# SPDX-License-Identifier: copyleft-next-0.3.1
#
# kvcache-bench: replays recorded multi-turn agent sessions (the
# sammshen/lmcache-agentic-traces dataset by default) against an
# OpenAI-compatible server and summarises time to first token, latency and
# the server's KV cache counters. The script beside this file is the whole
# tool; see its docstring for the dataset contract and the presets.
#
# It talks to the server over HTTP only, so it runs on the release
# channel's Python whatever the server is built from.
#
# Dataset: https://huggingface.co/datasets/sammshen/lmcache-agentic-traces
{
  lib,
  stdenvNoCC,
  python3,
  makeWrapper,
}:

let
  python = python3.withPackages (ps: [
    ps.aiohttp
    ps.datasets
  ]);
in
stdenvNoCC.mkDerivation {
  name = "kvcache-bench";

  src = ./kvcache-bench;

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall
    install -D --mode=0755 kvcache-bench.py $out/libexec/kvcache-bench.py
    makeWrapper ${python.interpreter} $out/bin/kvcache-bench \
      --add-flags $out/libexec/kvcache-bench.py
    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    $out/bin/kvcache-bench --list-presets
  '';

  meta = {
    description = "Replay recorded agent sessions to characterise an LLM server's KV cache";
    # nixpkgs has no entry for the license the script carries.
    license = {
      spdxId = "copyleft-next-0.3.1";
      fullName = "copyleft-next 0.3.1";
      url = "https://github.com/copyleft-next/copyleft-next/blob/master/Releases/copyleft-next-0.3.1";
      free = true;
    };
    platforms = lib.platforms.linux;
    mainProgram = "kvcache-bench";
    maintainers = [ ];
  };
}
