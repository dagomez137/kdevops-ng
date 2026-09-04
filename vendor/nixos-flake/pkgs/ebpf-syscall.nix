# SPDX-License-Identifier: copyleft-next-0.3.1
#
# ebpf-syscall: CO-RE eBPF storage tracers and KV-cache IO tooling
# from SamsungDS.
#
# The tracers pair application intent with kernel/device mechanism
# ("two-witness join"): nvme_tp_monitor records every NVMe command at
# the driver tracepoints, nvme_uring_cmd_monitor clocks io_uring
# passthrough from char-dev entry to device completion, and
# iouring_monitor / mmap_readamp / syscall_monitor cover the layers
# above. Each is a standalone libbpf skeleton binary emitting JSONL.
# Alongside them the repository ships syscall_replayer (cJSON), the
# nvme_uring_cmd_smoke and nvme_kv_smoke liburing workload
# generators, the pagemon_viz memory-map heatmap visualizer (no
# Makefile rule upstream; compiled here from pagemon_viz_v2.c), and
# kvio, the KV-cache storage IO tool whose bench subcommand carries
# the kvspill-derived workload shapes.
#
# kvio's Rust pyo3 engine (the vendored LMCache raw_block crate) is
# built here; the crate ships no Cargo.lock, so this package pins one
# (ebpf-syscall-raw-block.Cargo.lock, regenerate with
# `cargo generate-lockfile` in the crate on a source bump). The
# torch-linked lmcache_native C++ extension (build_native.py) is NOT
# built: it links the running interpreter's torch, which would pull
# PyTorch into the closure. kvio subcommands that drive the LMCache
# engine data path need torch at runtime and report so via
# `kvio doctor`; everything else works as shipped. The Python
# analyzers, converters and examples are installed under
# share/ebpf-syscall (the Perfetto converters need the `perfetto`
# pip package, not packaged in nixpkgs).
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
  cjson,
  liburing,
  pkg-config,
  gnumake,
  cargo,
  rustc,
  rustPlatform,
  python3,
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
    cargo
    rustc
    rustPlatform.cargoSetupHook
    # pyo3's build script probes the interpreter's config at build time.
    python3
  ];

  buildInputs = [
    libbpf
    elfutils
    zlib
    cjson
    liburing
  ];

  # The pinned lockfile for the vendored raw_block crate; the hook
  # writes the .cargo config pointing at the vendored crate copies.
  cargoRoot = "tools/kvio/vendor/lmcache/rust/raw_block";
  cargoDeps = rustPlatform.importCargoLock {
    lockFile = ./ebpf-syscall-raw-block.Cargo.lock;
  };

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
  # The crate gets the pinned lockfile the cargo hook expects.
  postPatch = ''
    cp ${libbpf-tools.src}/libbpf-tools/x86/vmlinux.h vmlinux.h
    cp ${./ebpf-syscall-raw-block.Cargo.lock} ${finalAttrs.cargoRoot}/Cargo.lock
  '';

  # CFLAGS is overridden because the Makefile hardcodes the Debian
  # cjson include dir. The kvio engine build mirrors the `make kvio`
  # recipe minus build_native.py (torch, see the header comment).
  buildPhase = ''
    runHook preBuild
    # `all` names nvme_tp_monitor through a variable defined only
    # below the rule, so make reads it as empty; build it explicitly.
    make CFLAGS="-g -O2 -Wall -Wextra -I${cjson}/include/cjson" \
      all nvme_tp_monitor nvme_uring_cmd_smoke nvme_kv_smoke
    cc -g -O2 pagemon_viz_v2.c -o pagemon_viz
    ( cd ${finalAttrs.cargoRoot} && cargo build --release --offline )
    mkdir --parents tools/kvio/build
    cp ${finalAttrs.cargoRoot}/target/release/liblmcache_rust_raw_block_io.so \
      tools/kvio/build/lmcache_rust_raw_block_io.so
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    install -D --mode=755 --target-directory=$out/bin \
      syscall_monitor syscall_replayer mmap_readamp iouring_monitor \
      nvme_uring_cmd_monitor nvme_tp_monitor \
      nvme_uring_cmd_smoke nvme_kv_smoke pagemon_viz

    mkdir --parents $out/share/ebpf-syscall
    cp --recursive tools analyzers examples $out/share/ebpf-syscall/
    rm --recursive --force \
      $out/share/ebpf-syscall/tools/kvio/vendor/lmcache/rust/raw_block/target

    printf '#!%s\nexec %s/bin/python3 %s/share/ebpf-syscall/tools/kvio/kvio "$@"\n' \
      "${stdenv.shell}" "${python3}" "$out" > $out/bin/kvio
    chmod 755 $out/bin/kvio
    runHook postInstall
  '';

  meta = {
    description = "CO-RE eBPF storage tracers and KV-cache IO tooling from SamsungDS";
    homepage = "https://github.com/SamsungDS/ebpf-syscall";
    # Repository LICENSE; the embedded BPF programs additionally
    # declare SEC("license") = "GPL" to the kernel.
    license = lib.licenses.asl20;
    platforms = lib.platforms.linux;
    maintainers = [ ];
  };
})
