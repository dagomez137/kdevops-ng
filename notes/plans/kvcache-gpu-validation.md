# kvcache: validation that needs a bigger machine

Status, 2026-10-10. Everything below is TODO and needs a peer with more memory
than hz-debian (61 GB) and, for the last item, an NVIDIA GPU. Running the CUDA
build there next to guests drove the host into a global OOM: nvcc's `cicc`
front end holds about 3.5 GB per process, and 16 of them plus a 16 GB guest
took the user manager, every guest and the monitoring stack down with it.
Cap the build (`--cores 4 --max-jobs 1`, about 15 GB peak) or give it a
machine of its own.

What is already verified on hz-debian: the CPU builds of LMCache v0.5.5, the
replayer and the kvcache closure; vLLM 0.28 loading LMCache as its connector;
the NVIDIA open modules built against a 7.3 kernel by `f/kernel/build_nvidia`;
the PCI passthrough units rendered from the vendored templates; and the
kvcache flow run live on a CPU guest (vanilla v7.3-rc6, NUMA preset) with
backend `none`: `gaia-burst` with one session on Qwen2.5-0.5B-Instruct
replayed 6 of 8 turns in 85 s, with vLLM's prefix cache answering 73344 of
105976 prompt tokens (69%), before the session outgrew the 32768-token context.
That run capped each turn at 8 tokens (`max_tokens`), gave vLLM half the
guest's memory (`gpu_memory_utilization` 0.5) and set `max_failure_rate` to
1.0, since the verdict then still counted the outgrown session as a failure.

## 1. vLLM's CUDA build

nixpkgs-unstable marks `python3Packages.vllm` broken with `cudaSupport`
(NixOS/nixpkgs#553566, 2026-08-17, still set at vLLM 0.29.0); the PR gives no
reason and the follow-up it names (#549327) merged without touching it. Its
recipe already pre-fetches tml-fa4 and the other CMake dependencies, so the
cause has to be found by building it:

    nix build --impure --keep-going --cores 4 --max-jobs 1 --expr '
      let f = builtins.getFlake (toString ./vendor/nixos-flake);
          p = import f.inputs.nixpkgs-unstable {
            system = "x86_64-linux";
            config = { allowUnfree = true; cudaSupport = true;
                       cudaCapabilities = [ "9.0" ];
                       problems.handlers.vllm.broken = "warn"; }; };
      in p.python313Packages.vllm'

Fix whatever fails in the vendored flake's `unstable` overlay (an override of
`python313Packages.vllm` in pkgsUnstable), clear the mark there, and send the
fix upstream. Until then the gpu profile with the kvcache suite does not
evaluate: the broken mark refuses it.

## 2. The CUDA closure

With vLLM building: imageless + gpu + kvcache at `cudaCapabilities = [ "9.0" ]`,
then LMCache's CUDA extension (`lmcache.cuda_ops`, built with nvcc) on top.
Expect hours; torch with CUDA is not in cache.nixos.org.

## 3. The NVIDIA open module and kernel matrix

Vanilla trees only, each configured with `defconfig` plus `CONFIG_DRM=m` (GCC
15), or through the kernel flow where noted:

| module release | v6.18 | v7.3-rc6 |
|---|---|---|
| 595.104.02 | builds | |
| 610.57.04 | builds | fails: `dmem_cgroup_register_region` |
| 615.71.09 (nixpkgs-unstable) | | fails: `dmem_cgroup_register_region` |
| 615.78.08 (shipped) | | builds, via the flow, preset and fragments |

The v7.3-rc6 failures come with `CONFIG_CGROUP_DMEM=y`, which both the
`imageless_defconfig` preset and the default fragment set enable; 615.78.08
is NVIDIA's first release that follows the changed signature.

Still to fill: v6.12, v7.0, v7.2, and 615.78.08 on each. Each kernel is a
full build, since NVIDIA's build refuses an empty `Module.symvers`. The recipe
turns `CONFIG_WERROR` off for every kernel because v6.12 fails to build itself
with GCC 15 under `-Werror`, and one recipe serves them all:

    make O=build defconfig
    scripts/config --file build/.config --module DRM --disable WERROR
    make O=build olddefconfig all
    make --directory=<open-gpu-kernel-modules at a tag> modules \
        SYSSRC=<kernel> SYSOUT=<kernel>/build CC=gcc CXX=g++ \
        IGNORE_PREEMPT_RT_PRESENCE=1

A guest booted from vanilla v7.3-rc6 built with the default fragment set
plus `mem/numa` and `virt/gpu-passthrough` never answered over vsock, while
the same tag built from the `imageless_defconfig` preset boots and serves. The
user journal records nothing on hz-debian, so the console was not available;
find out on a host whose journal works.

## 4. End to end on a GPU peer

On a peer with an NVIDIA GPU and the IOMMU on:

1. The manual VFIO setup in `docs/getting-started/requirements.rst` (PCI
   passthrough) for the GPU's IOMMU group, including the locked-memory drop-in
   and a restart of the user manager.
2. `f/qsu/bringup` with the closure's `gpu` profile and `kvcache` suite, the
   kernel's `nvidia_modules`, and the GPU in the Passthrough group.
3. In the guest: `nvidia-smi` sees the GPU, and `/dev/nvidia0` and
   `/dev/nvidia-uvm` exist (the gpu profile's udev rules).
4. `f/kvcache/run` with backends `none`, `cpu` and `disk` on the same presets,
   and the LMCache counters moving in the report for `cpu` and `disk`.
