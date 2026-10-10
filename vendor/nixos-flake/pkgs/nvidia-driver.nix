# SPDX-License-Identifier: copyleft-next-0.3.1
#
# The NVIDIA driver the gpu profile ships. nvidia-open-src is the source of
# its kernel modules, so the two stay one release. 615.78.08 is the first
# release whose open modules build against Linux 7.3 with
# CONFIG_CGROUP_DMEM, and nixpkgs still ships 615.71.09, so it is declared
# here through nixos-unstable's own mkDriver.
#
# Source: https://github.com/NVIDIA/open-gpu-kernel-modules
{ pkgsUnstable }:

pkgsUnstable.linuxPackages.nvidiaPackages.mkDriver {
  version = "615.78.08";
  sha256_64bit = "sha256-Pj9t3cLudnoIGFMAr3vjyyhuznZpjS3eNSRZl4LQf/4=";
  sha256_aarch64 = "sha256-UHYBE1AYHoGgMfD9AS/jX+S+N8eCDWUft6qolK9Eejc=";
  openSha256 = "sha256-HBINiOjL0ZJLIAJeNIBYHBnwgUXtNwPPtnFpAI1YwF4=";
  settingsSha256 = "sha256-inDRpG02sdDgHmlqgu/DsgK8OFdOt1fZIYyXdhlGC/c=";
  persistencedSha256 = "sha256-RzeR6Ldct6MUxjnXRyThdh5Y3jjMehTVg85MtwuWNX4=";
}
