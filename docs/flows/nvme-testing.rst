.. SPDX-License-Identifier: copyleft-next-0.3.1

:orphan:

=========================
Testing NVMe CMB and PMR
=========================

This is a how-to for exercising an emulated NVMe Controller Memory Buffer
(CMB) and Persistent Memory Region (PMR) inside a guest VM. It covers two
mutually exclusive paths: the `SPDK`_ userspace driver (the primary path) and
the in-kernel ``nvme`` driver (the no-SPDK alternative).

The guests are produced by the boot flow (:src:`f/qsu/boot`); see
:doc:`/flows/guests` for how to inspect a running guest. `QEMU`_ emits the CMB
on PCI BAR 2 and the PMR on BAR 4/5, controlled by the NVMe knobs in the boot
flow (``f/qsu/boot``).

SPDK versus the kernel nvme driver
==================================

SPDK is a userspace NVMe driver. Its ``scripts/setup.sh`` unbinds the
controller from the kernel ``nvme`` driver and rebinds it to ``vfio-pci``,
then SPDK drives it from userspace over VFIO. The guest therefore needs three
things the imageless build provides automatically: a VFIO stack, an active
vIOMMU, and hugepages. The in-kernel ``nvme`` driver is the opposite: it keeps
the device and uses the CMB itself. The two methods are mutually exclusive on a
given controller at a given time.

Owns the controller
    ``vfio-pci`` under SPDK; ``nvme`` for the kernel path.

CMB
    SPDK uses ``spdk_nvme_cmb_copy`` and identify reports it; the kernel
    exposes ``/sys/class/nvme/nvmeN/cmb`` and places submission queues in the
    CMB.

PMR
    SPDK uses ``spdk_nvme_pmr_persistence``; Linux does not use the PMR, so you
    poke the BAR directly.

Requirements
    SPDK needs VFIO, a vIOMMU, and hugepages; the kernel path needs nothing
    extra.

What is wired
=============

All of this is built into the imageless product; you do not configure it
per-run beyond choosing the vIOMMU and the NVMe knobs.

- ``VFIO``, ``VFIO_PCI`` (=m), and the AMD/Intel/virtio IOMMU drivers come from
  ``linux-config-fragments`` (the imageless preset plus ``core/vfio.config``).
- The vIOMMU ``caching-mode=on`` / ``dma-remap=on`` settings are emitted by the
  ``qemu-system-units`` ``vm.env.j2`` template whenever an intel or amd vIOMMU
  is set.
- ``spdk`` and the ``cmb_copy``/``pmr_persistence`` examples come from the
  ``nixos-flake`` ``overlays/spdk.nix`` and ``profiles/devel``.
- ``vfio_iommu_type1 allow_unsafe_interrupts=1`` is set by the ``nixos-flake``
  ``profiles/devel`` (``boot.extraModprobeConfig``).

Use ``iommu=intel-iommu``. QEMU's emulated amd-iommu does not support guest
VFIO: even with ``dma-remap=on``, DPDK fails with ``failed to select IOMMU
type``. The ``intel-iommu`` with ``caching-mode=on`` is the proven path, and
the emulated vIOMMU is independent of the host CPU, so it works on an AMD host.

Quick start
===========

Boot a guest with NVMe CMB+PMR and an Intel vIOMMU. A full build picks up any
kernel or closure changes; pass ``nix_lock.update_lock=true`` so a
vendored-flake edit reaches the closure.

.. code-block:: console

   $ wmill flow run f/qsu/bringup -d '{
   $   "kernel_source": "build", "closure_source": "build",
   $   "qemu_source": "nixpkgs",
   $   "nix_lock": {"update_lock": true},
   $   "boot_vm": {"auto_vm_name": false, "vm_name": "vm-spdk"},
   $   "boot_qemu": {"iommu": "intel-iommu"},
   $   "boot_nvme": {"nvme_drive_count": 4, "customize_drives": true,
   $                 "cmb_size_mb": "64", "pmr_size": "16777216",
   $                 "pmr_share": true}
   $ }'

Then ``ssh vm-spdk``. To change the vIOMMU later, reconfigure in place (reuse
the kernel and closure, just re-render): set ``kernel_source`` and
``closure_source`` to ``reuse``, ``reuse_from_vm`` to the guest, and flip
``boot_qemu.iommu``.

Using SPDK
==========

Run these as root in the guest. ``setup.sh`` reserves hugepages and binds the
NVMe controllers to ``vfio-pci``; ``allow_unsafe_interrupts`` is already set by
the ``devel`` profile, so no manual sysfs poke is needed.

.. code-block:: console

   $ SPDK=$(dirname $(dirname $(readlink -f $(command -v spdk_nvme_identify))))
   $ HUGEMEM=1024 "$SPDK"/scripts/setup.sh   # bind vfio-pci + hugepages
   $ "$SPDK"/scripts/setup.sh status         # list the NVMe BDFs

The BDFs shift with the vIOMMU. Pick a controller BDF from ``status`` (with
``intel-iommu`` they are ``0000:00:04.0`` through ``0000:00:07.0``).

Identify
--------

This proves SPDK drives the device over VFIO and reports CMB/PMR:

