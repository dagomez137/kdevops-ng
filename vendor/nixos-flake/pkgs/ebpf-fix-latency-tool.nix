# SPDX-License-Identifier: copyleft-next-0.3.1
#
# ebpf-fix-latency-tool: roundtrip latency for FIX protocol traffic.
#
# TC hooks on ingress and egress read the TCP payload, a tail-called
# parser pulls every FIX Tag 11 (ClOrdID) out of it, and each
# occurrence goes to userspace with a monotonic timestamp; userspace
# pairs request with response and records the difference in an HDR
# histogram, printing interval MIN/AVG/MAX plus cumulative
# percentiles.
#
# The subject is not storage; it is here for its shape. It keeps no
# histogram in the kernel at all, sending one ring buffer record per
# event and aggregating in userspace, which is the opposite of the
# in-kernel aggregation `nvme_latency` and the `libbpf-tools`
# histograms do.
#
# The repository commits its own CO-RE vmlinux.h (its CI runners have
# no BTF), so unlike the other eBPF packages here this one needs
# nothing from a running kernel. The unit tests are pure userspace
# (parser, HDR histogram, pending map, ASCII rendering) and run as
# the check phase.
#
# Source: https://github.com/epam/ebpf-fix-latency-tool
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
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "ebpf-fix-latency-tool";
  version = "0-unstable-2026-01-25";

  src = fetchFromGitHub {
    owner = "epam";
    repo = "ebpf-fix-latency-tool";
    rev = "9112a530d95da799d70adf0b907175df4146460b";
    hash = "sha256-yC84jvpMqJsNNQPt19c2d4m7aTDhE13jU4E6hBDZx5Y=";
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

  hardeningDisable = [
    "zerocallusedregs"
    "stackprotector"
  ];

  # The unit test binaries are committed next to their sources, so make
  # finds the test target's prerequisites satisfied and runs the shipped
  # ones, which are linked against a distribution loader. Drop them and
  # the check phase builds each from source.
  postPatch = ''
    rm --force test/test_parser_logic test/test_hdr_histogram \
      test/test_pending_map test/test_ascii_histogram
  '';

  # BPFTOOL defaults to a Debian linux-tools path found with `find`.
  makeFlags = [
    "BPF_CLANG=clang"
    "BPFTOOL=bpftool"
  ];

  doCheck = true;
  checkTarget = "test";

  installPhase = ''
    runHook preInstall
    install -D --mode=755 user/ebpf-fix-latency-tool $out/bin/ebpf-fix-latency-tool
    install -D --mode=644 --target-directory=$out/share/doc/ebpf-fix-latency-tool \
      README.md DISTRIBUTION.md RELEASE.md
    runHook postInstall
  '';

  meta = {
    description = "FIX protocol roundtrip latency measured with eBPF TC hooks";
    homepage = "https://github.com/epam/ebpf-fix-latency-tool";
    license = lib.licenses.asl20;
    mainProgram = "ebpf-fix-latency-tool";
    platforms = lib.platforms.linux;
    maintainers = [ ];
  };
})
