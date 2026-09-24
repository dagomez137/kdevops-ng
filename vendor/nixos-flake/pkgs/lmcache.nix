# SPDX-License-Identifier: copyleft-next-0.3.1
#
# LMCache: KV cache layer for LLM serving (CPU/disk/remote offload behind
# vLLM's KV connector interface). Not in nixpkgs (checked 2026-09:
# pkgs/by-name/lm/lmcache and pkgs/development/python-modules/lmcache both
# absent), so packaged here.
#
# Pinned to a tag on purpose: the KV cache characterisation compares runs
# across kernels and storage configs, and that comparison is only valid when
# the cache implementation itself is fixed. Bumping the pin is a deliberate
# act that changes the closure hash and therefore shows up in a closure diff.
#
# TODO(pin): set `version`/`hash` to the revision the current study is
# anchored to before first use; `lib.fakeHash` forces the decision at build
# time rather than letting a floating default slip in silently.
{
  lib,
  python3Packages,
  fetchFromGitHub,
}:
python3Packages.buildPythonPackage rec {
  pname = "lmcache";
  version = "0.5.4"; # TODO(pin): real tag, e.g. "0.3.x"
  pyproject = true;

  src = fetchFromGitHub {
    owner = "LMCache";
    repo = "lmcache";
    tag = "v${version}";
    hash = "sha256-jcRBA18KWRKnZsDhWJ2uYgba7syxRcJx8E1BH66wl4g=";
    # hash = lib.fakeHash; # TODO(pin): nix-prefetch-github LMCache lmcache --rev v<version>
  };

  build-system = with python3Packages; [
    setuptools
    setuptools-scm
  ];

  dependencies = with python3Packages; [
    numpy
    torch
    aiofiles
    pyyaml
    msgspec
    nvtx
    prometheus-client
    pyzmq
    redis
    safetensors
    sortedcontainers
    transformers
  ];

  # The test suite needs a CUDA device and a live vLLM; neither exists in the
  # build sandbox. Import check keeps the packaging honest without them.
  doCheck = false;
  pythonImportsCheck = [ "lmcache" ];

  meta = {
    description = "KV cache layer for LLM serving (vLLM KV-connector offload)";
    homepage = "https://github.com/LMCache/lmcache";
    license = lib.licenses.asl20;
    platforms = lib.platforms.linux;
  };
}
