# SPDX-License-Identifier: copyleft-next-0.3.1
#
# Custom packages not available in nixpkgs.
#
# Each package is a file declaring a function whose arguments are its
# dependencies. Use callPackage to compose them:
#
#   xnvme = pkgs.callPackage ./xnvme.nix { };
#
# The overlay at overlays/default.nix imports this file and merges
# the packages into the nixpkgs set.
#
# Reference: https://nix.dev/tutorials/callpackage
pkgs: {
  blktests = pkgs.callPackage ./blktests.nix { };
  bpf-perf-tools = pkgs.callPackage ./bpf-perf-tools.nix { };
  cpupower = pkgs.callPackage ./cpupower.nix { };
  damo = pkgs.callPackage ./damo.nix { };
  ebpf_exporter = pkgs.callPackage ./ebpf_exporter.nix { };
  ebpf-fix-latency-tool = pkgs.callPackage ./ebpf-fix-latency-tool.nix { };
  ebpf-syscall = pkgs.callPackage ./ebpf-syscall.nix { };
  faster-biolatency = pkgs.callPackage ./faster-biolatency.nix { };
  fustip = pkgs.callPackage ./fustip.nix { };
  libbpf-tools = pkgs.callPackage ./libbpf-tools.nix { };
  nfstest = pkgs.callPackage ./nfstest.nix { };
  nvme-bpf = pkgs.callPackage ./nvme-bpf.nix { };
  pynfs = pkgs.callPackage ./pynfs.nix { };
  systing = pkgs.callPackage ./systing.nix { };
  xnvme = pkgs.callPackage ./xnvme.nix { };
}
