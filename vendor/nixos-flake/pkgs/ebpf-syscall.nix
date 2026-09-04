# SPDX-License-Identifier: copyleft-next-0.3.1
#
# ebpf-syscall: CO-RE eBPF storage tracers from SamsungDS.
#
# The tracers pair application intent with kernel/device mechanism
# ("two-witness join"): nvme_tp_monitor records every NVMe command at
# the driver tracepoints, nvme_uring_cmd_monitor clocks io_uring
# passthrough from char-dev entry to device completion, and
# iouring_monitor / mmap_readamp / syscall_monitor cover the layers
# above. Each is a standalone libbpf skeleton binary emitting JSONL.
#
# The syscall_replayer is not built: it is the only target needing
# cJSON and is a workload generator, not a tracer.
#
# Source: https://github.com/SamsungDS/ebpf-syscall
{
  lib,
  stdenv,
  fetchFromGitHub,
  llvmPackages,
  bpftools,
  libbpf,
  elfutils,
  zlib,
  pkg-config,
  gnumake,
  libbpf-tools,
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "ebpf-syscall";
  version = "0-unstable-2026-09-02";

  src = fetchFromGitHub {
    owner = "SamsungDS";
    repo = "ebpf-syscall";
    rev = "9349e4815f377c0fb303f84561bcb71760bb0136";
    hash = "sha256-9AWM+qcdxFY75jlWMdAg1XfDxk80S/ERgqwEsuh0hAM=";
  };

  nativeBuildInputs = [
    llvmPackages.clang
    bpftools
    pkg-config
    gnumake
  ];

  buildInputs = [
    libbpf
    elfutils
    zlib
  ];

  # Nix hardening flags are not valid for the BPF target.
  hardeningDisable = [
    "zerocallusedregs"
    "stackprotector"
  ];

  # The Makefile generates vmlinux.h from /sys/kernel/btf/vmlinux,
  # which the sandbox cannot see, and skips the recipe when the file
  # already exists. Provide the CO-RE header the libbpf-tools package
  # already pins (bcc's x86 vmlinux.h) so both stay on the same
  # nixpkgs update cadence; the nvme_core module types are local
  # preserve_access_index mirrors in the sources and need no header.
  postPatch = ''
    cp ${libbpf-tools.src}/libbpf-tools/x86/vmlinux.h vmlinux.h
  '';

  # Only the tracers; `all` would also build syscall_replayer (cJSON).
  buildPhase = ''
    runHook preBuild
    make setup syscall_monitor mmap_readamp iouring_monitor \
      nvme_uring_cmd_monitor nvme_tp_monitor
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    install -D --mode=755 --target-directory=$out/bin \
      syscall_monitor mmap_readamp iouring_monitor \
      nvme_uring_cmd_monitor nvme_tp_monitor
    runHook postInstall
  '';

  meta = {
    description = "CO-RE eBPF storage tracers (NVMe, io_uring, syscall) from SamsungDS";
    homepage = "https://github.com/SamsungDS/ebpf-syscall";
    # Repository LICENSE; the embedded BPF programs additionally
    # declare SEC("license") = "GPL" to the kernel.
    license = lib.licenses.asl20;
    platforms = lib.platforms.linux;
    maintainers = [ ];
  };
})
