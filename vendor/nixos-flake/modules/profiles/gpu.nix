# SPDX-License-Identifier: copyleft-next-0.3.1
#
# gpu: the NVIDIA driver userspace and the CUDA build of nixpkgs, for a
# guest that boots an external kernel and reaches an NVIDIA GPU through
# VFIO passthrough.
#
# The kernel side of the driver is not here. The NVIDIA open GPU kernel
# modules must be built against the external kernel's own build tree and
# installed into its /lib/modules, which this flake cannot do for a kernel
# it did not build; nvidia-open-src (pkgs/default.nix) is the source to
# build them from. It is the same release as the userspace below, which is
# what the modules require: nvidia.ko refuses a userspace of any other
# version. The guest then loads nvidia and nvidia_uvm from /lib/modules
# like any other module.
#
# The driver is pkgs/nvidia-driver.nix, a newer release than either
# channel ships, built with nixos-unstable's mkDriver; the CUDA stack comes
# from pkgsUnstable, where the CUDA build of the inference stack lives.
#
# NVIDIA's driver and CUDA are unfree, so the profile allows unfree
# packages. Building with CUDA is a full rebuild of every package that
# honours cudaSupport; cudaCapabilities narrows the GPU generations it
# compiles for, which shortens that build considerably.
#
# A NixOS-built kernel (boot.kernel.enable) is refused rather than half
# served: there hardware.nvidia builds the kernel modules itself, and that
# path is not wired up or tested here.
#
# Source: https://github.com/NVIDIA/open-gpu-kernel-modules
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.nixos-flake.gpu;
  driver = pkgs.nvidia-driver;
in
{
  options.nixos-flake.gpu.cudaCapabilities = lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [ ];
    example = [ "9.0" ];
    description = ''
      CUDA compute capabilities to compile for, for example "8.0" for
      A100, "8.9" for L4 and L40S, "9.0" for H100 and H200. Empty keeps
      nixpkgs' default set, which covers every supported generation and
      takes the longest to build.
    '';
  };

  config = {
    assertions = [
      {
        assertion = !config.boot.kernel.enable;
        message = "nixos-flake.gpu supports an external kernel only; a NixOS-built kernel needs hardware.nvidia instead.";
      }
    ];

    nixpkgs.config = {
      allowUnfree = true;
      cudaSupport = true;
    }
    // lib.optionalAttrs (cfg.cudaCapabilities != [ ]) { inherit (cfg) cudaCapabilities; };

    # libcuda and the other driver libraries, at /run/opengl-driver/lib
    # where CUDA applications look for them.
    hardware.graphics = {
      enable = true;
      extraPackages = [ driver.out ];
    };

    # The GSP firmware the open modules load at probe time.
    hardware.firmware = [ driver.firmware ];

    boot.kernelModules = [
      "nvidia"
      "nvidia_uvm"
    ];
    boot.blacklistedKernelModules = [ "nouveau" ];

    # The device nodes CUDA opens. Without nvidia-modprobe nothing creates
    # them, so create them as each module loads, as hardware.nvidia does.
    services.udev.extraRules = ''
      KERNEL=="nvidia", RUN+="${pkgs.runtimeShell} -c 'mknod --mode=666 /dev/nvidiactl c 195 255'"
      KERNEL=="nvidia", RUN+="${pkgs.runtimeShell} -c 'for i in $$(cat /proc/driver/nvidia/gpus/*/information | grep Minor | cut --delimiter=\  --fields=4); do mknod --mode=666 /dev/nvidia$${i} c 195 $${i}; done'"
      KERNEL=="nvidia_uvm", RUN+="${pkgs.runtimeShell} -c 'mknod --mode=666 /dev/nvidia-uvm c $$(grep nvidia-uvm /proc/devices | cut --delimiter=\  --fields=1) 0'"
      KERNEL=="nvidia_uvm", RUN+="${pkgs.runtimeShell} -c 'mknod --mode=666 /dev/nvidia-uvm-tools c $$(grep nvidia-uvm /proc/devices | cut --delimiter=\  --fields=1) 1'"
    '';

    environment.systemPackages = [
      driver.bin
      pkgs.pciutils
    ];
  };
}
