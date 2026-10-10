# Bare-metal GPU hosts: what the kvcache suite would need

Status, 2026-10-10: open question, nothing built. The kvcache suite runs on a
guest with an NVIDIA GPU passed through (VFIO): the guest boots a kernel the
kernel flow built, which carries the NVIDIA open modules, and its closure's
`gpu` profile ships the matching userspace. This note is about running the same
workload on a bare-metal host instead, where the GPU is the host's own and no
guest exists.

## What changes on bare metal

- **The kernel is the host's.** It is the distro's kernel (or one the operator
  installs), not one the kernel flow built and published. The NVIDIA modules
  must be built against that kernel's headers, and rebuilt whenever it
  changes: what DKMS does on a distro, and what NixOS's `hardware.nvidia` does
  against `boot.kernelPackages` on a NixOS host.
- **Installing modules needs root.** `modules_install` into `/lib/modules`, a
  `depmod`, and loading `nvidia`/`nvidia_uvm` all need privileges, and
  kdevops-ng never runs sudo. Whatever is chosen, the root part is a manual
  step documented in `docs/getting-started/requirements.rst`, like the VFIO
  setup; no step runs, wraps or prints it (CLAUDE.md, privilege boundary).
- **Userspace and kernel must be one release.** nvidia.ko refuses a userspace
  of another version. On a guest both come from the vendored flake
  (`nvidia-driver` and `nvidia-open-src`); on a distro host the kernel module
  and the userspace may come from different places (distro packages, NVIDIA's
  installer, Nix), which is where version skew creeps in.
- **No closure.** The suite's units and the inference stack live in a NixOS
  closure today. On a non-NixOS host they would have to run as user units from
  a Nix profile or `nix shell`, with `/run/opengl-driver/lib` (where CUDA looks
  for libcuda on NixOS) replaced by wherever the host's driver installs it.

## Candidate shapes

1. **NixOS host, plain `hardware.nvidia`.** The host's own configuration
   imports the gpu profile's userspace choices and lets NixOS build the
   modules for its kernel. The gpu profile currently refuses a NixOS-built
   kernel; this path would add that branch. Root is the NixOS rebuild itself.
2. **Distro host, modules from the distro, userspace from Nix.** Install the
   distro's NVIDIA driver (root, once), then run the inference stack from Nix
   with `nixGL`-style library paths pointing at the host driver. Version skew
   between the Nix CUDA runtime and the distro driver is the risk to measure.
3. **Distro host, modules built by a flow.** A step builds `nvidia-open-src`
   against `/lib/modules/$(uname -r)/build` in its worker sandbox, and the
   operator installs the result by hand from a documented procedure. Userspace
   from the same vendored flake keeps one release on both sides, at the cost of
   fighting the distro's own packages.

## Questions to settle

- Which hosts are in scope: NixOS only, or distro hosts too?
- Is a host-level run expected to share the guest flow's steps (discover,
  render_config, the `kvcache@` units), or be its own flow?
- Who owns the driver version on a shared host, and may a flow change it?
