# Testing emulated NVMe CMB/PMR in a qsu VM (SPDK + kernel)

How to exercise an emulated NVMe **Controller Memory Buffer (CMB)** and **Persistent
Memory Region (PMR)** inside a qsu imageless VM, with SPDK's userspace driver (the
primary path) and with the in-kernel nvme driver (the no-SPDK alternative). Verified
live on 2026-06-11 against `vm-019eb28e` (kernel 7.1.0-rc7, closure with the `devel`
profile, `iommu=intel-iommu`).

QEMU emits the CMB on PCI BAR 2 and the PMR on BAR 4/5; see the qsu NVMe knobs in
`f/qsu/boot` (and [qsu-execution-model.md](qsu-execution-model.md) for how the VM is
driven from a worker).

## The one fact everything follows from

SPDK is a **userspace NVMe driver**: `scripts/setup.sh` unbinds the controller from the
kernel `nvme` driver and rebinds it to `vfio-pci`, then SPDK drives it from userspace
over VFIO. So the guest needs three things the imageless build now provides
automatically: a **VFIO** stack, an active **vIOMMU**, and **hugepages**. The in-kernel
nvme driver is the opposite — it keeps the device and uses the CMB itself — so the two
methods are mutually exclusive on a given controller at a given time.

| | SPDK (userspace) | kernel nvme driver |
|---|---|---|
| who owns the controller | `vfio-pci` (SPDK) | `nvme` |
| CMB | `spdk_nvme_cmb_copy`, identify shows it | `/sys/class/nvme/nvmeN/cmb`, SQs in CMB |
| PMR | `spdk_nvme_pmr_persistence` | not used by Linux — poke the BAR directly |
| needs | VFIO + vIOMMU + hugepages | nothing extra |

## What is wired (and where)

All of this is built into the imageless product; you do not configure it per-run beyond
choosing the vIOMMU and the NVMe knobs.

| Piece | Location |
|---|---|
| `VFIO`, `VFIO_PCI` (=m), AMD/Intel/virtio IOMMU | `linux-config-fragments` (imageless preset + `core/vfio.config`) |
| vIOMMU `caching-mode=on` / `dma-remap=on` | `qemu-system-units` `vm.env.j2` (emitted whenever an intel/amd vIOMMU is set) |
| `spdk` + the `cmb_copy`/`pmr_persistence` examples | `nixos-flake` `overlays/spdk.nix` + `profiles/devel` |
| `vfio_iommu_type1 allow_unsafe_interrupts=1` | `nixos-flake` `profiles/devel` (`boot.extraModprobeConfig`) |

**Use `iommu=intel-iommu`.** QEMU's emulated **amd-iommu does not support guest VFIO** —
even with `dma-remap=on`, DPDK fails `failed to select IOMMU type`. `intel-iommu` with
`caching-mode=on` is the proven path, and the emulated vIOMMU is independent of the host
CPU, so it works on an AMD host.

## Quick start

Boot a VM with NVMe CMB+PMR and an Intel vIOMMU. A full build picks up any kernel/closure
changes; pass `nix_lock.update_lock=true` so a vendored-flake edit reaches the closure.

```sh
wmill flow run f/qsu/bringup -d '{
  "kernel_source": "build", "closure_source": "build", "qemu_source": "nixpkgs",
  "nix_lock": {"update_lock": true},
  "boot_vm": {"auto_vm_name": false, "vm_name": "vm-spdk"},
  "boot_qemu": {"iommu": "intel-iommu"},
  "boot_nvme": {"nvme_drive_count": 4, "customize_drives": true,
                "cmb_size_mb": "64", "pmr_size": "16777216", "pmr_share": true}
}'
```

Then `ssh vm-spdk`. To change the vIOMMU later, reconfigure in place (reuse the kernel
and closure, just re-render): set `kernel_source`/`closure_source` to `reuse`,
`reuse_from_vm` to the VM, and flip `boot_qemu.iommu`.

## SPDK

As root in the guest. `setup.sh` reserves hugepages and binds the NVMe controllers to
`vfio-pci`; `allow_unsafe_interrupts` is already set by the `devel` profile, so no manual
sysfs poke is needed.

```sh
SPDK=$(dirname $(dirname $(readlink -f $(command -v spdk_nvme_identify))))
HUGEMEM=1024 "$SPDK"/scripts/setup.sh         # bind -> vfio-pci, reserve hugepages
"$SPDK"/scripts/setup.sh status               # list the NVMe BDFs (they shift with the vIOMMU)
```

Pick a controller BDF from `status` (with `intel-iommu` they are `0000:00:04.0`..`07.0`).

**Identify** — proves SPDK drives the device over VFIO and reports CMB/PMR:

