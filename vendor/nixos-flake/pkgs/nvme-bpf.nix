# SPDX-License-Identifier: copyleft-next-0.3.1
#
# nvme-bpf: NVMe driver latency and command tracing at the nvme
# tracepoints.
#
# `nvme_latency` accumulates in-kernel latency histograms per
# controller, opcode and size class: it times `nvme_setup_cmd` to
# `nvme_complete_rq` by keying a hash on the command's own identity
# (controller, queue, command id) rather than the request pointer, so
# it also covers commands the block layer never sees. `nvme_trace`
# prints one line per submission and completion queue entry.
#
# Upstream builds with Bazel, which needs the Bazel Central Registry
# at build time and so cannot run in the Nix sandbox. The build is
# small enough to spell out here instead: two BPF objects per tool
# (the plain one and the `-DVLOG` variant each tool's loader embeds),
# their skeletons, and one C++ link. Two consequences of dropping
# Bazel are visible below. libbpf comes from nixpkgs rather than the
# repository's `bpftool` submodule, which is not in the source
# tarball. And Abseil is linked from its shared libraries directly,
# because the upstream dependency list is per Bazel target and has no
# pkg-config or CMake equivalent; `--as-needed` keeps only what each
# binary actually calls.
#
# The BPF programs need the nvme_core module's tracepoint context
# types, which upstream dumps from a running kernel's BTF. There is
# none in a builder, so the CO-RE header is assembled from the
# vmlinux.h the libbpf-tools package pins plus the module types in
# nvme-bpf-nvme-core-types.h next to this recipe.
#
# Source: https://github.com/mogoreanu/nvme_bpf
{
  lib,
  stdenv,
  fetchFromGitHub,
  llvmPackages,
  bpftools,
  libbpf,
  abseil-cpp,
  elfutils,
  zlib,
  libbpf-tools,
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "nvme-bpf";
  version = "0-unstable-2026-07-10";

  src = fetchFromGitHub {
    owner = "mogoreanu";
    repo = "nvme_bpf";
    rev = "32ae2d5dfe9a69ae33ef66b94129ac346d527c96";
    hash = "sha256-/s00n4pZ+C1Ayk9YiW3LpjpsAMwU9DkzQYDZrcaZ8+E=";
  };

  nativeBuildInputs = [
    llvmPackages.clang
    bpftools
  ];

  buildInputs = [
    libbpf
    abseil-cpp
    elfutils
    zlib
  ];

  hardeningDisable = [
    "zerocallusedregs"
    "stackprotector"
  ];

  postPatch = ''
    cat ${libbpf-tools.src}/libbpf-tools/x86/vmlinux.h \
      ${./nvme-bpf-nvme-core-types.h} > nvme_core_gen.h
  '';

  buildPhase = ''
    runHook preBuild

    for variant in "" "-DVLOG"; do
      suffix=""
      [ -n "$variant" ] && suffix="_vlog"
      for tool in nvme_latency nvme_trace; do
        clang -g -O2 -target bpf -D__TARGET_ARCH_x86 $variant \
          -I. -I${libbpf}/include \
          -c $tool.bpf.c -o ''${tool}''${suffix}.bpf.o
      done
    done

    # Skeleton header names are the loaders' #includes, and the plain
    # nvme_trace one does not follow the pattern of the other three.
    bpftool gen skeleton nvme_latency.bpf.o > nvme_latency_bpf.skel.h
    bpftool gen skeleton nvme_latency_vlog.bpf.o > nvme_latency_vlog_bpf.skel.h
    bpftool gen skeleton nvme_trace.bpf.o > nvme_trace.skel.h
    bpftool gen skeleton nvme_trace_vlog.bpf.o > nvme_trace_vlog_bpf.skel.h

    absl_libs=""
    for lib in ${abseil-cpp}/lib/libabsl_*.so; do
      absl_libs="$absl_libs -l$(basename $lib .so | cut --characters=4-)"
    done

    for tool in nvme_latency nvme_trace; do
      $CXX -std=c++20 -O2 -Wno-packed-bitfield-compat -I. \
        $tool.cc histogram.cc nvme_strings.cc \
        -Wl,--as-needed $absl_libs -lbpf -lelf -lz -o $tool
    done

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    install -D --mode=755 --target-directory=$out/bin nvme_latency nvme_trace
    runHook postInstall
  '';

  meta = {
    description = "NVMe latency histograms and command tracing at the nvme tracepoints";
    homepage = "https://github.com/mogoreanu/nvme_bpf";
    # Repository LICENSE; the BPF programs declare
    # SEC("license") = "Dual BSD/GPL" to the kernel.
    license = lib.licenses.asl20;
    mainProgram = "nvme_latency";
    platforms = lib.platforms.linux;
    maintainers = [ ];
  };
})
