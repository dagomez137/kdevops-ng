# SPDX-License-Identifier: copyleft-next-0.3.1
#
# faster-biolatency: Tanel Poder's rewrite of BCC's biolatency, from
# the 0x.tools experiments directory.
#
# It answers the same question as `biolatency-libbpf` (block I/O
# latency as a histogram) at a fraction of the cost, by dropping the
# submit-side probe: the span comes from the request's own
# `io_start_time_ns` (or `start_time_ns` with `-Q`) instead of a
# timestamp inserted into a map at issue, so there is no per-request
# state and only one probe runs. The histogram is a `PERCPU_HASH`, so
# the completion path takes no atomics either.
#
# There is no build system upstream: the experiment is three files
# (`biolatency.c`, `biolatency.bpf.c`, `biolatency.h`) meant to
# replace BCC's own inside `libbpf-tools/`, where they pick up
# `bits.bpf.h`, `core_fixes.bpf.h`, the vendored libbpf and the
# skeleton rules. So the build tree here is the BCC source the
# `libbpf-tools` package already pins, with the three files copied
# over BCC's; the binary installs under the upstream directory name to
# keep it distinct from `biolatency-libbpf`.
#
# Source: https://github.com/tanelpoder/0xtools/tree/main/experiments/faster-biolatency
{
  lib,
  stdenv,
  fetchFromGitHub,
  llvmPackages,
  elfutils,
  zlib,
  openssl,
  pkg-config,
  gnumake,
  libbpf-tools,
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "faster-biolatency";
  version = "0-unstable-2025-11-24";

  src = fetchFromGitHub {
    owner = "tanelpoder";
    repo = "0xtools";
    rev = "c0b726947ab1f25a16460e9c32c278bbc069a174";
    hash = "sha256-xd6/UYDNx6GYYkGEI3n7ZSFOQGYDLS9CJZcSonYUFYA=";
  };

  # The build tree is BCC's, not this src: unpack the pinned BCC
  # source, drop the experiment's three files over BCC's biolatency,
  # and build from there. `.output` goes for the reason the
  # libbpf-tools package documents (a src that is a working tree
  # carries a host-built bpftool the sandbox cannot run).
  unpackPhase = ''
    runHook preUnpack
    cp --recursive ${libbpf-tools.src} bcc
    chmod --recursive u+w bcc
    sourceRoot=bcc/libbpf-tools
    rm --recursive --force $sourceRoot/.output
    cp ${finalAttrs.src}/experiments/faster-biolatency/biolatency.c \
       ${finalAttrs.src}/experiments/faster-biolatency/biolatency.h \
       ${finalAttrs.src}/experiments/faster-biolatency/biolatency.bpf.c \
       $sourceRoot/
    chmod u+w $sourceRoot/biolatency.c $sourceRoot/biolatency.h \
       $sourceRoot/biolatency.bpf.c
    runHook postUnpack
  '';

  nativeBuildInputs = [
    llvmPackages.clang
    llvmPackages.llvm # llvm-strip
    pkg-config
    gnumake
  ];

  buildInputs = [
    elfutils
    zlib
    openssl # the vendored bpftool bootstrap build links it
  ];

  # Not valid for the BPF target, as in every CO-RE package here.
  hardeningDisable = [
    "zerocallusedregs"
    "stackprotector"
  ];

  # biolatency is not one of the tools that #include blazesym.h, so
  # the Rust dependency the full libbpf-tools build carries is off.
  makeFlags = [ "USE_BLAZESYM=0" ];
  buildFlags = [ "biolatency" ];

  enableParallelBuilding = true;

  installPhase = ''
    runHook preInstall
    install -D --mode=755 biolatency $out/bin/faster-biolatency
    runHook postInstall
  '';

  meta = {
    description = "Lower overhead rewrite of BCC's biolatency from 0x.tools";
    homepage = "https://github.com/tanelpoder/0xtools";
    license = lib.licenses.gpl2Only;
    mainProgram = "faster-biolatency";
    platforms = lib.platforms.linux;
    maintainers = [ ];
  };
})