```sh
spdk_nvme_identify -r 'trtype:PCIe traddr:0000:00:04.0' | grep -iE 'Memory Buffer|Persistent Memory'
# Controller Memory Buffer Support
# Persistent Memory Region Support
```

**PMR persistence** (`-p` device, `-n` nsid, `-r`/`-w` LBAs, `-l` count; all mandatory):

```sh
spdk_nvme_pmr_persistence -p 0000:00:04.0 -n 1 -r 0 -l 1 -w 0
# attach_cb - attached 0000:00:04.0!
# PMR Data is Persistent across Controller Reset
```

**CMB copy** — copy a namespace between controllers using one controller's CMB as the
data buffer. Params are `<pci>-<ns>-<startLBA>-<nLBAs>`; `-c` is the controller whose CMB
to use:

```sh
spdk_nvme_cmb_copy -r 0000:00:04.0-1-0-16 -w 0000:00:05.0-1-0-16 -c 0000:00:04.0
# attached 0000:00:04.0! / attached 0000:00:05.0!  (exit 0)
```

When done, return the controllers to the kernel: `"$SPDK"/scripts/setup.sh reset`.

## Kernel-only (no SPDK)

With the controllers on the in-kernel `nvme` driver (the default, or after
`setup.sh reset`):

**CMB** — the kernel registers it as P2P memory and places I/O submission queues in it
(needs `CONFIG_PCI_P2PDMA=y`, which the imageless preset sets):

```sh
cat /sys/bus/pci/devices/0000:00:04.0/p2pmem/{size,published,available}
cat /sys/class/nvme/nvme0/cmb           # cmbsz bit0 (SQS) set => SQs in CMB
dd if=/dev/nvme0n1 of=/dev/null bs=1M count=8   # I/O exercises the CMB SQ path
```

`available` below `size` is the kernel having allocated SQs out of the CMB. If
`PCI_P2PDMA` were off, `dmesg` would show `failed to register the CMB` and `p2pmem/`
would be absent.

**PMR** — the Linux nvme driver does not use the PMR, so drive it as a userspace driver:
unbind nvme, enable `PMRCTL.EN` in BAR0, then read/write the PMR data in BAR4. MMIO needs
single aligned word accesses (`ctypes`, not `mmap`/`struct` bulk copy). The write reaches
the `share=on` backing file in the per-VM `StateDirectory`, proving persistence:

```python
# guest, root, after: echo 0000:00:05.0 > /sys/bus/pci/drivers/nvme/unbind
import ctypes, mmap, os
DEV="/sys/bus/pci/devices/0000:00:05.0"; MAGIC=b"PMRTEST"
m0=mmap.mmap(os.open(DEV+"/resource0",os.O_RDWR|os.O_SYNC),4096)        # BAR0 registers
base=ctypes.addressof(ctypes.c_char.from_buffer(m0))
ctypes.cast(base+0xE04,ctypes.POINTER(ctypes.c_uint32)).contents.value=1  # PMRCTL.EN
m4=mmap.mmap(os.open(DEV+"/resource4",os.O_RDWR|os.O_SYNC),4096)        # BAR4 PMR data
m4[0:len(MAGIC)]=MAGIC
```
```sh
# host: the guest write must appear in the backing file
grep -a PMRTEST ~/.local/state/qemu-system/<vm>/nvme-pmr-1.img
```

PMR `size` must be a power of two and at least one host page (`f/qsu/common` rejects
smaller); `00:05.0` ↔ drive index 1 ↔ `nvme-pmr-1.img`.

## Pitfalls

- **`failed to select IOMMU type`** — either the vIOMMU is `amd-iommu` (use `intel-iommu`),
  or `allow_unsafe_interrupts` is not set (the `devel` profile sets it; a non-`devel`
  closure needs `echo 1 > /sys/module/vfio_iommu_type1/parameters/allow_unsafe_interrupts`).
- **No spdk after editing the vendored nixos-flake** — the closure pins it by narHash, so
  rebuild with `nix_lock.update_lock=true`; and a **new** file (e.g. an overlay) must be
  `git add`ed in `vendor/nixos-flake` (a git flake sees only tracked files), not
  just copied.
- **nixpkgs SPDK lags upstream** — the pinned channel ships an older SPDK than the latest
  `vYY.MM` tag; the overlay only recovers the missing example binaries, it does not bump
  the version.

## References

- SPDK upstream: `~/src/spdk/spdk/doc/{getting_started,nvme}.md`.
- qsu NVMe knobs: `f/qsu/boot` NVMe group; CMB/PMR mechanics in the
  `qemu-system-units` `nvme.env.j2` macros and `docs/design-decisions.md`.
- Execution model: [qsu-execution-model.md](qsu-execution-model.md);
  closure build: [nix-build-flow.md](nix-build-flow.md).
