# SPDX-License-Identifier: copyleft-next-0.3.1
#
# systing: a libbpf based tracer that records what an application is
# doing and why it waits.
#
# One run attaches a set of recorders (scheduler and IRQ events, sleep
# and CPU stacks, syscalls, network at syscall or packet granularity,
# memory including VFIO DMA regions and the IOMMU map/unmap run-size
# histogram) and writes a single trace. The default output is a
# Perfetto protobuf; an output ending in .duckdb writes a queryable
# trace database and .systing writes the lightweight profile export.
# systing-analyze queries a recorded trace and systing-util carries the
# supporting subcommands.
#
# The BPF programs are compiled from source at build time by
# libbpf-cargo, driving clang with -target bpf. Two consequences shape
# this derivation. Nix's hardening flags (-fzero-call-used-regs,
# -fstack-protector) have no BPF backend, so hardeningDisable turns
# them off the way libbpf-tools and ebpf_exporter already do; and clang
# does not search the standard system include paths when it targets
# BPF, so CPATH carries the kernel and glibc headers, which it honours
# for every target. The repository vendors vmlinux_x86_64.h,
# vmlinux_aarch64.h and vmlinux_riscv64.h, so unlike the other CO-RE
# packages here this one needs neither bpftool nor a running kernel to
# generate the CO-RE header.
#
# The duckdb-bundled default feature is kept, so the duckdb the trace
# database is written with is the version the crate expects, compiled
# here rather than linked against a nixpkgs duckdb that moves on its
# own release cadence. That is what cmake is for.
#
# Source: https://github.com/josefbacik/systing
{
  lib,
  rustPlatform,
  fetchFromGitHub,
  pkg-config,
  cmake,
  protobuf,
  llvmPackages,
  elfutils,
  zlib,
  linuxHeaders,
  glibc,
}:

rustPlatform.buildRustPackage (finalAttrs: {
  pname = "systing";
  version = "1.17.8";

  src = fetchFromGitHub {
    owner = "josefbacik";
    repo = "systing";
    rev = "v${finalAttrs.version}";
    hash = "sha256-tRA/5X7CnPchUZroIniwqfvapWy6sCxTd1tZ176QTEU=";
  };

  # The crate set is vendored from the lockfile of whatever src is being
  # built, not from a hash of one pinned vendor tree, so an override that
  # swaps src for another ref keeps building. A `cargoHash` cannot: it is a
  # hash over the vendor output, and that output embeds the lockfile, so it
  # changes even between two refs whose dependencies are identical. What a
  # ref still has to share is the set of git dependencies, since each needs
  # its own entry below; a bump of the pinned blazesym revision stops with
  # the new one named.
  cargoLock = {
    lockFile = "${finalAttrs.src}/Cargo.lock";
    outputHashes = {
      "blazesym-0.2.6" = "sha256-bQVzLUgwiUqsPGJKgvWnwyNu6iyuKPFWtsR+2BcWclI=";
    };
  };

  nativeBuildInputs = [
    pkg-config
    cmake
    protobuf
    llvmPackages.clang
    rustPlatform.bindgenHook
  ];

  buildInputs = [
    elfutils
    zlib
  ];

  # cmake is here to build the bundled duckdb from the crate's own
  # build script, not to configure this source tree.
  dontUseCmakeConfigure = true;

  hardeningDisable = [ "all" ];

  env.CPATH = "${linuxHeaders}/include:${glibc.dev}/include";

  meta = {
    description = "libbpf based tracer to figure out what an application is doing";
    homepage = "https://github.com/josefbacik/systing";
    # Repository LICENSE; the embedded BPF programs additionally
    # declare SEC("license") = "GPL" to the kernel.
    license = lib.licenses.mit;
    mainProgram = "systing";
    platforms = lib.platforms.linux;
    maintainers = [ ];
  };
})