.. code-block:: console

   $ spdk_nvme_identify -r 'trtype:PCIe traddr:0000:00:04.0' \
       | grep -iE 'Memory Buffer|Persistent Memory'
   # Controller Memory Buffer Support
   # Persistent Memory Region Support

PMR persistence
---------------

The ``-p`` device, ``-n`` nsid, ``-r``/``-w`` LBAs, and ``-l`` count are all
mandatory:

.. code-block:: console

   $ spdk_nvme_pmr_persistence -p 0000:00:04.0 -n 1 -r 0 -l 1 -w 0
   # attach_cb - attached 0000:00:04.0!
   # PMR Data is Persistent across Controller Reset

CMB copy
--------

Copy a namespace between controllers using one controller's CMB as the data
buffer. The parameters are ``<pci>-<ns>-<startLBA>-<nLBAs>``; ``-c`` is the
controller whose CMB to use:

.. code-block:: console

   $ spdk_nvme_cmb_copy -r 0000:00:04.0-1-0-16 -w 0000:00:05.0-1-0-16 \
       -c 0000:00:04.0
   # attached 0000:00:04.0! / attached 0000:00:05.0!  (exit 0)

When done, return the controllers to the kernel with
``"$SPDK"/scripts/setup.sh reset``.

Kernel-only access (no SPDK)
============================

With the controllers on the in-kernel ``nvme`` driver (the default, or after
``setup.sh reset``), the CMB is reachable through P2PDMA and the PMR through
direct BAR access.

CMB via P2PDMA
--------------

The kernel registers the CMB as P2P memory and places I/O submission queues in
it. This needs ``CONFIG_PCI_P2PDMA=y``, which the imageless preset sets:

.. code-block:: console

   $ cat /sys/bus/pci/devices/0000:00:04.0/p2pmem/{size,published,available}
   $ cat /sys/class/nvme/nvme0/cmb       # cmbsz bit0 (SQS) set => SQs in CMB
   $ dd if=/dev/nvme0n1 of=/dev/null bs=1M count=8   # exercise the CMB SQ path

An ``available`` below ``size`` is the kernel having allocated SQs out of the
CMB. If ``PCI_P2PDMA`` were off, :cmd:`dmesg` would show ``failed to register
the CMB`` and ``p2pmem/`` would be absent.

PMR via MMIO
------------

The Linux ``nvme`` driver does not use the PMR, so drive it as a userspace
driver: unbind ``nvme``, enable ``PMRCTL.EN`` in BAR0, then read or write the
PMR data in BAR4. MMIO needs single aligned word accesses (``ctypes``, not
``mmap``/``struct`` bulk copy). The write reaches the ``share=on`` backing file
in the per-VM ``StateDirectory``, proving persistence:

.. code-block:: python

   # guest, root, after: echo 0000:00:05.0 > /sys/bus/pci/drivers/nvme/unbind
   import ctypes, mmap, os
   DEV = "/sys/bus/pci/devices/0000:00:05.0"
   MAGIC = b"PMRTEST"
   m0 = mmap.mmap(os.open(DEV + "/resource0", os.O_RDWR | os.O_SYNC), 4096)
   base = ctypes.addressof(ctypes.c_char.from_buffer(m0))  # BAR0 registers
   ctypes.cast(base + 0xE04, ctypes.POINTER(ctypes.c_uint32)).contents.value = 1
   m4 = mmap.mmap(os.open(DEV + "/resource4", os.O_RDWR | os.O_SYNC), 4096)
   m4[0:len(MAGIC)] = MAGIC  # BAR4 PMR data

Confirm on the host that the guest write appears in the backing file:

.. code-block:: console

   $ grep -a PMRTEST ~/.local/state/qemu-system/<vm>/nvme-pmr-1.img

The PMR ``size`` must be a power of two and at least one host page
(:src:`f/qsu/common` rejects anything smaller). Here ``00:05.0`` maps to drive
index 1, which maps to ``nvme-pmr-1.img``.

Pitfalls
========

``failed to select IOMMU type``
    Either the vIOMMU is ``amd-iommu`` (use ``intel-iommu`` instead), or
    ``allow_unsafe_interrupts`` is not set. The ``devel`` profile sets it; a
    non-``devel`` closure needs ``echo 1 >
    /sys/module/vfio_iommu_type1/parameters/allow_unsafe_interrupts``.

No ``spdk`` after editing the vendored ``nixos-flake``
    The closure pins it by narHash, so rebuild with
    ``nix_lock.update_lock=true``. A new file (for example an overlay) must
    also be ``git add``ed in ``vendor/nixos-flake``, because a git flake sees
    only tracked files; copying it in is not enough.

nixpkgs SPDK lags upstream
    The pinned channel ships an older SPDK than the latest ``vYY.MM`` tag. The
    overlay only recovers the missing example binaries; it does not bump the
    version.

References
==========

- `SPDK`_ upstream documentation for ``setup.sh`` and the NVMe examples.
- The NVMe knobs live in the boot flow's NVMe group (``f/qsu/boot``); the
  CMB/PMR mechanics are in the ``qemu-system-units`` ``nvme.env.j2`` macros.

.. _SPDK: https://spdk.io/

.. _QEMU: https://www.qemu.org/
