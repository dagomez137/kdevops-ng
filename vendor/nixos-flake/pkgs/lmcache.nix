# SPDX-License-Identifier: copyleft-next-0.3.1
#
# LMCache: a KV cache layer for LLM serving engines. It offloads the KV
# cache an engine computes to CPU memory, local disk or remote stores and
# serves it back, and plugs into vLLM as the LMCacheConnectorV1 KV
# connector, so LMCache must be importable from the Python environment
# vLLM runs in.
#
# It is called from the nixos-unstable Python 3.13 set (pkgsUnstable): the
# release's build pins torch 2.13, which no release channel carries yet,
# and it declares Python below 3.14.
#
# The common C++ extensions (lmcache_native, lmcache_redis, lmcache_fs)
# are always built against torch's cpp_extension. The CUDA kernels
# (lmcache.cuda_ops) follow torch: built with nvcc when torch has CUDA,
# skipped with NO_GPU_EXT otherwise, so the package is the same recipe on
# a GPU guest and a CPU-only one.
#
# The build pins its build-system requirements to exact versions for the
# release wheels. Those pins are stripped by pattern rather than by exact
# string, so a source override to another ref still builds. Three runtime
# requirements are absent from nixpkgs and optional at runtime: nvtx and
# cufile-python are imported behind ImportError fallbacks, and the CUDA
# bindings are reached through cuda-bindings, the module cuda-python
# re-exports. setuptools and setuptools_scm are listed as runtime
# requirements but never imported.
#
# Source: https://github.com/LMCache/LMCache
{
  lib,
  buildPythonPackage,
  fetchFromGitHub,

  # build-system
  grpcio-tools,
  ninja,
  packaging,
  setuptools,
  setuptools-scm,
  wheel,

  # dependencies
  aiofile,
  aiofiles,
  aiohttp,
  awscrt,
  blake3,
  cachetools,
  cryptography,
  fastapi,
  google-api-core,
  google-cloud-bigtable,
  grpcio,
  httptools,
  httpx,
  huggingface-hub,
  msgspec,
  numba,
  numpy,
  opentelemetry-api,
  opentelemetry-exporter-otlp,
  opentelemetry-exporter-prometheus,
  opentelemetry-sdk,
  prometheus-client,
  protobuf,
  psutil,
  py-cpuinfo,
  pyyaml,
  pyzmq,
  redis,
  safetensors,
  sortedcontainers,
  torch,
  transformers,
  uvicorn,

  # CUDA dependencies
  cuda-bindings,
  cupy,
  nixl,
  nvidia-ml-py,
  ray,
  torchvision,
  xformers,
}:

let
  inherit (torch) cudaCapabilities cudaPackages cudaSupport;
in
buildPythonPackage.override
  { stdenv = if cudaSupport then cudaPackages.backendStdenv else torch.stdenv; }
  (finalAttrs: {
    pname = "lmcache";
    version = "0.5.5";
    pyproject = true;

    src = fetchFromGitHub {
      owner = "LMCache";
      repo = "LMCache";
      tag = "v${finalAttrs.version}";
      hash = "sha256-fz6wBevgaGlz6UIIVgPiKbvAKULa5LHEKr8P0agTTtE=";
    };

    postPatch = ''
      sed --in-place --regexp-extended \
        '/^requires = \[/,/^\]/ s/"([A-Za-z0-9_.-]+)[=<>!~][^"]*"/"\1"/' \
        pyproject.toml
    '';

    build-system = [
      grpcio-tools
      ninja
      packaging
      setuptools
      setuptools-scm
      torch
      wheel
    ];

    nativeBuildInputs = lib.optionals cudaSupport [ cudaPackages.cuda_nvcc ];

    buildInputs = lib.optionals cudaSupport (
      with cudaPackages;
      [
        cccl
        cuda_cudart
      ]
    );

    env =
      if cudaSupport then
        {
          BUILD_WITH_CUDA = "1";
          LMCACHE_CUDA_MAJOR = cudaPackages.cudaMajorVersion;
          TORCH_CUDA_ARCH_LIST = lib.concatStringsSep ";" cudaCapabilities;
        }
      else
        { NO_GPU_EXT = "1"; };

    # With MAX_JOBS unset the CUDA build sizes its nvcc jobs to the host's
    # memory, 3 GiB each; a set MAX_JOBS is taken as-is.
    preBuild = lib.optionalString (!cudaSupport) ''
      export MAX_JOBS=$NIX_BUILD_CORES
    '';

    dependencies = [
      aiofile
      aiofiles
      aiohttp
      awscrt
      blake3
      cachetools
      cryptography
      fastapi
      google-api-core
      google-cloud-bigtable
      grpcio
      httptools
      httpx
      huggingface-hub
      msgspec
      numba
      numpy
      opentelemetry-api
      opentelemetry-exporter-otlp
      opentelemetry-exporter-prometheus
      opentelemetry-sdk
      prometheus-client
      protobuf
      psutil
      py-cpuinfo
      pyyaml
      pyzmq
      redis
      safetensors
      sortedcontainers
      torch
      transformers
      uvicorn
    ]
    ++ lib.optionals cudaSupport [
      cuda-bindings
      cupy
      nixl
      nvidia-ml-py
      ray
      torchvision
      xformers
    ];

    pythonRelaxDeps = true;
    pythonRemoveDeps = [
      "cuda-python"
      "cufile-python"
      "cupy-cuda12x"
      "cupy-cuda13x"
      "nvtx"
      "pytest"
      "setuptools"
      "setuptools_scm"
    ];

    # The tests need a GPU and a running vLLM; the import check does not.
    doCheck = false;
    pythonImportsCheck = [
      "lmcache"
      "lmcache.lmcache_fs"
      "lmcache.lmcache_native"
      "lmcache.lmcache_redis"
    ];

    meta = {
      description = "KV cache layer that offloads and reuses an LLM serving engine's KV cache";
      homepage = "https://github.com/LMCache/LMCache";
      license = lib.licenses.asl20;
      platforms = lib.platforms.linux;
      mainProgram = "lmcache";
      maintainers = [ ];
    };
  })
