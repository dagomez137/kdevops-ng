# SPDX-License-Identifier: copyleft-next-0.3.1
#
# What scripts/check-vendor.sh runs each vendored project's own checks with:
# minijinja and systemd for qemu-system-units, the QEMU and virtiofsd its
# rendered units name, and a pinned kernel source with the Kconfig toolchain
# for linux-config-fragments.
{ pkgs }:
pkgs.mkShell {
  packages = [
    pkgs.bash
    pkgs.git
    pkgs.nixfmt
    pkgs.minijinja
    pkgs.systemd
    pkgs.qemu_kvm
    pkgs.virtiofsd
    pkgs.gnumake
    pkgs.gcc
    pkgs.flex
    pkgs.bison
    pkgs.bc
    pkgs.perl
    pkgs.gawk
  ];
  KDEVOPS_KERNEL_SRC = pkgs.srcOnly pkgs.linux_latest;
  KDEVOPS_SYSTEMD_UNITS = "${pkgs.systemd}/example/systemd";
}
