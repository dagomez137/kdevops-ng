# SPDX-License-Identifier: copyleft-next-0.3.1
#
# kvcache-bench: the V3 agentic-trace replayer, packaged so the benchmark
# binary is part of the closure (content-addressed, visible in a closure
# diff) rather than a file templated into the guest at run time. Runtime
# tunables still arrive out-of-band via the kvcache@ unit's EnvironmentFile
# and CLI flags, so the split matches fstests: tool from the closure, config
# over the share.
#
# The script lives beside this file (kvcache-bench/kvcache-bench.py); it is
# the kdevops vllm-benchmark-v3.py.j2 ported off Jinja with the review
# fixes applied (streamed TTFT, preset validation, failed-session
# accounting, /metrics scrape).
{
  lib,
  stdenvNoCC,
  makeWrapper,
  python3,
}:
let
  py = python3.withPackages (
    ps: with ps; [
      aiohttp
      datasets
      pyarrow
    ]
  );
in
# Plain mkDerivation: buildPythonApplication's fixup wraps every
# executable in bin/, the hidden unwrapped script included, and python3
# then executes a shell wrapper.
stdenvNoCC.mkDerivation {
  pname = "kvcache-bench";
  version = "0.1.0";

  src = ./kvcache-bench;

  dontBuild = true;
  installPhase = ''
    runHook preInstall
    install -Dm0755 kvcache-bench.py $out/bin/.kvcache-bench-unwrapped
    makeWrapper ${py}/bin/python3 $out/bin/kvcache-bench \
      --add-flags $out/bin/.kvcache-bench-unwrapped
    runHook postInstall
  '';
  nativeBuildInputs = [ makeWrapper ];

  meta = {
    description = "V3 agentic-trace replayer for KV cache characterisation";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "kvcache-bench";
  };
}
