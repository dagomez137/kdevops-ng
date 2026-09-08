# SPDX-License-Identifier: copyleft-next-0.3.1
#
# FuStIP: full stack I/O profiling.
#
# One run attaches four layers to the same workload and lines their
# results up on one time base: sysstat for system-wide CPU, memory and
# disk, and eBPF layers for the filesystem, the block layer and the
# NVMe driver. Each eBPF layer has two modes. Summary mode runs a
# bpftrace script that aggregates in the kernel and prints one
# distribution at the end; detailed mode runs a libbpf loader that
# streams every event through a ring buffer to a CSV, converted to
# Parquet afterwards, which is what the time series and the
# visualizations are derived from. Filters compose as
# container, then device, then command or PID.
#
# Upstream develops inside a Devbox shell, so the repository carries
# no install step: `run.sh` drives the per-layer Makefiles in place
# and each Makefile builds its loader on first use. That is what the
# three parts of this derivation replace. The loaders are built here
# (each Makefile generates its CO-RE header from the running kernel's
# BTF, which a builder has none of, so they get the vmlinux.h the
# libbpf-tools package pins); the tree is installed with the built
# loaders where the Makefiles expect them, so a run finds them
# up to date and rebuilds nothing; and the `fustip` wrapper runs
# `run.sh` out of that tree with the interpreter, the tracers and the
# workload generators the tool shells out to on PATH.
#
# The Python parts (the CLI parser, the stats generators, the
# visualizations) get the three packages requirements.txt names.
# Docker, one of the Devbox packages, is not pulled in: it is only
# needed for the container filter, and a NixOS host provides its own
# container runtime.
#
# Source: https://github.com/mottosen/FuStIP
{
  lib,
  stdenv,
  fetchFromGitHub,
  makeWrapper,
  llvmPackages,
  bpftools,
  libbpf,
  elfutils,
  zlib,
  gnumake,
  python3,
  bpftrace,
  sysstat,
  nvme-cli,
  fio,
  libbpf-tools,
}:

let
  pythonEnv = python3.withPackages (ps: [
    ps.matplotlib
    ps.numpy
    ps.polars
  ]);
in
stdenv.mkDerivation (finalAttrs: {
  pname = "fustip";
  version = "0-unstable-2026-06-28";

  src = fetchFromGitHub {
    owner = "mottosen";
    repo = "FuStIP";
    rev = "620ce68a62dd0e7013b1aaf783ac7deda79b16a8";
    hash = "sha256-JiyGIY2Ms1WzoN5WrTdwFRjs6ot1FvS7cC0JIV0VgXs=";
  };

  nativeBuildInputs = [
    llvmPackages.clang
    bpftools
    gnumake
    makeWrapper
  ];

  buildInputs = [
    libbpf
    elfutils
    zlib
  ];

  hardeningDisable = [
    "zerocallusedregs"
    "stackprotector"
  ];

  postPatch = ''
    mkdir --parents util/bpf/include
    cp ${libbpf-tools.src}/libbpf-tools/x86/vmlinux.h util/bpf/include/vmlinux.h
  '';

  buildPhase = ''
    runHook preBuild
    # Each layer Makefile takes clang from `which`, which the sandbox
    # does not ship; a command line variable overrides that.
    for layer in nvme block fs; do
      make --directory=layers/$layer/bpf CLANG=clang standalone
    done
    runHook postBuild
  '';

  # The loaders install outside the directories stdenv strips, and the
  # Makefiles build them with `-g`: the debug info names gcc's include
  # path, which keeps the whole compiler in the closure.
  stripDebugList = [ "share/fustip/layers" ];

  installPhase = ''
    runHook preInstall

    mkdir --parents $out/share/fustip
    cp --recursive layers tests util run.sh readme.md $out/share/fustip/
    # Build leftovers: the loaders stay, their inputs do not.
    rm --force $out/share/fustip/layers/*/bpf/c/*.o \
       $out/share/fustip/layers/*/bpf/c/*.skel.h

    makeWrapper $out/share/fustip/run.sh $out/bin/fustip \
      --prefix PATH : ${
        lib.makeBinPath [
          pythonEnv
          bpftrace
          sysstat
          nvme-cli
          fio
          gnumake
        ]
      }

    runHook postInstall
  '';

  meta = {
    description = "Full stack I/O profiling across the filesystem, block and NVMe layers";
    homepage = "https://github.com/mottosen/FuStIP";
    license = lib.licenses.gpl2Only;
    mainProgram = "fustip";
    platforms = lib.platforms.linux;
    maintainers = [ ];
  };
})
