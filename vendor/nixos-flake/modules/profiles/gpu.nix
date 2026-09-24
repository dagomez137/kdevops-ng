# SPDX-License-Identifier: copyleft-next-0.3.1
#
# gpu: driver + compute stack for a physical GPU that reached this guest
# through VFIO passthrough (the host's `pci_passthrough` qemu-system var).
#
# Not featured by default and meaningless without a passed-through device:
# paravirtual display adapters expose no CUDA/ROCm device, and the stack is
# multi-GB of closure. Select it together with a workload suite (kvcache).
#
# Kernel-module caveat, imageless backend: the guest usually boots a custom
# kdevops-built kernel (direct `-kernel` boot, /lib/modules over virtiofs),
# not nixpkgs' kernel. The proprietary NVIDIA kmod is prebuilt against
# `config.boot.kernelPackages` and will not load there (version magic).
# `openKernelModule` therefore defaults to true: the open GPU kernel modules
# build from source against the running kernel's headers. Turing or newer
# only; H100-class devices qualify. For nixpkgs-kernel guests either setting
# works.
{
  config,
  pkgs,
  lib,
  ...
}:
{
  options.nixos-flake.gpu = {
    vendor = lib.mkOption {
      type = lib.types.enum [
        "nvidia"
        "amd"
      ];
      default = "nvidia";
      description = "Vendor of the GPU passed through to this guest.";
    };
    openKernelModule = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Use NVIDIA's open GPU kernel modules (built from source against the
        running kernel) instead of the prebuilt proprietary kmod. Required
        for custom kdevops kernels; needs a Turing-or-newer device.
      '';
    };
  };

  config =
    let
      cfg = config.nixos-flake.gpu;
      nvidia = cfg.vendor == "nvidia";
    in
    {
      # CUDA and the NVIDIA userspace are unfree; scope the allowance to this
      # profile so closures without it stay pure-free.
      nixpkgs.config.allowUnfree = lib.mkDefault nvidia;

      hardware.graphics.enable = true;

      hardware.nvidia = lib.mkIf nvidia {
        open = cfg.openKernelModule;
        modesetting.enable = true;
        # A headless benchmark guest: no settings GUI, no persistenced GUI
        # dependencies; nvidia-smi and CUDA are what the workloads need.
        nvidiaSettings = false;
      };
      services.xserver.videoDrivers = lib.mkIf nvidia [ "nvidia" ];

      # Container path for workloads that arrive as OCI images (optional for
      # the bare-metal-style kvcache suite, harmless otherwise).
      hardware.nvidia-container-toolkit.enable = lib.mkIf nvidia (lib.mkDefault true);

      environment.systemPackages =
        (with pkgs; [ pciutils ])
        ++ lib.optionals nvidia (
          with pkgs;
          [
            cudaPackages.cudatoolkit
          ]
        )
        ++ lib.optionals (!nvidia) (
          with pkgs;
          [
            rocmPackages.rocminfo
            rocmPackages.rocm-smi
          ]
        );
    };
}
