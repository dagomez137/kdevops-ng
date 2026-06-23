<!-- SPDX-License-Identifier: copyleft-next-0.3.1 -->
# Linux Kernel Configuration Fragments

Modular kernel configuration fragments for building custom Linux kernels
targeting QEMU/KVM virtual machines. Feature fragments default to `=m`
for tristate configs. A `builtin/` subdirectory provides `=y` overrides
for boot scenarios without module loading.

Fragment naming and comment style follow the upstream kernel convention
(`kernel/configs/` in the Linux source tree). Fragments are grouped by
topic into topical subdirectories under `kernel/configs/`:

```
core/       base kernel, modules, init system, boot, firmware, hardening
arch/       per-architecture (x86_64 / arm64) knobs and extras
fs/         filesystems
storage/    block layer, device mapper, MD, NVMe, NVDIMM, CXL
virt/       virtio device drivers, KVM host
net/        iptables, nftables, Docker/Moby
security/   LSMs, IMA/EVM, crypto API and algorithms
mem/        memory-management knobs (DAMON, KSM, NUMA, zswap)
debug/      developer instrumentation and sanitizers
test/       in-tree test infrastructure
rust/       Rust language and module-versioning
perf/       performance monitoring
```

`builtin/` mirrors the same subdirectory layout: `builtin/fs/ext4.config`
is the `=y` override for `fs/ext4.config`.

## How to use it

Run `merge_config.sh` from the kernel source tree and pass the
fragments you want. `$C` is the path to this project's
`kernel/configs/` directory.

A minimal bootable x86_64 VM with ext4 root and virtio-net:

```sh
./scripts/kconfig/merge_config.sh -n -O ../build \
    ../build/.config \
    $C/core/64bit.config \
    $C/core/modules.config \
    $C/core/core.config \
    $C/core/systemd.config \
    $C/core/initrd.config \
    $C/arch/x86_64.config \
    $C/core/acpi-poweroff.config \
    $C/fs/ext4.config \
    $C/virt/virtio-net.config \
    $C/core/localversion.config
```

Append any other feature fragment to add it. To build a feature into
the kernel instead of as a module, add `-y` to the command line and
append the matching `builtin/` fragment. `-y` ("make builtin have
precedence over modules") stops a `=m` from demoting the `builtin/`
`=y` value, so fragment order does not matter:

```sh
./scripts/kconfig/merge_config.sh -y -n -O ../build \
    ../build/.config \
    ... \
    $C/fs/ext4.config \
    $C/virt/virtio-fs.config \
    $C/builtin/fs/ext4.config \
    $C/builtin/virt/virtio-fs.config
```

Each builtin fragment is self-contained: it sets the target symbol
and every tristate dependency to `=y` (for example
`builtin/virt/virtio-fs` pulls in `VIRTIO=y` and `VIRTIO_PCI=y`).
`-y` only protects a `=y` that a fragment already supplies; it does
not by itself promote a feature fragment's `=m` to `=y`, which is why
the matching `builtin/` fragment still has to be on the line.

See [docs/design-decisions.md](docs/design-decisions.md) for the
full design: why `=m` is the default, how the `builtin/` override
model works, the Kconfig exceptions that force specific symbols to
`=y`, and the list of configs this project deliberately omits.

## Everything built-in (no modules)

For a kernel that runs without a module loader at all, just drop
`core/modules.config` from the merge. With `CONFIG_MODULES=n` the
kernel has no module state, so `make olddefconfig` promotes every
`=m` to `=y` on its own; no `builtin/` fragments are needed and `-y`
has nothing to do:

```sh
./scripts/kconfig/merge_config.sh -n -O ../build \
    ../build/.config \
    $C/core/64bit.config \
    $C/core/core.config \
    $C/core/systemd.config \
    $C/core/initrd.config \
    $C/arch/x86_64.config \
    $C/core/acpi-poweroff.config \
    $C/fs/ext4.config \
    $C/virt/virtio-net.config \
    $C/core/localversion.config
```

After `make olddefconfig`, the final `.config` contains
`# CONFIG_MODULES is not set` and zero `=m` lines; every
enabled tristate is `=y`. This is the right shape for a
no-module-loader boot (custom init, initramfs-less boot, or a
recovery kernel where modprobe is not available).

## Verifying a merged .config

`scripts/verify_config.sh` checks that the values you requested in the
fragments actually appear in the final `.config`. It handles the
last-wins rule and prints a summary of `=y`/`=m` totals:

```sh
scripts/verify_config.sh ../build/.config \
    $C/core/64bit.config $C/core/modules.config $C/core/core.config \
    $C/core/systemd.config $C/core/initrd.config $C/arch/x86_64.config \
    $C/core/acpi-poweroff.config $C/fs/ext4.config \
    $C/virt/virtio-net.config $C/core/localversion.config
```

## License

This project is licensed under copyleft-next-0.3.1. See
[LICENSE](LICENSE) and [COPYING](COPYING) for details.
